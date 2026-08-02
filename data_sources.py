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


def sec_user_agent() -> str:
    """
    Il contatto da dichiarare alla SEC, dall'ambiente o dal file locale.

    `run.sh` legge gia' `.sec_user_agent` e lo esporta, ma l'applicazione non
    si avvia sempre da li': lanciando `streamlit run app.py` a mano — o da una
    configurazione dell'editor — la variabile non c'era, e le funzioni che
    parlano con la SEC restavano spente senza che il file, presente sul disco a
    due passi, venisse mai guardato. Il ripiego sta qui e non in `run.sh`
    perche' questo e' il punto da cui passano TUTTE le chiamate alla SEC.
    """
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if ua:
        return ua
    try:
        path = Path(__file__).resolve().parent / ".sec_user_agent"
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return ""


def _sec_headers() -> dict:
    ua = sec_user_agent()
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
                                              list[tuple[date, float]],
                                              list[tuple[date, float]], str]:
    """
    Prezzi storici, split E dividendi in UNA sola chiamata a yfinance.

    Chiedendo `actions=True` la stessa risposta porta anche le colonne degli
    split — che servono a rettificare gli EPS depositati — e dei dividendi, da
    cui si ricava il rendimento senza credere al campo `dividendRate` del
    provider (vedi `dividend_profile`). Erano tre richieste per ticker: su
    settemila societa' fanno quattordicimila richieste risparmiate, cioe'
    altrettante occasioni in meno di essere bloccati.

    Ritorna (prezzi, split, dividendi, fonte). Su fallimento ripiega su Stooq,
    che pero' non conosce ne' split ne' dividendi: in quel caso le due liste
    sono vuote.
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
        dividends: list[tuple[date, float]] = []
        if "Dividends" in hist.columns:
            for idx, amount in hist["Dividends"].items():
                try:
                    if amount and float(amount) > 0:
                        dividends.append((idx.date(), float(amount)))
                except (TypeError, ValueError, AttributeError):
                    continue
        if prices:
            return prices, sorted(splits), sorted(dividends), "yfinance"

    try:
        rows = fetch_prices_stooq(ticker)
        if rows:
            return rows, [], [], "stooq"
    except Exception:
        pass
    return [], [], [], "none"


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
    prices, _, _, src = fetch_price_history(ticker)
    return prices, src


# ---------------------------------------------------------------------------
# Dividendi
# ---------------------------------------------------------------------------

# Cadenze di pagamento ammesse, in numero di stacchi all'anno. Non esiste una
# societa' che paghi undici volte l'anno: la cadenza si sceglie fra queste
# quattro, quella la cui distanza teorica somiglia di piu' a quella osservata.
DIVIDEND_FREQUENCIES = (1, 2, 4, 12)

# Due cedole si considerano LO STESSO regime se differiscono meno di questo:
# un aumento del 10-15% e' l'aumento annuale tipico, non un cambio di natura.
SAME_REGIME_TOLERANCE = 0.25

# Un pagamento oltre DUE VOLTE E MEZZA il livello ordinario e' uno straordinario,
# non un aumento: nessuna societa' alza la cedola di due volte e mezzo in un
# colpo, mentre il variabile annuale di CME (7,45 $ contro 1,30 $) o quello di
# Progressive (13,60 $ contro 0,10 $) stanno ampiamente oltre. Se invece
# l'aumento e' vero, alla cedola successiva si ripete e diventa il regime da se'.
SPECIAL_DIVIDEND_MULTIPLE = 2.5

# UN DIVIDENDO ANNUALE VA PROVATO MEGLIO DI UNO TRIMESTRALE. Con stacchi
# lontani un anno, due soli pagamenti sono indistinguibili da due elargizioni
# una tantum: TransDigm ne ha fatte quattro in otto anni (22, 30, 18, 35, 75,
# 90 $) e chiamarle "cedola annuale" dava un rendimento del 7,2% a una societa'
# che dividendi ordinari non ne paga; Rocket ha distribuito tre volte in cinque
# anni. Per la cadenza annuale servono quindi tre pagamenti E intervalli
# regolari fra loro: se il piu' lungo supera il piu' corto di oltre la meta',
# non e' una cadenza, sono occasioni.
ANNUAL_MIN_PAYMENTS = 3
ANNUAL_MAX_GAP_RATIO = 1.5
# ...oppure due soli stacchi, ma di importo praticamente identico: e' la firma
# di una politica di dividendo annuale (Vornado, 0,74 $ ogni dicembre), non di
# due elargizioni decise volta per volta.
ANNUAL_PAIR_TOLERANCE = 0.05

# Entro quanti giorni dall'ultimo stacco la serie si considera ancora in corso.
CURRENT_SERIES_DAYS = 120


# Quanti intervalli di pagamento si aspetta prima di dichiarare INTERROTTO un
# dividendo. Una societa' trimestrale che non stacca da otto mesi (91 × 2 + 60)
# ha sospeso, non e' in ritardo — e un rendimento calcolato sull'ultima cedola
# di una societa' che non paga piu' e' semplicemente falso.
DIVIDEND_STALE_INTERVALS = 2.0
DIVIDEND_STALE_GRACE_DAYS = 60


def dividend_profile(dividends: list[tuple[date, float]],
                     price: float | None,
                     asof: date | None = None) -> dict:
    """
    Il rendimento da dividendo ricostruito dai PAGAMENTI EFFETTIVI.

    PERCHE' NON SI USA `dividendRate` DEL PROVIDER. Quel campo dovrebbe essere
    il dividendo annuo corrente, e per la maggior parte delle societa' lo e'.
    Ma cambia significato proprio nei casi in cui la risposta conta:

      - dopo un TAGLIO diventa la somma degli ultimi quattro stacchi, non il
        nuovo passo. Whirlpool ha dimezzato la cedola da 1,75 a 0,90 $:
        `dividendRate` restava 4,45 $ e il rendimento usciva 11,7% invece di
        9,5%. Conagra, tagliata da 0,35 a 0,175 $, usciva all'8,2% contro un
        4,7% reale — quasi il doppio, ed e' esattamente il tipo di titolo che
        un filtro "alto dividendo" pesca per primo.
      - con i dividendi STRAORDINARI ci finisce dentro anche la cedola una
        tantum. Progressive paga 0,10 $ a trimestre piu' un variabile annuale
        che nel 2025 e' stato 13,60 $: il campo dava 6,5% come se quel 13,60 $
        tornasse ogni anno.

    Qui il conto e' esplicito: si isolano le cedole ORDINARIE, si misura ogni
    quanto arrivano, si annualizza l'ultima. Il totale davvero incassato negli
    ultimi dodici mesi — straordinari inclusi — viene restituito a parte,
    perche' e' un'altra domanda e merita un'altra riga.

    LE REGOLE, e ognuna nasce da un caso vero trovato nel dataset.

    1. LE ULTIME DUE CEDOLE UGUALI SONO IL REGIME. Se gli ultimi due stacchi si
       somigliano (entro il 25%), sono loro a definire importo e cadenza, e non
       serve altro. E' la via normale, e sistema da sola il caso di STAG
       Industrial, passata da mensile 0,124 $ a trimestrale 0,388 $: guardando
       la storia intera il cambio di cadenza sembrava una serie di cedole
       straordinarie e la societa' risultava aver smesso di pagare.
    2. QUANDO LE ULTIME DUE NON COINCIDONO, decide la direzione. Verso il basso
       e' un TAGLIO e vale subito: Conagra e' passata da 0,35 a 0,175 $ e il
       rendimento deve dimezzarsi lo stesso giorno. Verso l'alto oltre due volte
       e mezza e' uno STRAORDINARIO e si ignora: Nvidia stacca 0,01 $ a
       trimestre, e un pagamento da 0,25 $ non e' un aumento del 2.400%. Un
       rialzo piu' contenuto e' un aumento vero e si accetta.
    3. LA CADENZA SI MISURA SULLE SOLE CEDOLE ORDINARIE. American Financial
       Group paga 0,88 $ a trimestre piu' straordinari infilati fra un trimestre
       e l'altro: contando anche quelli la distanza mediana fra pagamenti
       scendeva a due mesi, il codice deduceva cadenza MENSILE e annualizzava
       0,88 × 12 = 10,56 $. Rendimento 7,5% invece di 2,5%.
    4. UN SOLO PAGAMENTO NON FA UNA CEDOLA, e nemmeno due lontanissimi fra loro.
       Arch Capital ha distribuito 5,00 $ una volta sola: annualizzarlo dava un
       rendimento del 19,8% a una societa' che non paga dividendi. Rocket ha
       pagato tre volte in cinque anni: non e' un dividendo annuale.
    5. UN DIVIDENDO VECCHIO E' UN DIVIDENDO SOSPESO. Adobe ha smesso nel 2005,
       American Airlines nel febbraio 2020: le loro cedole sono ancora nello
       storico, e annualizzarle dava 0,01% e 3,3% a due societa' che non
       distribuiscono nulla da anni.

    Ritorna: rate (annuo ordinario per azione), yield_pct, ttm (incassato negli
    ultimi 12 mesi), ttm_yield_pct, frequency, last_payment, has_special,
    discontinued.
    """
    out = {"rate": None, "yield_pct": None, "ttm": None, "ttm_yield_pct": None,
           "frequency": None, "last_payment": None, "has_special": False,
           "discontinued": False}
    rows = sorted((d, float(v)) for d, v in (dividends or []) if v and v > 0)
    if not rows:
        return out

    asof = asof or date.today()
    # Finestra APERTA a 365 giorni: con "<=" la cedola staccata esattamente un
    # anno prima rientrerebbe insieme a quella di quest'anno, e un pagatore
    # annuale risulterebbe aver distribuito il doppio.
    ttm = sum(v for d, v in rows if 0 <= (asof - d).days < 365)
    out["ttm"] = ttm
    if price and price > 0 and ttm:
        out["ttm_yield_pct"] = ttm / price * 100.0
    if len(rows) < 2:                      # regola 4
        return out

    # Solo la storia recente decide: una societa' passata da semestrale a
    # trimestrale dieci anni fa paga trimestralmente OGGI.
    recent = rows[-12:]
    (_, prev_amt), (last_date, last_amt) = recent[-2], recent[-1]
    same_regime = abs(last_amt - prev_amt) <= SAME_REGIME_TOLERANCE * max(
        last_amt, prev_amt)

    if same_regime:                                              # regola 1
        peers = [(d, v) for d, v in recent
                 if abs(v - last_amt) <= SAME_REGIME_TOLERANCE * max(v, last_amt)]
        out["has_special"] = len(peers) != len(recent)
    else:                                                        # regola 2
        is_special = last_amt > SPECIAL_DIVIDEND_MULTIPLE * prev_amt
        # Livello ordinario: la mediana della META' PIU' BASSA dei pagamenti
        # recenti. La mediana semplice non basta quando gli straordinari sono
        # frequenti quasi quanto le cedole — W. R. Berkley ne stacca uno ogni
        # due trimestri — e finirebbe a meta' strada fra i due livelli.
        amounts = sorted(v for _, v in recent)
        low_half = amounts[:max(1, len(amounts) // 2)]
        baseline = low_half[len(low_half) // 2]
        peers = [(d, v) for d, v in recent
                 if v <= SPECIAL_DIVIDEND_MULTIPLE * baseline]
        out["has_special"] = len(peers) != len(recent)
        if not is_special and (last_date, last_amt) not in peers:
            peers.append((last_date, last_amt))   # taglio o aumento contenuto
            peers.sort()
    if len(peers) < 2:                      # regola 4
        return out

    # LA CADENZA SI MISURA SUL CALENDARIO, L'IMPORTO SUGLI IMPORTI. Sono due
    # domande diverse e vanno tenute separate: quando le ultime due cedole non
    # coincidono, il filtro degli straordinari serve solo a scegliere QUANTO
    # vale l'ultima cedola ordinaria, mentre OGNI QUANTO si paga si legge su
    # tutti i pagamenti. CNH ha ridotto il dividendo annuale da 0,47 a 0,10 $ in
    # tre anni: filtrando gli importi "troppo alti" sparivano gli anni di mezzo,
    # restava un buco di quattro anni fra due pagamenti e la cadenza annuale non
    # veniva piu' riconosciuta.
    cadence_from = peers if same_regime else recent
    gaps = sorted((cadence_from[i + 1][0] - cadence_from[i][0]).days
                  for i in range(len(cadence_from) - 1))
    # Voto di maggioranza, non mediana: con uno straordinario infilato fra due
    # trimestri la mediana cade a meta' strada fra due cadenze e sceglie quella
    # sbagliata, mentre la maggioranza degli intervalli resta trimestrale.
    votes: dict[int, int] = {}
    for g in gaps:
        f = min(DIVIDEND_FREQUENCIES, key=lambda x: abs(365.0 / x - g))
        votes[f] = votes.get(f, 0) + 1
    freq = max(votes, key=lambda f: (votes[f], -f))
    expected_gap = 365.0 / freq

    last_date, last_payment = peers[-1]
    out.update(frequency=freq, last_payment=last_payment)

    # regola 4: la cadenza annuale va provata meglio delle altre. Con tre o piu'
    # stacchi bastano intervalli regolari; con due soli, gli importi devono
    # coincidere quasi esattamente — Vornado paga 0,74 $ ogni dicembre, e sono
    # due pagamenti identici a un anno esatto di distanza, mentre i 75 e 90 $ di
    # TransDigm non sono una cedola che si ripete.
    if freq == 1:
        med = gaps[len(gaps) // 2]
        last_gap = (cadence_from[-1][0] - cadence_from[-2][0]).days
        # Regolare = l'intervallo tipico e' davvero un anno, la maggioranza
        # degli intervalli gli somiglia, e l'ultimo non e' un salto. I tre test
        # servono a cose diverse: Coeur Mining ha pagato ogni anno dal 1992 al
        # 1997 e poi piu' nulla fino al 2026 — la maggioranza degli intervalli
        # e' regolarissima, ed e' l'ULTIMO a dire che non e' una cadenza.
        near = sum(1 for g in gaps if abs(g - med) <= 0.3 * med)
        regular = (abs(med - 365) <= 0.3 * 365
                   and near * 3 >= len(gaps) * 2
                   and last_gap <= med * ANNUAL_MAX_GAP_RATIO)
        pair = [v for _, v in cadence_from[-2:]]
        identical = (len(pair) == 2 and max(pair) > 0
                     and abs(pair[0] - pair[1]) <= ANNUAL_PAIR_TOLERANCE * max(pair))
        enough = len(cadence_from) >= ANNUAL_MIN_PAYMENTS and regular
        if not (enough or identical):
            out["frequency"] = None
            return out

    # LA SOGLIA DI SOSPENSIONE E' LARGA DI PROPOSITO — due intervalli pieni piu'
    # due mesi. Lo storico dei dividendi di questo fornitore ha dei buchi: Aon
    # risulta saltare due trimestri che ha invece pagato. Con una soglia stretta
    # ogni buco diventerebbe una "sospensione" e cancellerebbe il rendimento di
    # una societa' che paga regolarmente — un errore peggiore di quello che si
    # vuole evitare, perche' toglie un dato vero invece di correggerne uno
    # falso.
    stale_after = expected_gap * DIVIDEND_STALE_INTERVALS + DIVIDEND_STALE_GRACE_DAYS
    if (asof - last_date).days > stale_after:        # regola 5
        out["discontinued"] = True
        return out

    # ULTIMO CONTROLLO, CONTRO I PAGAMENTI DAVVERO AVVENUTI. Una cadenza non
    # puo' promettere molti piu' stacchi di quanti se ne contino nell'ultimo
    # anno. Crown Holdings ha due trimestri distanti 58 giorni — abbastanza da
    # far sembrare mensile una cedola trimestrale, e da triplicare il
    # rendimento — ma nell'anno ha pagato quattro volte, non dodici.
    #
    # Serve pero' che ci sia qualcosa da contare: lo storico di questo fornitore
    # salta dei trimestri (Aon ne perde due), e un solo pagamento nell'anno non
    # prova che la societa' paghi una volta l'anno. Sotto i due stacchi il
    # controllo non si applica.
    # E serve che la serie sia CORRENTE. Dentsply ha smesso di pagare a fine
    # 2025: contare i suoi due stacchi rimasti nella finestra e dedurne una
    # cadenza semestrale significherebbe leggere la sospensione come un cambio
    # di politica, e dimezzare un rendimento che invece va o lasciato
    # trimestrale o dichiarato interrotto — cosa di cui si occupa gia' la
    # regola 5, poche righe sotto.
    #
    # La "corrente" si misura su una finestra FISSA e non sull'intervallo
    # dedotto: e' proprio la cadenza a essere sotto accusa, e usarla per
    # decidere se il controllo si applica lo disattiverebbe tutte le volte che
    # serve. Quattro mesi coprono sia il mensile sia il trimestrale; i pagatori
    # semestrali e annuali non arrivano comunque alla soglia dei due stacchi.
    # Il conteggio si fa su DUE ANNI dimezzati, non su uno solo: con i buchi
    # dello storico una societa' trimestrale puo' mostrare due soli stacchi
    # nell'ultimo anno, e su quella base il controllo la declasserebbe a
    # semestrale dimezzandole il rendimento — quindici titoli, fra cui Accenture
    # e Chubb, sono stati dimezzati cosi' in una versione precedente di questo
    # codice. Su due anni un buco pesa la meta'.
    window = [d for d, _ in rows if 0 <= (asof - d).days < 730]
    covered = max((asof - window[0]).days, 1) / 365.0 if window else 0.0
    n_year = len(window) / covered if covered else 0.0
    current = (asof - last_date).days <= CURRENT_SERIES_DAYS
    if current and n_year >= 2 and freq > n_year * 1.5:
        # A parita' di distanza si preferisce la cadenza PIU' FITTA: sbagliare
        # per difetto dimezza un rendimento vero, sbagliare per eccesso lo
        # gonfia — ma il caso per eccesso e' gia' stato escluso dal confronto.
        freq = min(DIVIDEND_FREQUENCIES, key=lambda f: (abs(f - n_year), -f))
        out["frequency"] = freq
    out["rate"] = rate = last_payment * freq
    if price and price > 0:
        out["yield_pct"] = rate / price * 100.0
    return out


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

    # DIVIDENDO — QUESTO E' SOLO IL RIPIEGO.
    #
    # Il campo dividendYield di yfinance e' incoerente (a volte frazione, a
    # volte percentuale), quindi non lo si usa mai. Resta dividendRate, il
    # dividendo annuo per azione, che pero' cambia significato dopo un taglio e
    # ingloba le cedole straordinarie: vedi `dividend_profile`, che ricostruisce
    # il rendimento dai pagamenti effettivi ed e' la fonte buona. Il build
    # sovrascrive quanto calcolato qui non appena ha il registro dei dividendi;
    # questo valore sopravvive solo per i ticker senza storico (Stooq).
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
