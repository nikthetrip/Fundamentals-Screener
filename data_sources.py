"""
data_sources.py — Tutte le chiamate di rete stanno qui.

- SEC EDGAR: mappa ticker->CIK, universo dei titoli quotati, companyfacts
  (utili e bilanci storici), submissions (SIC, filing recenti). Tutto gratuito
  e senza API key.
- Prezzi e dati di mercato: yfinance (storico max) con fallback Stooq.

NOTA SEC: EDGAR richiede un header User-Agent con un contatto reale, altrimenti
risponde 403. Impostalo nella variabile d'ambiente SEC_USER_AGENT.

NOTA SUI LIMITI: la SEC chiede di non superare le 10 richieste al secondo. Il
limitatore in questo modulo e' condiviso e sincronizzato, quindi vale anche
quando il builder lavora su piu' thread — senza, una build a 8 worker prende
403 sistematici dopo poche centinaia di ticker.
"""

from __future__ import annotations
import io
import json
import os
import threading
import time
import zipfile
from datetime import date, datetime
from pathlib import Path

import requests
import pandas as pd

_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_TICKER_EXCHANGE_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_BULK_COMPANYFACTS_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"

# Borse "vere" (esclude l'OTC, dove la qualita' di prezzi e depositi crolla).
MAJOR_EXCHANGES = ("NYSE", "Nasdaq", "NYSE American", "CBOE", "NYSEArca")


def _sec_headers() -> dict:
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua or "@" not in ua:
        raise RuntimeError(
            "Variabile SEC_USER_AGENT mancante o senza email.\n"
            "Impostala con un contatto reale prima di lanciare lo scraper:\n"
            '  export SEC_USER_AGENT="Lynch Research tua@email.com"   (macOS/Linux)\n'
            "  set SEC_USER_AGENT=Lynch Research tua@email.com         (Windows)"
        )
    return {"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}


# ---------------------------------------------------------------------------
# Sessione HTTP condivisa + limitatore di frequenza
# ---------------------------------------------------------------------------

_session_local = threading.local()


def _session() -> requests.Session:
    """
    Sessione requests per thread, con connessioni riusate.

    Riusare la connessione TCP invece di aprirne una nuova a ogni ticker toglie
    l'handshake TLS da ogni chiamata: sull'intero listino USA sono migliaia di
    handshake risparmiati.
    """
    s = getattr(_session_local, "s", None)
    if s is None:
        s = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=8)
        s.mount("https://", adapter)
        _session_local.s = s
    return s


class _RateLimiter:
    """Limitatore condiviso fra thread: al piu' `rate` chiamate al secondo."""

    def __init__(self, rate: float):
        self._min_interval = 1.0 / rate
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait_for = self._next_at - now
            if wait_for > 0:
                time.sleep(wait_for)
                now = time.monotonic()
            self._next_at = now + self._min_interval


# La SEC dichiara un tetto di 10 richieste/secondo. Restiamo sotto: 8.
_sec_limiter = _RateLimiter(float(os.environ.get("SEC_RATE_LIMIT", "8")))


def _sec_get(url: str, timeout: int = 30, retries: int = 3):
    """
    GET verso la SEC con limitatore di frequenza e ritentativi con attesa
    crescente sui codici transitori (429 troppe richieste, 5xx).

    Ritorna la Response (anche 404: e' un esito informativo, non un errore) o
    None se dopo i ritentativi non si e' ottenuta risposta.
    """
    headers = _sec_headers()
    delay = 1.0
    for attempt in range(retries):
        _sec_limiter.wait()
        try:
            r = _session().get(url, headers=headers, timeout=timeout)
        except requests.RequestException:
            if attempt == retries - 1:
                return None
            time.sleep(delay)
            delay *= 2
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            if attempt == retries - 1:
                return r
            time.sleep(delay)
            delay *= 2
            continue
        return r
    return None


# ---------------------------------------------------------------------------
# Mappa ticker -> CIK e universo dei titoli
# ---------------------------------------------------------------------------

_ticker_cik_cache: dict[str, str] | None = None
_cache_lock = threading.Lock()

# File di override ticker->CIK, opzionale, versionato nel repo.
# Serve per un caso reale e ricorrente: dopo una riorganizzazione societaria la
# mappa ufficiale della SEC punta il ticker alla NUOVA entita' (la holding), che
# non ha ancora storia depositata, mentre tutti i bilanci restano sotto il CIK
# della societa' operativa — che dalla mappa sparisce del tutto. Exxon e' il caso
# tipico: XOM risolve a 'ExxonMobil Holdings Corp' (CIK 2115436, zero fatti EPS)
# mentre 'Exxon Mobil Corporation' (CIK 34088) ha 224 fatti EPS e 63 trimestri.
# Senza override una delle maggiori societa' dell'indice sparisce dal dataset.
CIK_OVERRIDES_FILE = "cik_overrides.csv"


def _load_cik_overrides() -> dict[str, str]:
    """Legge cik_overrides.csv (ticker,cik,nota). Assente = nessun override."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        CIK_OVERRIDES_FILE)
    if not os.path.exists(path):
        return {}
    import csv
    out: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            t = (row.get("ticker") or "").strip().upper()
            c = (row.get("cik") or "").strip()
            if t and c.isdigit():
                out[t] = c.zfill(10)
    return out


def get_ticker_cik_map() -> dict[str, str]:
    """
    Scarica (una volta) la mappa ufficiale SEC ticker->CIK, applicando gli
    override locali. Ritorna {TICKER_UPPER: 'CIK a 10 cifre con zeri iniziali'}.
    """
    global _ticker_cik_cache
    if _ticker_cik_cache is not None:
        return _ticker_cik_cache
    with _cache_lock:
        if _ticker_cik_cache is not None:
            return _ticker_cik_cache
        r = _sec_get(_TICKER_MAP_URL)
        if r is None or r.status_code != 200:
            raise RuntimeError("Mappa ticker->CIK della SEC non raggiungibile")
        out = {}
        for row in r.json().values():
            out[str(row["ticker"]).upper()] = str(row["cik_str"]).zfill(10)
        out.update(_load_cik_overrides())   # l'override vince sulla mappa SEC
        _ticker_cik_cache = out
        return out


def resolve_cik(ticker: str) -> str | None:
    """CIK a 10 cifre per un ticker, provando le varianti '.'/'-' di classe."""
    cik_map = get_ticker_cik_map()
    for c in (ticker.upper(), ticker.upper().replace(".", "-"),
              ticker.upper().replace("-", ".")):
        if c in cik_map:
            return cik_map[c]
    return None


# Quinta lettera (o suffisso dopo il trattino) che identifica un titolo che NON
# e' azione ordinaria. Applicata solo quando esiste, sotto lo STESSO CIK, un
# ticker piu' corto di cui questo e' l'estensione: e' quella coincidenza a
# rendere il giudizio sicuro.
#   W warrant · R diritto · U unit · N, D, P azioni privilegiate e obbligazioni
# La lettera L resta fuori di proposito: e' quella di GOOGL, che e' azione
# ordinaria a tutti gli effetti e non va confusa con un derivato di GOOG.
_NON_COMMON_SUFFIXES = frozenset("WRUNDP")


def is_secondary_security(ticker: str, cik_tickers: set[str]) -> bool:
    """
    True se il ticker e' un titolo derivato o privilegiato dello stesso emittente.

    PERCHE' CONTA. La SEC elenca sotto lo stesso CIK le azioni ordinarie, le
    privilegiate, i warrant e le obbligazioni convertibili. Il loro PREZZO e'
    quello del titolo derivato, ma gli UTILI che gli assoceremmo sono quelli
    della societa': il rapporto fra i due non e' un P/E, e' un numero senza
    significato. Arbor Realty preferred serie E risultava a P/E 7,4 accanto
    all'ordinaria — e sarebbe finita in cima allo screener come "occasione".
    """
    if "-" in ticker:
        suffix = ticker.split("-", 1)[1]
        # NYSE: BRK-B e' una classe di ordinarie, ABR-PE una privilegiata
        return bool(suffix) and suffix[0] == "P"
    if len(ticker) >= 5 and ticker[-1] in _NON_COMMON_SUFFIXES:
        return ticker[:-1] in cik_tickers
    return False


def fetch_us_universe(exchanges: tuple[str, ...] = MAJOR_EXCHANGES,
                      include_secondary: bool = False) -> list[dict]:
    """
    Tutti i titoli quotati sulle borse USA principali, dalla SEC.

    Sorgente: company_tickers_exchange.json, l'elenco ufficiale dei filer con
    la borsa di quotazione — circa 10.400 righe, di cui ~7.700 fuori dall'OTC.
    E' l'unica lista gratuita, completa e allineata ai CIK che usiamo per i
    bilanci: qualunque altra (Wikipedia, CSV di terzi) copre solo un indice.

    Di default esclude privilegiate, warrant, diritti e unit (vedi
    is_secondary_security): sono quotati ma non sono l'azione della societa'.

    Ritorna [{ticker, cik, name, exchange}, ...] ordinato per ticker.
    Le classi multiple (BRK.B) sono normalizzate con il trattino, come yfinance.
    """
    r = _sec_get(_TICKER_EXCHANGE_URL, timeout=60)
    if r is None or r.status_code != 200:
        raise RuntimeError("Elenco SEC dei titoli quotati non raggiungibile")
    payload = r.json()
    fields = [f.lower() for f in payload.get("fields", [])]
    idx = {name: fields.index(name) for name in ("cik", "name", "ticker", "exchange")
           if name in fields}
    wanted = {e.lower() for e in exchanges}
    out: dict[str, dict] = {}
    by_cik: dict[str, set[str]] = {}
    for row in payload.get("data", []):
        exch = row[idx["exchange"]] if idx.get("exchange") is not None else None
        if not exch or str(exch).lower() not in wanted:
            continue
        ticker = str(row[idx["ticker"]]).upper().strip().replace(".", "-")
        if not ticker or ticker == "NONE":
            continue
        cik = str(row[idx["cik"]]).zfill(10)
        by_cik.setdefault(cik, set()).add(ticker)
        out.setdefault(ticker, {"ticker": ticker, "cik": cik,
                                "name": row[idx["name"]], "exchange": exch})
    if not include_secondary:
        out = {t: row for t, row in out.items()
               if not is_secondary_security(t, by_cik.get(row["cik"], set()))}
    return sorted(out.values(), key=lambda d: d["ticker"])


# ---------------------------------------------------------------------------
# companyfacts: da API oppure dall'archivio bulk
# ---------------------------------------------------------------------------

class CompanyFactsSource:
    """
    Sorgente dei companyfacts EDGAR, con due modalita' intercambiabili.

    'api'  — una richiesta HTTP per societa'. Va bene fino a qualche centinaio
             di ticker.
    'bulk' — un unico archivio zip (~1,5 GB) che contiene i companyfacts di
             TUTTI i filer, letto poi in locale societa' per societa'. Sopra il
             migliaio di ticker e' l'unica strada sensata: scaricare 7.000
             volte un JSON da qualche megabyte significa decine di gigabyte di
             traffico e ore di attesa, mentre lo zip si scarica una volta sola.
             zipfile legge il singolo membro senza estrarre l'intero archivio,
             quindi non serve spazio disco oltre allo zip stesso.
    """

    def __init__(self, mode: str = "api", cache_dir: Path | None = None):
        self.mode = mode
        self.cache_dir = cache_dir or Path(".cache")
        self._zip: zipfile.ZipFile | None = None
        self._zip_names: set[str] | None = None
        self._lock = threading.Lock()

    # -- bulk ---------------------------------------------------------------
    def _ensure_bulk(self) -> zipfile.ZipFile:
        with self._lock:
            if self._zip is not None:
                return self._zip
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path = self.cache_dir / "companyfacts.zip"
            if not path.exists() or path.stat().st_size < 100_000_000:
                _sec_limiter.wait()
                with _session().get(_BULK_COMPANYFACTS_URL, headers=_sec_headers(),
                                    stream=True, timeout=1800) as r:
                    r.raise_for_status()
                    tmp = path.with_suffix(".part")
                    with open(tmp, "wb") as fh:
                        for chunk in r.iter_content(chunk_size=1 << 22):
                            fh.write(chunk)
                    tmp.replace(path)
            self._zip = zipfile.ZipFile(path)
            self._zip_names = set(self._zip.namelist())
            return self._zip

    def get(self, ticker: str) -> dict | None:
        """companyfacts per ticker, o None se il ticker non e' risolvibile."""
        cik = resolve_cik(ticker)
        if cik is None:
            return None
        if self.mode == "bulk":
            zf = self._ensure_bulk()
            name = f"CIK{cik}.json"
            if self._zip_names is not None and name not in self._zip_names:
                return None
            try:
                with self._lock:
                    raw = zf.read(name)
                return json.loads(raw)
            except (KeyError, zipfile.BadZipFile, json.JSONDecodeError):
                return None
        return _fetch_companyfacts_api(cik)


def _fetch_companyfacts_api(cik: str) -> dict | None:
    r = _sec_get(_COMPANYFACTS_URL.format(cik=cik), timeout=60)
    if r is None or r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def fetch_companyfacts(ticker: str, sleep: float = 0.0) -> dict | None:
    """
    Scarica il JSON companyfacts EDGAR per un ticker (modalita' API).
    Ritorna None se il ticker non e' mappabile o non esiste su EDGAR.

    Il parametro `sleep` e' mantenuto per compatibilita' ma non serve piu':
    la frequenza e' governata dal limitatore condiviso in _sec_get.
    """
    cik = resolve_cik(ticker)
    if cik is None:
        return None
    return _fetch_companyfacts_api(cik)


# ---------------------------------------------------------------------------
# submissions: anagrafica (SIC) + filing recenti, con UNA sola richiesta
# ---------------------------------------------------------------------------

def fetch_submissions(ticker: str) -> dict | None:
    """
    Indice submissions EDGAR: anagrafica della societa' e depositi recenti.

    Una sola richiesta serve sia i link ai 10-K/10-Q sia il codice SIC, da cui
    ricaviamo settore e industria per i titoli che yfinance non copre.
    """
    cik = resolve_cik(ticker)
    if cik is None:
        return None
    r = _sec_get(_SUBMISSIONS_URL.format(cik=cik), timeout=30)
    if r is None or r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def parse_company_meta(subs: dict | None) -> dict:
    """Anagrafica utile dal submissions: nome, SIC, descrizione SIC, borsa."""
    if not subs:
        return {}
    exch = subs.get("exchanges") or []
    return {
        "company_sec": subs.get("name"),
        "sic": subs.get("sic"),
        "sic_description": subs.get("sicDescription"),
        "exchange": exch[0] if exch else None,
        "fiscal_year_end": subs.get("fiscalYearEnd"),
        "entity_type": subs.get("entityType"),
    }


def parse_recent_filings(subs: dict | None, cik: str | None, n: int = 5,
                         forms: tuple[str, ...] = ("10-K", "10-Q")) -> list[dict]:
    """Ultimi N filing dai dati submissions gia' scaricati."""
    if not subs:
        return []
    recent = subs.get("filings", {}).get("recent", {})
    forms_list = recent.get("form", [])
    accns = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    docs = recent.get("primaryDocument", [])
    cik = cik or subs.get("cik") or ""
    try:
        cik_int = str(int(str(cik)))   # l'URL Archives non vuole gli zeri iniziali
    except (TypeError, ValueError):
        return []

    out: list[dict] = []
    for i, frm in enumerate(forms_list):
        if frm not in forms or i >= len(accns) or i >= len(docs):
            continue
        out.append({
            "form": frm,
            "filing_date": filing_dates[i] if i < len(filing_dates) else None,
            "period_date": report_dates[i] if i < len(report_dates) else None,
            "url": (f"https://www.sec.gov/Archives/edgar/data/{cik_int}/"
                    f"{accns[i].replace('-', '')}/{docs[i]}"),
        })
        if len(out) >= n:
            break
    return out


def fetch_recent_filings(ticker: str, n: int = 5,
                         forms: tuple[str, ...] = ("10-K", "10-Q")) -> list[dict]:
    """Ultimi N filing (10-K/10-Q) per un ticker. Lista vuota se non risolvibile."""
    return parse_recent_filings(fetch_submissions(ticker), resolve_cik(ticker),
                                n=n, forms=forms)


# ---------------------------------------------------------------------------
# yfinance: frequenza e reazione al blocco per troppe richieste
#
# Yahoo non pubblica un limite ma lo applica: dopo qualche migliaio di chiamate
# ravvicinate risponde YFRateLimitError a TUTTO, e da quel momento ogni ticker
# risulta "senza prezzi". In una build sull'intero listino il danno non e' un
# errore visibile, e' un dataset che si interrompe a meta' senza dirlo — nel
# test su S&P 500 sono state 34 societa' di fila, fra cui Verizon e Vertex.
#
# Due presidi: un limitatore di frequenza in ingresso e una pausa condivisa fra
# tutti i thread quando il blocco scatta comunque.
# ---------------------------------------------------------------------------

_yf_limiter = _RateLimiter(float(os.environ.get("YF_RATE_LIMIT", "3")))


class _Cooldown:
    """Pausa condivisa: quando un thread viene bloccato, si fermano tutti."""

    def __init__(self):
        self._lock = threading.Lock()
        self._until = 0.0
        self._level = 0

    def wait(self) -> None:
        while True:
            with self._lock:
                remaining = self._until - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 5.0))

    def trip(self, base: float = 20.0) -> float:
        """Blocco rilevato: allunga la pausa. Ritorna i secondi di attesa."""
        with self._lock:
            self._level = min(self._level + 1, 6)
            wait_for = base * self._level
            self._until = max(self._until, time.monotonic() + wait_for)
            return wait_for

    def relax(self) -> None:
        """Chiamata andata a buon fine fuori pausa: allenta gradualmente."""
        with self._lock:
            if self._level and time.monotonic() > self._until:
                self._level -= 1


_yf_cooldown = _Cooldown()


def _is_rate_limited(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return ("ratelimit" in name or "too many requests" in text
            or "429" in text or "rate limited" in text)


def yf_call(fn, retries: int = 4, default=None):
    """
    Esegue una chiamata yfinance rispettando frequenza e pause.

    Sul blocco per troppe richieste NON solleva: mette in pausa tutti i thread e
    ritenta. Solleva invece qualunque altro errore, che va visto.
    """
    for attempt in range(retries):
        _yf_cooldown.wait()
        _yf_limiter.wait()
        try:
            out = fn()
            _yf_cooldown.relax()
            return out
        except Exception as exc:                       # noqa: BLE001
            if not _is_rate_limited(exc):
                raise
            if attempt == retries - 1:
                return default
            _yf_cooldown.trip()
    return default


# ---------------------------------------------------------------------------
# Prezzi
# ---------------------------------------------------------------------------

def fetch_price_history(ticker: str) -> tuple[list[tuple[date, float]],
                                              list[tuple[date, float]], str]:
    """
    Prezzi storici E split in UNA sola chiamata a yfinance.

    Chiedendo `actions=True` la stessa risposta porta anche la colonna degli
    split, che serve a rettificare gli EPS depositati. Erano due richieste per
    ticker: su settemila societa' fanno settemila richieste risparmiate, cioe'
    un terzo in meno di occasioni di essere bloccati.

    Ritorna (prezzi, split, fonte). Su fallimento ripiega su Stooq, che pero'
    non conosce gli split: in quel caso la lista degli split e' vuota.
    """
    import yfinance as yf

    def _download():
        return yf.Ticker(ticker).history(period="max", auto_adjust=True, actions=True)

    try:
        hist = yf_call(_download)
    except Exception:
        hist = None

    if hist is not None and not hist.empty and "Close" in hist.columns:
        prices = [(idx.date(), float(v))
                  for idx, v in hist["Close"].dropna().items()]
        splits: list[tuple[date, float]] = []
        if "Stock Splits" in hist.columns:
            for idx, ratio in hist["Stock Splits"].items():
                try:
                    if ratio and float(ratio) > 0:
                        splits.append((idx.date(), float(ratio)))
                except (TypeError, ValueError, AttributeError):
                    continue
        if prices:
            return prices, sorted(splits), "yfinance"

    try:
        rows = fetch_prices_stooq(ticker)
        if rows:
            return rows, [], "stooq"
    except Exception:
        pass
    return [], [], "none"


def fetch_prices_yf(ticker: str) -> list[tuple[date, float]]:
    """Prezzi storici (close) via yfinance, periodo massimo."""
    return fetch_price_history(ticker)[0]


def fetch_prices_stooq(ticker: str) -> list[tuple[date, float]]:
    """
    Fallback: prezzi da Stooq (CSV pubblico). Ticker USA -> suffisso '.us'.
    """
    sym = ticker.lower().replace(".", "-")
    url = f"https://stooq.com/q/d/l/?s={sym}.us&i=d"
    r = _session().get(url, timeout=30)
    if r.status_code != 200 or not r.text or r.text.startswith("<"):
        return []
    df = pd.read_csv(io.StringIO(r.text))
    if df.empty or "Close" not in df.columns:
        return []
    df["Date"] = pd.to_datetime(df["Date"]).dt.date
    return list(zip(df["Date"], df["Close"].astype(float)))


def fetch_prices(ticker: str) -> tuple[list[tuple[date, float]], str]:
    """Prova yfinance, poi Stooq. Ritorna (righe, fonte)."""
    prices, _, src = fetch_price_history(ticker)
    return prices, src


def fetch_splits(ticker: str) -> list[tuple[date, float]]:
    """
    Storico stock split da yfinance: [(data, rapporto), ...].
    Es. AMZN -> [(2022-06-06, 20.0)], NVDA -> [(2024-06-10, 10.0), ...].
    Serve a rettificare gli EPS storici di EDGAR sulla base azionaria attuale.
    """
    import yfinance as yf

    s = yf_call(lambda: yf.Ticker(ticker).splits)
    if s is None or len(s) == 0:
        return []
    out = []
    for idx, ratio in s.items():
        try:
            d = idx.date()
        except AttributeError:
            continue
        if ratio and float(ratio) > 0:
            out.append((d, float(ratio)))
    return sorted(out)


def fetch_shares_outstanding(ticker: str) -> float | None:
    """
    Numero di azioni in circolazione (valore corrente) da yfinance.

    Serve SOLO come denominatore quando l'EPS non e' ricavabile da EDGAR perche'
    la societa' lo tagga per classe di azioni. E' un dato puntuale di oggi, non
    una media ponderata storica: l'EPS che se ne ricava e' una stima e va
    etichettata come tale.
    """
    info = fetch_info(ticker)
    for key in ("impliedSharesOutstanding", "sharesOutstanding"):
        val = info.get(key)
        if val and float(val) > 0:
            return float(val)
    return None


def fetch_info(ticker: str) -> dict:
    """Scheda anagrafica yfinance, con limitatore e pause. {} se non arriva."""
    import yfinance as yf

    try:
        return yf_call(lambda: yf.Ticker(ticker).info, default={}) or {}
    except Exception:
        return {}


def _finite(value) -> float | None:
    """
    Un campo del provider come numero finito, oppure None.

    NON e' pedanteria difensiva: yfinance restituisce la STRINGA 'Infinity' nel
    campo trailingPE quando l'utile per azione e' zero. Il valore attraversava
    tutto il modulo travestito da numero e faceva esplodere il primo confronto
    (`pe_provider > 0` -> TypeError fra str e int), perdendo l'intero ticker con
    un messaggio che non diceva nulla — Bill.com e' sparita cosi'. I campi di un
    provider vanno normalizzati sul CONFINE, una volta, non a ogni uso.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):   # NaN oppure infinito
        return None
    return f


def _to_iso_date(value) -> str | None:
    """Converte un timestamp epoch o una data yfinance in 'YYYY-MM-DD'."""
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)) and value > 0:
            return datetime.utcfromtimestamp(float(value)).date().isoformat()
        if isinstance(value, str) and len(value) >= 10:
            return value[:10]
        if hasattr(value, "isoformat"):
            return value.isoformat()[:10]
    except (ValueError, OverflowError, OSError):
        return None
    return None


def fetch_current_snapshot(ticker: str) -> dict:
    """
    Dati "oggi" via yfinance (prezzo, settore, dividendo, market cap, P/E
    prospettico, prossima trimestrale).

    Best-effort: i campi mancanti restano None. Tutto cio' che riguarda i
    BILANCI viene invece da EDGAR: qui prendiamo solo cio' che i depositi non
    contengono — il prezzo di mercato, la classificazione settoriale e le
    aspettative degli analisti.
    """
    info = fetch_info(ticker)
    price = _finite(info.get("currentPrice")) or _finite(info.get("regularMarketPrice"))

    # DIVIDENDO: il campo dividendYield di yfinance e' incoerente (a volte frazione,
    # a volte percentuale). Lo calcoliamo in modo deterministico dal dividendo annuo
    # per azione (dividendRate, in $) diviso il prezzo. Fonte singola e verificabile.
    rate = _finite(info.get("dividendRate"))  # $ annui per azione
    div_yield = (rate / price * 100.0) if (rate and price) else None

    shares = None
    for key in ("impliedSharesOutstanding", "sharesOutstanding"):
        val = _finite(info.get(key))
        if val and val > 0:
            shares = val
            break

    market_cap = _finite(info.get("marketCap"))
    if not market_cap and price and shares:
        market_cap = price * shares       # ricostruita quando il campo manca

    # P/E prospettico: e' l'unico dato di questa scheda che guarda avanti, e per
    # costruzione viene dal consenso degli analisti, non da un bilancio.
    forward_pe = _finite(info.get("forwardPE"))
    forward_eps = _finite(info.get("forwardEps"))
    if not forward_pe and forward_eps and price and forward_eps > 0:
        forward_pe = price / forward_eps

    next_earnings = _to_iso_date(info.get("earningsTimestampStart")
                                 or info.get("earningsTimestamp"))

    return {
        "company": info.get("longName") or info.get("shortName") or ticker,
        "sector": info.get("sector") or None,
        "industry": info.get("industry") or None,
        # Ogni campo numerico passa da _finite: il provider consegna stringhe e
        # infiniti dove il resto del codice si aspetta numeri.
        "pe_ratio": _finite(info.get("trailingPE")),
        "forward_pe": forward_pe,
        "forward_eps": forward_eps,
        "current_price": price,
        # trailingEps di yfinance: EPS TTM diluito gia' rettificato per gli split.
        # Lo usiamo come EPS "di oggi" affidabile e come controllo incrociato su EDGAR.
        "trailing_eps": _finite(info.get("trailingEps")),
        "price_to_book": _finite(info.get("priceToBook")),
        "book_value": _finite(info.get("bookValue")),
        "dividend_yield": div_yield,
        "dividend_rate": rate,
        "market_cap": market_cap,
        "shares_outstanding": shares,
        "next_earnings_date": next_earnings,
        "beta": _finite(info.get("beta")),
        "currency": info.get("currency"),
        "quote_type": info.get("quoteType"),
    }
