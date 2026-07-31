#!/usr/bin/env python3
"""
build_dataset.py — Costruisce i dataset per la Lynch dashboard.

Per ogni ticker:
  1. Utili storici (EPS) da SEC EDGAR  -> serie TTM EPS
  2. Bilanci storici da SEC EDGAR      -> ricavi, utile netto, FCF, patrimonio
  3. Prezzi storici da yfinance/Stooq
  4. Fair value line = TTM_EPS(as-of) * target_PE   (P/E 15/20/25)

Output in data/:
  - history.csv.gz        (ticker, date, price, eps, eps_date)
  - fundamentals.csv      (una riga per ticker: valutazione, qualita', CAGR)
  - financials_annual.csv (ultimi 5 esercizi per ticker: ricavi, utili, FCF)
  - cagr_detail.csv       (ogni CAGR con i due estremi da cui e' ricavato)
  - events.csv, filings.csv, skipped.csv

Uso:
  python build_dataset.py                      # 10 ticker di test
  python build_dataset.py --universe sp500                # S&P 500
  python build_dataset.py --universe sp500+russell1000    # ~1000 large/mid cap
  python build_dataset.py --universe us-all               # tutti i quotati USA

Sull'intero listino conviene la modalita' bulk piu' i worker paralleli:
  python build_dataset.py --universe us-all --bulk-facts --workers 6 --freq M
"""

from __future__ import annotations
import argparse
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

import requests
import pandas as pd

from edgar_logic import (
    extract_eps_facts, build_ttm_eps, build_fair_value_rows,
    ttm_growth_yoy, ttm_growth_yoy_dated, normalized_eps,
    eps_cagr, earnings_volatility, had_recent_losses, classify_lynch, lynch_ratio,
    growth_estimate, loss_profile, eps_growth_trend,
    choose_eps, fair_value_is_plausible,
    adjust_facts_for_splits, extract_share_counts, detect_share_events,
    build_derived_eps_facts, extract_dei_shares, extract_net_income_facts,
    series_age_days, series_is_stale, max_age_for_method,
    max_usable_age_for_method,
    extract_financials, compute_ratios, compute_cagrs, annual_financial_table,
    compute_cagr_details,
)
from data_sources import (
    CompanyFactsSource, fetch_price_history, fetch_current_snapshot,
    fetch_submissions, parse_company_meta, parse_recent_filings,
    fetch_shares_outstanding, fetch_us_universe, resolve_cik,
)
from sic_map import sector_from_sic, industry_from_sic, non_operating_reason

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

TARGET_PES = [15, 20, 25]
DATA_DIR = Path("data")

# Ticker di test (quelli richiesti). Nomi validi sia su EDGAR che su yfinance.
TEST_TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
                "JNJ", "V", "WMT", "PG", "JPM"]

# File con ticker extra da includere OLTRE all'universo scelto (uno per riga,
# righe che iniziano con '#' o vuote ignorate).
EXTRA_TICKERS_FILE = Path("extra_tickers.txt")

_log_lock = threading.Lock()


def log(msg: str) -> None:
    with _log_lock:
        print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def get_extra_tickers(path: Path = EXTRA_TICKERS_FILE) -> list[str]:
    """Legge extra_tickers.txt se presente. Ritorna una lista (puo' essere vuota)."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line.upper().replace(".", "-"))
    return out


def merge_tickers(base: list[str], extra: list[str]) -> list[str]:
    """Unisce due liste di ticker senza duplicati, mantenendo l'ordine."""
    seen = set(base)
    merged = list(base)
    for t in extra:
        if t not in seen:
            merged.append(t)
            seen.add(t)
    return merged


def shard(items: list, index: int, total: int) -> list:
    """
    Fetta deterministica di una lista, per distribuire una build su piu' job.

    Sull'intero listino USA un solo processo supera il tetto di sei ore di
    GitHub Actions: la build va divisa in N job paralleli che scrivono file
    parziali, poi uniti da `--merge`. Lo slicing a passo N (non a blocchi)
    distribuisce anche il carico, perche' i ticker sono ordinati alfabeticamente
    e i blocchi non sarebbero omogenei per dimensione delle societa'.
    """
    if total <= 1:
        return items
    return items[index::total]


# Eta' massima (giorni) del conteggio azioni di copertina per considerarlo
# rappresentativo. Oltre, quasi sempre significa che anche quel dato e'
# dimensionale (societa' multiclasse) e l'ultimo valore non dimensionale
# risale a prima del cambio di tagging: Visa si ferma al 2010.
MAX_DEI_SHARES_AGE_DAYS = 400


def resolve_eps_series(cf: dict, ticker: str, splits: list,
                       shares_hint: float | None = None) -> tuple[list, str, str, str | None]:
    """
    Costruisce la serie EPS annualizzata scegliendo fra le fonti disponibili,
    in ordine di qualita' decrescente, e dichiarando sempre quale ha vinto.

    1. 'edgar'         — EPS depositato, ricostruito in TTM. E' il dato primario.
    2. 'edgar-derived' — utile netto SEC diviso per il numero di azioni. Serve
                         alle societa' multiclasse (Visa, Constellation, KKR,
                         Berkshire dal 2014), per le quali l'EPS e' taggato per
                         classe e le API XBRL della SEC non lo espongono. Il
                         numeratore resta un dato depositato, il denominatore no.

    Ritorna (serie, metodo, base, nota). base e' '' se nessuna fonte e' utilizzabile.
    """
    facts = extract_eps_facts(cf)
    primary: tuple[list, str] | None = None
    if facts:
        # I prezzi di mercato sono gia' split-adjusted, gli EPS depositati no.
        facts = adjust_facts_for_splits(facts, splits)
        series, method = build_ttm_eps(facts)
        if series and not series_is_stale(series, max_age_for_method(method)):
            return series, method, "edgar", None
        if series and not series_is_stale(series, max_usable_age_for_method(method)):
            primary = (series, method)   # in ritardo ma utilizzabile: vedi sotto

    def _stale_primary():
        """
        Ultima risorsa: la serie depositata, in ritardo ma dentro il limite di
        utilizzabilita'. Meglio una societa' presente con l'eta' del dato
        dichiarata che una societa' assente senza spiegazione.
        """
        if primary is None:
            return [], "none", "", None
        series_, method_ = primary
        age = series_age_days(series_)
        note_ = (f"Ultimo dato EPS depositato nelle API XBRL della SEC: {age} "
                 f"giorni fa. Lo storico e' completo e corretto, ma non copre "
                 f"l'ultimo trimestre: per i valori 'di oggi' viene preferito "
                 f"l'EPS del provider di mercato, piu' aggiornato.")
        return series_, method_, "edgar-stale", note_

    # --- fallback: EPS derivato dall'utile netto ---
    # Nessuna rettifica per split qui: il denominatore e' il numero di azioni di
    # OGGI, quindi la serie e' gia' espressa sulla base azionaria corrente.
    # Applicare anche gli split significherebbe rettificarla due volte.
    if not extract_net_income_facts(cf):
        return _stale_primary()

    shares, shares_src = None, ""
    dei = extract_dei_shares(cf)
    if dei and (date.today() - dei[0]).days <= MAX_DEI_SHARES_AGE_DAYS:
        shares, shares_src = dei[1], "copertina SEC"
    if shares is None and shares_hint:
        shares, shares_src = shares_hint, "yfinance"
    if shares is None:
        shares = fetch_shares_outstanding(ticker)
        shares_src = "yfinance"
    if not shares:
        return _stale_primary()

    dfacts = build_derived_eps_facts(cf, shares)
    if not dfacts:
        return _stale_primary()
    d_series, d_method = build_ttm_eps(dfacts)
    if not d_series or series_is_stale(d_series, max_age_for_method(d_method)):
        return _stale_primary()

    note = (f"EPS non depositato in forma non dimensionale (societa' multiclasse): "
            f"derivato da utile netto SEC / {shares:,.0f} azioni ({shares_src}). "
            f"La serie storica usa un numero di azioni costante, quindi non "
            f"riflette buyback e diluizioni passate.")
    return d_series, d_method, "edgar-derived", note


def resample_prices(price_rows: list[tuple[date, float]], freq: str) -> list[tuple[date, float]]:
    """
    Riduce la risoluzione dello storico prezzi tenendo l'ultimo prezzo per periodo.
    freq: 'D' (giornaliero, nessun taglio), 'W' (settimanale), 'M' (mensile).
    Serve a contenere la dimensione dei CSV su scala di mercato.
    """
    if freq == "D" or not price_rows:
        return price_rows
    keep: dict = {}
    for d, p in price_rows:
        if freq == "W":
            key = (d.isocalendar().year, d.isocalendar().week)
        else:  # 'M'
            key = (d.year, d.month)
        keep[key] = (d, p)  # l'ultimo per periodo vince
    return sorted(keep.values())


def get_sp500_tickers() -> list[str]:
    """
    Lista SP500 con tre livelli di fallback.

    1) Wikipedia via requests con User-Agent esplicito. NB: pandas.read_html
       scarica senza User-Agent e Wikipedia risponde 403 agli IP dei data center
       (es. i runner di GitHub Actions): per questo scarichiamo noi l'HTML.
    2) CSV pubblico su GitHub (datasets/s-and-p-500-companies).
    3) Lista statica di emergenza (mega-cap), per non fallire del tutto.
    """
    import io as _io

    # --- 1) Wikipedia con User-Agent ---
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        ua = os.environ.get("SEC_USER_AGENT", "Lynch Valuation Tool")
        r = requests.get(url, headers={"User-Agent": ua}, timeout=30)
        r.raise_for_status()
        tables = pd.read_html(_io.StringIO(r.text))
        for t in tables:
            if "Symbol" in t.columns:
                syms = t["Symbol"].astype(str).str.upper().str.strip().tolist()
                syms = [s.replace(".", "-") for s in syms if s and s != "NAN"]
                if len(syms) > 400:
                    log(f"Lista SP500 da Wikipedia: {len(syms)} ticker")
                    return syms
        raise ValueError("tabella 'Symbol' non trovata")
    except Exception as e:
        log(f"⚠ Wikipedia non disponibile ({e}) — provo il fallback")

    # --- 2) CSV pubblico su GitHub ---
    try:
        csv_url = ("https://raw.githubusercontent.com/datasets/"
                   "s-and-p-500-companies/main/data/constituents.csv")
        r = requests.get(csv_url, timeout=30)
        r.raise_for_status()
        df = pd.read_csv(_io.StringIO(r.text))
        col = "Symbol" if "Symbol" in df.columns else df.columns[0]
        syms = df[col].astype(str).str.upper().str.strip().tolist()
        syms = [s.replace(".", "-") for s in syms if s and s != "NAN"]
        if len(syms) > 400:
            log(f"Lista SP500 da CSV GitHub: {len(syms)} ticker")
            return syms
        raise ValueError(f"solo {len(syms)} ticker nel CSV")
    except Exception as e:
        log(f"⚠ CSV GitHub non disponibile ({e}) — uso la lista di emergenza")

    # --- 3) Lista statica di emergenza ---
    fallback = [
        "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA", "META", "TSLA", "BRK-B",
        "AVGO", "JPM", "LLY", "V", "UNH", "XOM", "MA", "JNJ", "PG", "COST", "HD",
        "ABBV", "WMT", "MRK", "NFLX", "KO", "AMD", "PEP", "ADBE", "CVX", "CRM",
        "TMO", "MCD", "CSCO", "ACN", "ABT", "LIN", "INTC", "DHR", "TXN", "AMAT",
        "VZ", "PFE", "NKE", "PM", "WFC", "CAT", "DIS", "GE", "BAC", "IBM",
    ]
    log(f"Lista di emergenza: {len(fallback)} ticker (NON è l'SP500 completo)")
    return fallback


def _round(v, n=2):
    """Arrotonda solo se il valore esiste ed e' finito. Altrimenti None."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return round(f, n)


def _wikipedia_symbols(url: str, min_rows: int, label: str) -> list[str]:
    """
    Colonna 'Symbol' della prima tabella abbastanza grande di una pagina Wikipedia.

    NB: pandas.read_html scarica senza User-Agent e Wikipedia risponde 403 agli
    IP dei data center (i runner di GitHub Actions inclusi): l'HTML lo scarichiamo
    noi con un contatto dichiarato.
    """
    import io as _io
    ua = os.environ.get("SEC_USER_AGENT", "Lynch Valuation Tool")
    r = requests.get(url, headers={"User-Agent": ua}, timeout=40)
    r.raise_for_status()
    for t in pd.read_html(_io.StringIO(r.text)):
        cols = {str(c).strip(): c for c in t.columns}
        key = next((cols[c] for c in ("Symbol", "Ticker", "Ticker symbol") if c in cols), None)
        if key is None or len(t) < min_rows:
            continue
        syms = t[key].astype(str).str.upper().str.strip().tolist()
        syms = [s.replace(".", "-") for s in syms
                if s and s not in ("NAN", "NONE", "-")]
        if len(syms) >= min_rows:
            log(f"Lista {label} da Wikipedia: {len(syms)} ticker")
            return syms
    raise ValueError(f"nessuna tabella con almeno {min_rows} simboli in {url}")


def get_russell1000_tickers() -> list[str]:
    """
    Componenti del Russell 1000: le circa mille maggiori societa' USA.

    Include gia' quasi tutto l'S&P 500 e aggiunge le mid cap che restano fuori
    dall'indice. Non esiste un elenco ufficiale gratuito e scaricabile (FTSE
    Russell lo vende), quindi la fonte e' la pagina Wikipedia dell'indice, che
    la comunita' mantiene allineata alle ricostituzioni annuali.
    """
    try:
        return _wikipedia_symbols("https://en.wikipedia.org/wiki/Russell_1000_Index",
                                  min_rows=800, label="Russell 1000")
    except Exception as e:
        log(f"⚠ Russell 1000 non disponibile ({e}) — resto sull'S&P 500")
        return []


def process_ticker(ticker: str, facts_source: CompanyFactsSource, freq: str = "D",
                   min_market_cap: float = 0.0) -> tuple[list[dict], dict | None,
                                                          list[dict], list[dict],
                                                          list[dict], list[dict],
                                                          str | None]:
    """
    Ritorna (righe_storiche, riga_fondamentali, eventi, filing, bilanci_annuali,
    motivo_skip) per un ticker. riga_fondamentali e' None quando il ticker viene
    scartato, e motivo_skip spiega perche'.

    L'ORDINE DELLE CHIAMATE non e' casuale: lo snapshot di mercato viene per
    primo perche' e' l'unico modo di conoscere la capitalizzazione prima di
    scaricare i companyfacts, che sono la chiamata piu' pesante (fino a qualche
    decina di megabyte). Sull'intero listino USA questo evita di scaricare i
    bilanci di migliaia di micro-cap che verrebbero comunque filtrate.
    """
    snap = fetch_current_snapshot(ticker)
    price_today = snap.get("current_price")
    market_cap = snap.get("market_cap")

    if min_market_cap and market_cap is not None and market_cap < min_market_cap:
        return [], None, [], [], [], [], (
            f"capitalizzazione {market_cap/1e6:.0f} M$ sotto la soglia "
            f"{min_market_cap/1e6:.0f} M$")

    # Fondi, ETF e trust non hanno utili d'impresa: il modello Lynch non li
    # descrive. Escluderli qui evita righe prive di senso nello screener.
    if (snap.get("quote_type") or "").upper() in ("ETF", "MUTUALFUND", "INDEX", "CURRENCY"):
        return [], None, [], [], [], [], f"strumento non azionario ({snap.get('quote_type')})"

    # Una sola richiesta submissions serve tre cose: i link ai filing, il codice
    # SIC (da cui settore e industria quando yfinance non li da') e il tipo di
    # emittente.
    #
    # E' PRIMA dei companyfacts di proposito. submissions e' un JSON piccolo,
    # companyfacts arriva a decine di megabyte: riconoscere qui un ETF significa
    # non scaricarne i bilanci. Sull'intero listino sono migliaia di download
    # risparmiati.
    subs = fetch_submissions(ticker)
    meta = parse_company_meta(subs)

    not_operating = non_operating_reason(
        meta.get("sic"), meta.get("entity_type"), meta.get("sic_description"))
    if not_operating:
        return [], None, [], [], [], [], not_operating

    cf = facts_source.get(ticker)
    if cf is None:
        return [], None, [], [], [], [], "non risolto su EDGAR (ticker->CIK o 404)"

    recent_filings = parse_recent_filings(subs, resolve_cik(ticker), n=5)

    # --- PREZZI E SPLIT, in una sola chiamata ---
    # I prezzi di mercato sono gia' split-adjusted; gli EPS di EDGAR no. Senza
    # la rettifica la fair value line "salta" al momento dello split. Entrambi
    # arrivano dalla stessa risposta: una richiesta in meno per ticker, cioe'
    # un terzo in meno di occasioni di farsi bloccare da Yahoo.
    prices, splits, price_src = fetch_price_history(ticker)
    if not prices:
        return [], None, [], [], [], [], "nessun prezzo storico (yfinance e Stooq irraggiungibili)"
    prices = resample_prices(prices, freq)
    if price_today is None:
        price_today = prices[-1][1]       # ultimo close come ripiego

    eps_series, method, eps_basis, eps_basis_note = resolve_eps_series(
        cf, ticker, splits, shares_hint=snap.get("shares_outstanding"))
    if not eps_series:
        # Diagnostica precisa: distinguere "il dato non esiste" da "il dato
        # esiste ma il nostro codice non lo raggiunge" o "il dato e' vecchio".
        # Confonderli manda fuori dal dataset societa' perfettamente coperte.
        gaap = cf.get("facts", {}).get("us-gaap", {})
        eps_like = [k for k in gaap if "PerShare" in k or "PerDilutedShare" in k]
        has_ni = bool(extract_net_income_facts(cf))
        raw = extract_eps_facts(cf)
        raw_series, raw_method = build_ttm_eps(raw) if raw else ([], "none")
        age = series_age_days(raw_series)
        age_limit = max_usable_age_for_method(raw_method)

        if age is not None and age > age_limit:
            reason = (f"serie EPS EDGAR obsoleta: ultimo dato {age} giorni fa "
                      f"(soglia {age_limit} per una serie {raw_method}); "
                      f"tipico delle societa' che "
                      f"hanno iniziato a taggare l'EPS per classe di azioni")
        elif has_ni and not raw:
            reason = ("EPS non esposto dalle API XBRL (taggato per classe di azioni) "
                      "e numero di azioni non ricavabile per derivarlo dall'utile netto")
        elif eps_like:
            reason = (f"nessun EPS utilizzabile; tag presenti ma non usabili: "
                      f"{', '.join(eps_like[:4])}")
        else:
            reason = "nessun tag EPS ne' utile netto in us-gaap (possibile filer estero/IFRS)"
        return [], None, [], [], [], [], reason

    # eventi societari: split + variazioni rilevanti del numero di azioni
    events: list[dict] = [
        {"ticker": ticker, "date": d, "type": "split", "detail": f"{r:g}:1"}
        for d, r in splits
    ]
    for ev in detect_share_events(extract_share_counts(cf)):
        events.append({"ticker": ticker, "date": ev["date"], "type": ev["type"],
                       "detail": f"{ev['change_pct']:+.1f}% shares"})

    # --- EPS corrente: due fonti indipendenti ---
    eps_edgar = eps_series[-1][1] if eps_series else None   # ultimo TTM da EDGAR
    eps_yf = snap.get("trailing_eps")                        # trailingEps da yfinance

    # cross-check: divergenza % tra le due fonti (guardrail di affidabilita')
    if eps_edgar and eps_yf and eps_yf != 0:
        eps_div_pct = abs(eps_edgar - eps_yf) / abs(eps_yf) * 100
    else:
        eps_div_pct = None

    # ARBITRAGGIO tra le due fonti: non preferiamo ciecamente yfinance, che a
    # volte riporta valori errati (es. WM: 0.86 contro 6.91 reale). Quando
    # divergono, choose_eps verifica quale valore e' coerente con lo storico
    # normalizzato e con un P/E implicito plausibile.
    _eps_norm_pre = normalized_eps(eps_series, years=3)
    _stale = (date.today() - eps_series[-1][0]).days if eps_series else 9999
    eps_now, eps_source, eps_flag = choose_eps(
        eps_edgar, eps_yf, _eps_norm_pre, price_today, _stale)

    # ANCORAGGIO (prudente): allinea il livello della serie storica all'EPS pulito
    # di yfinance SOLO se le due misure sono davvero coeve, cioe' se l'ultimo punto
    # EDGAR e' recente. Se la serie EDGAR e' ferma indietro nel tempo, scalarla
    # significherebbe gonfiare retroattivamente TUTTO lo storico con un rapporto
    # che confronta due periodi diversi.
    eps_scale = 1.0
    last_eps_date = eps_series[-1][0] if eps_series else None
    days_stale = (date.today() - last_eps_date).days if last_eps_date else 9999
    if (eps_edgar and eps_now and eps_edgar > 0 and eps_now > 0
            and days_stale <= 200):
        ratio = eps_now / eps_edgar
        # limita a scostamenti plausibili: oltre significa disallineamento
        # strutturale (periodi diversi), non una differenza di misura
        if 0.8 <= ratio <= 1.25:
            eps_scale = ratio
    eps_series_line = ([(d, v * eps_scale) for d, v in eps_series]
                       if eps_scale != 1.0 else eps_series)

    fv_rows = build_fair_value_rows(prices, eps_series_line, TARGET_PES)
    for r in fv_rows:
        r["ticker"] = ticker

    # crescita YoY robusta (basata su date); fallback al metodo per-indice
    growth = ttm_growth_yoy_dated(eps_series)
    if growth is None:
        growth = ttm_growth_yoy(eps_series)

    # fair value "oggi" a P/E 15 con l'EPS scelto
    fv15_today = eps_now * 15 if (eps_now and eps_now > 0) else None
    # cancello di plausibilita': scarta fair value distanti dal prezzo oltre 20x
    if not fair_value_is_plausible(fv15_today, price_today):
        fv15_today = None
    if fv15_today and price_today:
        discount = (price_today - fv15_today) / fv15_today * 100  # >0 = sopra fair value
        valuation = "Undervalued" if price_today < fv15_today else "Overvalued"
    else:
        discount, valuation = None, "N/A"

    # EPS normalizzato (mediana TTM 3 anni): smussa plusvalenze/svalutazioni
    # straordinarie. Base per un fair value piu' conservativo.
    eps_norm_raw = normalized_eps(eps_series_line, years=3)
    eps_norm = eps_norm_raw if eps_norm_raw and eps_norm_raw > 0 else None
    fv15_norm = eps_norm * 15 if eps_norm else None

    # scostamento tra utili correnti e normalizzati: segnala utili "drogati"
    if eps_norm and eps_now and eps_norm > 0:
        eps_vs_norm_pct = (eps_now / eps_norm - 1) * 100
    else:
        eps_vs_norm_pct = None

    # ---- SETTORE E INDUSTRIA ----
    # yfinance e' la fonte preferita (tassonomia piu' ricca e allineata a quella
    # che gli utenti conoscono); il codice SIC della SEC copre i casi in cui
    # manca — sull'intero listino sono migliaia, e senza industria un titolo non
    # ha gruppo di confronto.
    sector = snap.get("sector")
    industry = snap.get("industry")
    class_src = "yfinance"
    if not sector:
        sector = sector_from_sic(meta.get("sic"))
        class_src = "sic" if sector else "none"
    if not industry:
        industry = industry_from_sic(meta.get("sic"), meta.get("sic_description"))
        if class_src == "yfinance" and industry:
            class_src = "yfinance+sic"
    sector = sector or "Unspecified"
    industry = industry or "Unspecified"

    # ---- BILANCI EDGAR: ricavi, utile netto, FCF, patrimonio ----
    fin = extract_financials(cf)
    ratios = compute_ratios(
        fin, price=price_today, market_cap=market_cap,
        shares=snap.get("shares_outstanding"))
    cagrs = compute_cagrs(fin, eps_series_line, horizons=(3, 5, 10))
    # NB: la tabella per esercizio usa la serie NON ancorata.
    # L'ancoraggio (eps_scale) allinea il LIVELLO della serie storica all'EPS
    # corrente del provider, ed e' giusto per il grafico e per i valori "di
    # oggi". Applicato anche qui falsificherebbe una tabella intitolata "as
    # filed": Qualcomm mostrava un EPS 2024 di 9,52$ contro gli 8,97$
    # effettivamente depositati, per un fattore di 1,062. Cio' che e' dichiarato
    # come depositato deve essere il depositato (rettificato per gli split, che
    # e' l'unica trasformazione necessaria a renderlo comparabile nel tempo).
    annual_rows = [
        {"ticker": ticker, **{k: (str(v) if k == "fy_end" else v)
                              for k, v in row.items()}}
        for row in annual_financial_table(fin, eps_series, n_years=6)
    ]
    # Estremi di ogni CAGR, per poterlo rifare a mano dalla scheda Data quality.
    cagr_rows = [
        {"ticker": ticker,
         **{k: (str(v) if k in ("start_date", "end_date") else v)
            for k, v in row.items()}}
        for row in compute_cagr_details(fin, eps_series_line, horizons=(3, 5, 10))
    ]

    # ---- CLASSIFICAZIONE LYNCH + FAIR VALUE A MULTIPLO VARIABILE ----
    # La crescita che alimenta il multiplo equo viene dalla scala di
    # growth_estimate (tendenza a 5 anni, poi CAGR, poi finestre a 3 anni), non
    # dal solo CAGR a 5 anni: e' il moltiplicatore del fair value, e prenderlo da
    # due soli trimestri era la prima causa dei multipli senza senso.
    gest = growth_estimate(eps_series_line)
    growth_5y = gest["value"]
    growth_5y_cagr_raw = eps_cagr(eps_series_line, years=5)
    growth_5y_trend = eps_growth_trend(eps_series_line, years=5)
    volatility = earnings_volatility(eps_series_line)
    lp = loss_profile(eps_series_line, years=5)
    losses = lp["any"]

    cls = classify_lynch(
        growth_pct=growth_5y,
        eps_now=eps_now,
        volatility=volatility,
        sector=sector,
        dividend_yield=snap.get("dividend_yield"),
        price_to_book=snap.get("price_to_book"),
        market_cap=market_cap,
        losses=lp,
        eps_normalized=eps_norm,
        growth_basis=gest["basis"],
        growth_confidence=gest["confidence"],
        rebound_risk=gest["rebound_risk"],
    )

    # Fair value di categoria. L'ANCORA la decide classify_lynch, non questa
    # riga: puo' essere un multiplo sugli utili correnti, lo stesso multiplo
    # sugli utili normalizzati (cicliche, turnaround, utili in calo), oppure il
    # patrimonio netto per azione (asset play). Prima la scelta era cablata qui
    # con un `if category == "Cyclical"`, e ogni nuova categoria che avesse
    # avuto bisogno di una base diversa avrebbe dovuto ricordarsi di modificarla.
    fair_pe = cls["fair_pe"]
    fv_peg = None
    if cls["anchor"] == "book":
        bvps = ratios.get("book_value_per_share")
        if bvps and bvps > 0 and cls.get("fair_pb"):
            fv_peg = bvps * cls["fair_pb"]
    elif cls["anchor"] == "earnings" and fair_pe:
        base_eps = eps_norm if cls["eps_base"] == "normalized" else eps_now
        if base_eps and base_eps > 0:
            fv_peg = base_eps * fair_pe
    if not fair_value_is_plausible(fv_peg, price_today):
        fv_peg = None
    if fv_peg and price_today:
        peg_disc = (price_today / fv_peg - 1) * 100
        peg_val = "Undervalued" if price_today < fv_peg else "Overvalued"
    else:
        peg_disc, peg_val = None, "N/A"

    # ---- P/E RICALCOLATO DAI COMPONENTI SCELTI ----
    # Il trailingPE del provider e' calcolato sul SUO EPS. Quando l'arbitraggio
    # sceglie una fonte diversa, quel P/E descrive un altro numero e la riga
    # diventa internamente incoerente: Howmet mostrava P/E 1.694 accanto a un
    # prezzo e a un EPS che ne implicano 67. Il P/E che pubblichiamo deve
    # derivare dal prezzo e dall'EPS che pubblichiamo, sempre.
    pe_provider = snap.get("pe_ratio")
    pe_calc = (price_today / eps_now) if (price_today and eps_now and eps_now > 0) else None
    pe_divergence = (abs(pe_calc - pe_provider) / pe_provider * 100
                     if (pe_calc and pe_provider and pe_provider > 0) else None)

    lr = lynch_ratio(growth_5y, snap.get("dividend_yield"), pe_calc)

    # PEG classico = P/E / crescita. Convenzione opposta al Lynch ratio:
    # PEG < 1 = economico, PEG > 1 = caro.
    peg = (pe_calc / growth_5y) if (pe_calc and growth_5y and growth_5y > 0) else None

    fund = {
        "ticker": ticker,
        # Il CIK serve a costruire i link di verifica verso le API della SEC
        # (companyconcept per singola voce di bilancio, viewer XBRL, filing).
        "cik": resolve_cik(ticker),
        "company": snap.get("company") or meta.get("company_sec") or ticker,
        "sector": sector,
        "industry": industry,
        "classification_source": class_src,
        "exchange": meta.get("exchange"),
        "sic": meta.get("sic"),
        "sic_description": meta.get("sic_description"),
        # pe_ratio = prezzo / eps_ttm, entrambi pubblicati in questa stessa riga.
        "pe_ratio": _round(pe_calc),
        "pe_ratio_provider": _round(pe_provider),
        "pe_divergence_pct": _round(pe_divergence, 1),
        "forward_pe": _round(snap.get("forward_pe")),
        "forward_eps": _round(snap.get("forward_eps"), 3),
        "eps_ttm": _round(eps_now, 3),
        "eps_ttm_yf": _round(eps_yf, 3),
        "eps_ttm_edgar": _round(eps_edgar, 3),
        "eps_divergence_pct": _round(eps_div_pct, 1),
        "eps_flag": eps_flag,
        "eps_source": eps_source,
        "eps_scale_applied": (_round(eps_scale, 3)
                              if abs(eps_scale - 1.0) > 0.02 else None),
        "eps_growth_yoy": _round(growth),
        "current_price": _round(price_today),
        "fair_value_pe15": _round(fv15_today),
        "eps_normalized_3y": _round(eps_norm, 3),
        "fair_value_norm_pe15": _round(fv15_norm),
        "eps_vs_normalized_pct": _round(eps_vs_norm_pct, 1),
        "eps_last_date": str(eps_series[-1][0]) if eps_series else None,
        "lynch_category": cls["category"],
        "lynch_fair_pe": _round(fair_pe, 1),
        "lynch_pe_basis": cls["basis"],
        "lynch_note": cls["note"],
        # Su COSA e' ancorato il fair value di categoria: "earnings" (multiplo),
        # "book" (patrimonio netto per azione), "none" (nessuna base valida).
        # Senza questa colonna l'interfaccia non puo' spiegare un fair value che
        # non nasce da un P/E, e per le asset play e' esattamente il caso.
        "lynch_anchor": cls["anchor"],
        "lynch_eps_base": cls["eps_base"],
        "lynch_fair_pb": _round(cls.get("fair_pb"), 2),
        "lynch_confidence": cls["confidence"],
        "lynch_confidence_note": cls["confidence_note"],
        "fair_value_peg": _round(fv_peg),
        "discount_vs_peg_pct": _round(peg_disc, 1),
        "valuation_peg": peg_val,
        # growth_5y_cagr resta il nome storico della colonna (l'esporta lo
        # screener), ma ora contiene la stima SCELTA dalla scala, non
        # necessariamente il CAGR. Le due misure grezze viaggiano accanto, cosi'
        # che la scheda possa mostrare da dove viene il multiplo.
        "growth_5y_cagr": _round(growth_5y, 1),
        "growth_basis": gest["basis"],
        "growth_5y_cagr_raw": _round(growth_5y_cagr_raw, 1),
        "growth_5y_trend": _round(growth_5y_trend, 1),
        "loss_periods_5y": lp["periods"],
        "loss_episodes_5y": lp["episodes"],
        "years_since_last_loss": _round(lp["quarters_since"], 1),
        "earnings_volatility": _round(volatility, 1),
        "lynch_ratio": _round(lr),
        "peg_ratio": _round(peg),
        "price_to_book": _round(snap.get("price_to_book"), 3),
        "had_recent_losses": losses,
        "eps_points": len(eps_series),
        "n_splits": len(splits),
        "discount_vs_fv15_pct": _round(discount, 1),
        "dividend_yield": _round(snap.get("dividend_yield")),
        "market_cap": snap.get("market_cap"),
        "shares_outstanding": snap.get("shares_outstanding"),
        "next_earnings_date": snap.get("next_earnings_date"),
        "beta": _round(snap.get("beta")),
        # --- bilanci EDGAR ---
        "revenue_ttm": ratios.get("revenue_ttm"),
        "revenue_latest_fy": ratios.get("revenue_latest_fy"),
        "net_income_ttm": ratios.get("net_income_ttm"),
        "ebit_ttm": ratios.get("ebit_ttm"),
        "ocf_ttm": ratios.get("ocf_ttm"),
        "fcf_ttm": ratios.get("fcf_ttm"),
        "fcf_latest_fy": ratios.get("fcf_latest_fy"),
        "equity": ratios.get("equity"),
        "assets": ratios.get("assets"),
        "long_term_debt": ratios.get("long_term_debt"),
        "total_debt": ratios.get("total_debt"),
        "cash": ratios.get("cash"),
        "net_debt": ratios.get("net_debt"),
        "enterprise_value": ratios.get("enterprise_value"),
        "ev_to_ebit": _round(ratios.get("ev_to_ebit")),
        "net_debt_to_ebit": _round(ratios.get("net_debt_to_ebit")),
        "ps_ratio": _round(ratios.get("ps_ratio")),
        "pfcf_ratio": _round(ratios.get("pfcf_ratio")),
        "roe_pct": _round(ratios.get("roe_pct"), 1),
        "equity_ratio_pct": _round(ratios.get("equity_ratio_pct"), 1),
        "earning_power_pct": _round(ratios.get("earning_power_pct"), 1),
        "net_margin_pct": _round(ratios.get("net_margin_pct"), 1),
        "operating_margin_pct": _round(ratios.get("operating_margin_pct"), 1),
        "debt_to_equity": _round(ratios.get("debt_to_equity")),
        "fcf_yield_pct": _round(ratios.get("fcf_yield_pct"), 2),
        "fcf_growth_yoy_pct": _round(ratios.get("fcf_growth_yoy_pct"), 1),
        # 'ttm' oppure 'annual:<voci>' quando per quelle voci si e' dovuto
        # ripiegare sull'ultimo esercizio (societa' che depositano solo il 10-K).
        "financials_basis": ratios.get("financials_basis"),
        # Tag XBRL da cui vengono davvero i numeri, voce per voce: senza questo
        # "ricavi 44,1 B$" non e' verificabile, perche' non si sa quale riga del
        # bilancio sia stata letta.
        "concepts_used": ";".join(f"{k}={v}" for k, v in
                                  (fin.get("concepts") or {}).items() if v) or None,
        "revenue_growth_yoy_pct": _round(ratios.get("revenue_growth_yoy_pct"), 1),
        "book_value_per_share": _round(ratios.get("book_value_per_share")),
        "revenue_per_share": _round(ratios.get("revenue_per_share")),
        "fcf_per_share": _round(ratios.get("fcf_per_share")),
        # --- CAGR (3, 5, 10 anni) ---
        **{k: _round(v, 1) for k, v in cagrs.items()},
        "eps_method": method,
        # Provenienza della serie storica: 'edgar' = EPS depositato,
        # 'edgar-derived' = utile netto SEC / azioni (societa' multiclasse).
        "eps_basis": eps_basis,
        "eps_basis_note": eps_basis_note,
        "eps_series_age_days": series_age_days(eps_series),
        "price_source": price_src,
        # Input mancanti che influenzano la classificazione, dichiarati invece
        # che trattati come zero: senza market cap ogni Stalwart finisce in
        # "mid cap", senza dividendo il multiplo equo degli Slow Grower scende
        # e la societa' tende a risultare sopravvalutata.
        "missing_inputs": ";".join(k for k, v in (
            ("market_cap", market_cap),
            ("dividend_yield", snap.get("dividend_yield")),
            ("price_to_book", snap.get("price_to_book")),
            ("growth_5y", growth_5y),
            ("revenue", ratios.get("revenue_ttm")),
            ("free_cash_flow", ratios.get("fcf_ttm")),
            ("equity", ratios.get("equity")),
        ) if v is None) or None,
        "valuation": valuation,
    }

    tag = "UNDER" if valuation == "Undervalued" else ("OVER" if valuation == "Overvalued" else "N/A")
    flagmark = "" if eps_flag == "ok" else f" ⚠{eps_flag}"
    log(f"  {ticker}: ✓ {len(fv_rows)} righe | EPS={eps_now:.2f} "
        f"(div={fund['eps_divergence_pct']}%){flagmark} | {industry} | {tag}")
    filings_out = [{"ticker": ticker, **f_} for f_ in recent_filings]
    return fv_rows, fund, events, filings_out, annual_rows, cagr_rows, None


# ---------------------------------------------------------------------------
# Universi
# ---------------------------------------------------------------------------

UNIVERSE_CHOICES = ("test", "sp500", "russell1000", "sp500+russell1000", "us-all")


def build_universe(name: str) -> list[str]:
    """
    Lista di ticker per l'universo richiesto.

    Accetta piu' universi uniti con '+' o ',' (es. 'sp500+russell1000'): l'unione
    e' senza duplicati e mantiene l'ordine, quindi chiedere S&P 500 e Russell
    1000 insieme non elabora due volte le circa 500 societa' in comune.
    """
    parts = [p.strip() for p in name.replace("+", ",").split(",") if p.strip()]
    if len(parts) > 1:
        merged: list[str] = []
        for part in parts:
            before = len(merged)
            merged = merge_tickers(merged, build_universe(part))
            log(f"  + {part}: {len(merged) - before} ticker nuovi "
                f"(totale {len(merged)})")
        return merged

    one = parts[0] if parts else name
    if one == "test":
        return list(TEST_TICKERS)
    if one == "sp500":
        return get_sp500_tickers()
    if one == "russell1000":
        return get_russell1000_tickers()
    if one == "us-all":
        rows = fetch_us_universe()
        log(f"Universo USA dalla SEC: {len(rows)} titoli su "
            f"{', '.join(sorted({r['exchange'] for r in rows}))}")
        return [r["ticker"] for r in rows]
    raise ValueError(f"universo sconosciuto: {one}")


# Motivi di skip che vale la pena ritentare: sono quasi sempre un timeout o un
# rate-limit momentaneo, non un problema strutturale del ticker (a differenza di
# "non risolto su EDGAR", deterministico, dove ritentare non cambia nulla).
_RETRYABLE = ("nessun prezzo",)


def _write_csv(df: pd.DataFrame, path: Path, **kw) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, **kw)


def _suffix(shard_idx: int | None, shards: int) -> str:
    """Suffisso di file per le build divise in piu' job."""
    return "" if shards <= 1 or shard_idx is None else f".part{shard_idx}"


def merge_parts() -> None:
    """
    Unisce i file parziali prodotti dai job sharded in un dataset unico.

    Ogni job scrive `<nome>.partN.csv`; qui li concateniamo, togliamo i
    duplicati e riscriviamo i file definitivi. Il merge e' idempotente: puo'
    essere rilanciato senza effetti collaterali.
    """
    specs = [
        ("history", ["ticker", "date"], True),
        ("fundamentals", ["ticker"], False),
        ("financials_annual", ["ticker", "fy_end"], False),
        ("cagr_detail", ["ticker", "metric", "horizon_years"], False),
        ("events", ["ticker", "date", "type"], False),
        ("filings", ["ticker", "url"], False),
        ("skipped", ["ticker"], False),
    ]
    for name, keys, gz in specs:
        parts = sorted(DATA_DIR.glob(f"{name}.part*.csv*"))
        if not parts:
            continue
        frames = [pd.read_csv(p) for p in parts]
        df = pd.concat(frames, ignore_index=True)
        df = df.drop_duplicates(subset=[k for k in keys if k in df.columns])
        sort_keys = [k for k in keys if k in df.columns]
        if sort_keys:
            df = df.sort_values(sort_keys)
        out = DATA_DIR / (f"{name}.csv.gz" if gz else f"{name}.csv")
        _write_csv(df, out, compression="gzip" if gz else None)
        log(f"✓ {out.name} ({len(df)} righe da {len(parts)} parti)")
        for p in parts:
            p.unlink()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default=None,
                    help="Universo da elaborare: " + " · ".join(UNIVERSE_CHOICES) +
                         ". Piu' universi si uniscono con '+' "
                         "(es. sp500+russell1000). Default: test")
    ap.add_argument("--full", action="store_true",
                    help="Alias storico di --universe sp500")
    ap.add_argument("--freq", choices=["D", "W", "M"], default=None,
                    help="Risoluzione storico: D=giornaliero, W=settimanale, M=mensile")
    ap.add_argument("--tickers", default=None,
                    help="Ticker extra separati da virgola, aggiunti alla lista")
    ap.add_argument("--workers", type=int, default=1,
                    help="Ticker elaborati in parallelo (consigliato 4-6 sui grandi universi)")
    ap.add_argument("--bulk-facts", action="store_true",
                    help="Scarica una sola volta l'archivio companyfacts della SEC "
                         "invece di una richiesta per societa' (consigliato oltre "
                         "il migliaio di ticker)")
    ap.add_argument("--shard", type=int, default=None,
                    help="Indice di questo job (0-based) quando la build e' divisa")
    ap.add_argument("--shards", type=int, default=1,
                    help="Numero totale di job fra cui dividere l'universo")
    ap.add_argument("--merge", action="store_true",
                    help="Unisce i file parziali .partN prodotti dai job sharded")
    ap.add_argument("--min-market-cap", type=float, default=0.0,
                    help="Esclude le societa' sotto questa capitalizzazione (in $)")
    ap.add_argument("--limit", type=int, default=None,
                    help="Elabora solo i primi N ticker (per provare la pipeline)")
    args = ap.parse_args()

    DATA_DIR.mkdir(exist_ok=True)

    if args.merge:
        merge_parts()
        return

    universe = args.universe or ("sp500" if args.full else "test")
    tickers = build_universe(universe)
    log(f"Universo '{universe}': {len(tickers)} ticker")

    extra = get_extra_tickers()
    if args.tickers:
        extra = merge_tickers(extra, [t.strip().upper()
                                      for t in args.tickers.split(",") if t.strip()])
    if extra:
        before = len(tickers)
        tickers = merge_tickers(tickers, extra)
        log(f"Ticker extra ({len(tickers) - before} aggiunti): {', '.join(extra)}")

    if args.shards > 1:
        idx = args.shard or 0
        tickers = shard(tickers, idx, args.shards)
        log(f"Shard {idx + 1}/{args.shards}: {len(tickers)} ticker")
    if args.limit:
        tickers = tickers[:args.limit]
        log(f"Limite richiesto: primi {len(tickers)} ticker")

    # default: giornaliero sui piccoli universi, settimanale sull'SP500,
    # mensile sull'intero listino (altrimenti il file storico esplode)
    # default: giornaliero sui piccoli universi, settimanale fino a ~1500 ticker,
    # mensile oltre (altrimenti il file storico esplode)
    freq = args.freq or ("D" if len(tickers) <= 50
                        else "W" if len(tickers) <= 1500 else "M")
    log(f"Risoluzione storico: {freq}")

    facts_source = CompanyFactsSource(mode="bulk" if args.bulk_facts else "api")
    if args.bulk_facts:
        log("Modalita' bulk: scarico l'archivio companyfacts della SEC "
            "(~1,5 GB, una volta sola)...")

    all_hist: list[dict] = []
    all_fund: list[dict] = []
    all_events: list[dict] = []
    all_filings: list[dict] = []
    all_annual: list[dict] = []
    all_cagr: list[dict] = []
    skip_reasons: list[dict] = []
    done = 0
    total = len(tickers)
    t0 = time.time()

    def work(t: str):
        try:
            out = process_ticker(t, facts_source, freq=freq,
                                 min_market_cap=args.min_market_cap)
            if out[1] is None and out[5] and out[5].startswith(_RETRYABLE):
                time.sleep(3)
                out = process_ticker(t, facts_source, freq=freq,
                                     min_market_cap=args.min_market_cap)
            return t, out, None
        except Exception as e:                       # noqa: BLE001 — va loggato, non propagato
            return t, None, e

    def collect(t, out, err):
        nonlocal done
        done += 1
        if err is not None:
            log(f"[{done}/{total}] {t}: ERRORE {err}")
            skip_reasons.append({"ticker": t, "motivo": f"eccezione: {err}"})
            return
        hist, fund, events, filings, annual, cagr, reason = out
        all_hist.extend(hist)
        all_events.extend(events)
        all_filings.extend(filings)
        all_annual.extend(annual)
        all_cagr.extend(cagr)
        if fund:
            all_fund.append(fund)
        else:
            skip_reasons.append({"ticker": t, "motivo": reason or "motivo sconosciuto"})
            log(f"[{done}/{total}] {t}: ✗ {reason}")
        if done % 100 == 0:
            rate = done / max(time.time() - t0, 1e-9)
            eta = (total - done) / rate / 60 if rate else 0
            log(f"— avanzamento {done}/{total} ({done/total:.0%}) · "
                f"{rate*60:.0f} ticker/min · stimati {eta:.0f} min alla fine")

    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(work, t): t for t in tickers}
            for fut in as_completed(futures):
                t, out, err = fut.result()
                collect(t, out, err)
    else:
        for t in tickers:
            collect(*work(t))

    sfx = _suffix(args.shard, args.shards)

    # --- scrittura ---
    if all_hist:
        hist_df = pd.DataFrame(all_hist)
        # SOLO le colonne essenziali: i fair value ai vari P/E sono semplici
        # moltiplicazioni di eps e vengono ricalcolati al volo dall'app.
        hist_df = hist_df[["ticker", "date", "price", "eps", "eps_date"]].sort_values(
            ["ticker", "date"])
        hist_df["price"] = hist_df["price"].round(2)
        hist_df["eps"] = hist_df["eps"].round(4)
        out_gz = DATA_DIR / f"history{sfx}.csv.gz"
        _write_csv(hist_df, out_gz, compression="gzip")
        size_mb = out_gz.stat().st_size / 1e6
        log(f"✓ {out_gz.name} ({len(hist_df)} righe, {size_mb:.1f} MB)")
        old_csv = DATA_DIR / "history.csv"
        if not sfx and old_csv.exists():
            old_csv.unlink()   # residuo non compresso di run precedenti
        if size_mb > 90:
            log(f"⚠ ATTENZIONE: {size_mb:.1f} MB, vicino al limite GitHub di 100 MB. "
                f"Rilancia con --freq M.")

    if all_events:
        ev_df = pd.DataFrame(all_events).drop_duplicates(
            subset=["ticker", "date", "type"]).sort_values(["ticker", "date"])
        _write_csv(ev_df, DATA_DIR / f"events{sfx}.csv")
        log(f"✓ events{sfx}.csv ({len(ev_df)} eventi)")

    if all_filings:
        fil_df = pd.DataFrame(all_filings).drop_duplicates(
            subset=["ticker", "url"]).sort_values(["ticker", "filing_date"],
                                                  ascending=[True, False])
        _write_csv(fil_df, DATA_DIR / f"filings{sfx}.csv")
        log(f"✓ filings{sfx}.csv ({len(fil_df)} filing)")

    if all_annual:
        ann_df = pd.DataFrame(all_annual).sort_values(["ticker", "fy_end"])
        _write_csv(ann_df, DATA_DIR / f"financials_annual{sfx}.csv")
        log(f"✓ financials_annual{sfx}.csv ({len(ann_df)} esercizi)")

    if all_cagr:
        cg_df = pd.DataFrame(all_cagr).sort_values(
            ["ticker", "metric", "horizon_years"])
        _write_csv(cg_df, DATA_DIR / f"cagr_detail{sfx}.csv")
        log(f"✓ cagr_detail{sfx}.csv ({len(cg_df)} tassi, con i due estremi "
            f"di ciascuno)")

    if all_fund:
        fund_df = pd.DataFrame(all_fund).sort_values("ticker")
        _write_csv(fund_df, DATA_DIR / f"fundamentals{sfx}.csv")
        log(f"✓ fundamentals{sfx}.csv ({len(fund_df)} ticker)")

        under = (fund_df["valuation"] == "Undervalued").sum()
        over = (fund_df["valuation"] == "Overvalued").sum()
        log(f"Riepilogo: {under} undervalued, {over} overvalued (a P/E 15)")
        n_check = (fund_df["eps_flag"] == "check").sum()
        if n_check:
            tick = ", ".join(fund_df.loc[fund_df["eps_flag"] == "check", "ticker"][:10])
            log(f"Da verificare (fonti EPS discordi >15%): {n_check} → {tick}")
        n_ind = fund_df["industry"].nunique()
        log(f"Industrie distinte: {n_ind} · classificazione da SIC per "
            f"{(fund_df['classification_source'] != 'yfinance').sum()} ticker")

    skip_path = DATA_DIR / f"skipped{sfx}.csv"
    if skip_reasons:
        _write_csv(pd.DataFrame(skip_reasons).sort_values("ticker"), skip_path)
        log(f"✓ {skip_path.name} ({len(skip_reasons)} ticker saltati, con motivo)")
    elif skip_path.exists():
        skip_path.unlink()

    log(f"FATTO in {(time.time() - t0)/60:.1f} minuti "
        f"({len(all_fund)} ticker validi, {len(skip_reasons)} saltati).")


if __name__ == "__main__":
    main()
