#!/usr/bin/env python3
"""
app.py — Lynch Valuation Dashboard (Streamlit)

Two views:
  1. Screener — table sorted by discount to fair value, with sector/industry
     and each stock's P/E gap against its own industry.
  2. Details  — everything about one ticker, in four tabs: the valuation
     chart, the financial statements, the 5-year track record, and the data
     quality audit.

Data: the CSVs in data/, produced by build_dataset.py.
"""

from __future__ import annotations
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import charts

# La tabella avanzata dello Screener dipende da un componente di terze parti.
# Se non e' installato l'app deve continuare a partire con la tabella classica:
# un componente mancante non puo' portarsi dietro l'intero screener.
try:
    import screener_grid
    HAS_AGGRID = True
except ImportError:
    screener_grid = None
    HAS_AGGRID = False

# Cartella dei CSV. Sovrascrivibile con LYNCH_DATA_DIR per aprire la dashboard
# su un dataset diverso — un universo alternativo, o una build di prova — senza
# spostare i file di quello buono.
DATA_DIR = Path(os.environ.get("LYNCH_DATA_DIR", "data"))
PE_OPTIONS = [15, 20, 25]

# Minimum number of companies in an industry before its median is used as a
# peer benchmark. Below this, one outlier moves the "industry average" enough
# to make the comparison misleading — better to fall back to the sector, and
# to say so.
MIN_PEERS = 5

# Lynch category icons, shared between the Details and Screener views
LYNCH_ICONS = {"Fast Grower": "🚀", "Stalwart": "🏛️",
               "Slow Grower": "🐢", "Cyclical": "🔄",
               "Asset Play": "🏗️", "Turnaround": "🔧",
               "Unclassified": "❓"}

# Ordine di presentazione delle categorie nei filtri. NON e' l'elenco di quelle
# ammesse: le opzioni si ricavano sempre dai dati (vedi lo Screener). Serve solo
# a dare un ordine sensato — dalla piu' in crescita alla piu' in difficolta' —
# invece dell'ordine alfabetico.
#
# Prima questo elenco era anche il FILTRO, e le categorie che non vi comparivano
# sparivano dallo Screener senza un messaggio: "Stalwart (dimensione non
# disponibile)" non era nell'elenco, e le sue undici societa' non erano
# selezionabili in nessun modo.
LYNCH_ORDER = ["Fast Grower", "Stalwart", "Stalwart (mid cap)",
               "Stalwart (size unknown)", "Stalwart (recovering)",
               "Slow Grower", "Slow Grower (declining earnings)",
               "Cyclical", "Asset Play", "Turnaround", "Unclassified"]

CONFIDENCE_BADGE = {"high": "🟢 high confidence", "medium": "🟡 medium confidence",
                    "low": "🔴 low confidence"}


def lynch_icon(cat: str | None) -> str:
    if not cat:
        return "❓"
    return next((v for k, v in LYNCH_ICONS.items() if cat.startswith(k)), "❓")


st.set_page_config(page_title="Lynch Valuation", page_icon="📈",
                   layout="wide", initial_sidebar_state="collapsed")

# --- minimal styling ---
st.markdown("""
<style>
  .block-container {padding-top: 2rem; max-width: 1280px;}
  h1, h2, h3 {letter-spacing: -0.02em;}
  [data-testid="stMetricValue"] {font-size: 1.35rem;}
  [data-testid="stMetricLabel"] {opacity: 0.75;}
</style>
""", unsafe_allow_html=True)


# ===========================================================================
# DATA LOADING
# ===========================================================================

@st.cache_data
def _read_csv(path_str: str, mtime: float, parse_dates=None) -> pd.DataFrame:
    """
    CSV read with cache invalidated by the file's timestamp.

    IMPORTANT: 'mtime' is part of the cache key. Without it Streamlit would
    keep serving the version read the first time, even after regenerating the
    CSVs with build_dataset.py — showing stale data on top of new code.
    """
    return pd.read_csv(path_str, parse_dates=parse_dates)


def _load(name: str, parse_dates=None) -> pd.DataFrame | None:
    p = DATA_DIR / name
    if not p.exists():
        return None
    return _read_csv(str(p), p.stat().st_mtime, parse_dates)


def load_history() -> pd.DataFrame | None:
    """
    Loads the historical series. Prefers the compressed version (.csv.gz): on
    the full US market the uncompressed CSV exceeds GitHub's 100 MB limit.
    Fair values at the various P/E multiples are NOT stored in the file: we
    recompute them here from eps, since they're simple multiplications and
    would triple the file size otherwise.
    """
    df = None
    for name in ("history.csv.gz", "history.csv"):
        if (DATA_DIR / name).exists():
            df = _load(name, parse_dates=["date"])
            break
    if df is None:
        return None
    for pe in PE_OPTIONS:
        col = f"fair_value_pe{pe}"
        if col not in df.columns:
            df[col] = np.where(df["eps"] > 0, df["eps"] * pe, np.nan)
    return df


def load_fundamentals() -> pd.DataFrame | None:
    """
    NB: build_dataset.py writes the literal value "N/A" for tickers that
    can't be valued (Turnaround, Asset Play, earnings too far from price...).
    pandas.read_csv treats "N/A" as one of its default NA values though, and
    silently converts it to NaN: the Screener's
    `.isin(["Undervalued", "Overvalued", "N/A"])` filter never matches NaN, so
    those rows used to vanish from the table with no error. We restore them
    here. Same story for a missing sector/industry/category: without a
    fallback they're excluded from every selection, "All" included.
    """
    df = _load("fundamentals.csv")
    if df is None:
        return None
    for col in ("valuation", "valuation_peg"):
        if col in df.columns:
            df[col] = df[col].fillna("N/A")
    for col in ("sector", "industry"):
        if col in df.columns:
            df[col] = df[col].fillna("Unspecified").replace("", "Unspecified")
    if "lynch_category" in df.columns:
        df["lynch_category"] = df["lynch_category"].fillna("Unclassified")
    return derive_ratios(df)


# Tassazione usata per il NOPAT del ROIC. Non e' l'aliquota effettiva della
# societa': quella richiederebbe le imposte depositate riga per riga, che il
# dataset non porta. E' l'aliquota federale statunitense, la stessa per tutti,
# dichiarata nel tooltip — un ROIC confrontabile fra societa' vale piu' di un
# ROIC "esatto" per una sola e incomparabile con le altre.
ROIC_TAX_RATE = 0.21


def _series(df: pd.DataFrame, col: str) -> pd.Series:
    """La colonna come numeri, o una colonna di NaN se non c'e'."""
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce")


def _ratio(num: pd.Series, den: pd.Series, *, positive_den: bool = True,
           scale: float = 1.0) -> pd.Series:
    """
    Divisione riga per riga che si rifiuta di produrre numeri privi di senso.

    Un denominatore ZERO o NEGATIVO non da' un rapporto interpretabile: un
    margine su ricavi negativi, un ROIC su capitale investito negativo (societa'
    con piu' cassa che capitale) o un payout su utili in perdita si leggerebbero
    come valori normali con il segno sbagliato. Meglio la cella vuota, che il
    resto dell'interfaccia sa gia' rappresentare.
    """
    den = den.where(den > 0) if positive_den else den.where(den != 0)
    return num / den * scale


def derive_ratios(df: pd.DataFrame) -> pd.DataFrame:
    """
    I rapporti che il dataset non porta gia' pronti, calcolati QUI e una volta
    sola.

    Perche' qui e non nella pagina: le mediane di settore (`peer_medians`) si
    costruiscono sulle colonne del DataFrame, quindi un rapporto calcolato al
    momento di disegnare una scheda sarebbe l'unico senza confronto con i pari.
    Aggiungendolo alla tabella, ogni nuovo indicatore eredita gratuitamente il
    delta contro l'industria come tutti gli altri.
    """
    rev = _series(df, "revenue_ttm")
    ni = _series(df, "net_income_ttm")
    ebit = _series(df, "ebit_ttm")
    ocf = _series(df, "ocf_ttm")
    fcf = _series(df, "fcf_ttm")
    assets = _series(df, "assets")
    equity = _series(df, "equity")
    debt = _series(df, "total_debt")
    cash = _series(df, "cash")
    ndebt = _series(df, "net_debt")
    mcap = _series(df, "market_cap")
    pe = _series(df, "pe_ratio")
    dy = _series(df, "dividend_yield")

    # Capex non e' nel file dei fondamentali, ma e' esattamente la differenza
    # fra i due flussi di cassa che ci sono. Segno POSITIVO = uscita.
    df["capex_ttm"] = ocf - fcf

    df["fcf_margin_pct"] = _ratio(fcf, rev, scale=100)
    df["ocf_margin_pct"] = _ratio(ocf, rev, scale=100)
    df["capex_to_revenue_pct"] = _ratio(df["capex_ttm"], rev, scale=100)
    # Conversione: quanta parte dell'utile CONTABILE diventa cassa. Ha senso
    # solo con utile positivo — con una perdita il rapporto cambia segno e si
    # leggerebbe come una conversione eccellente.
    df["fcf_conversion_pct"] = _ratio(fcf, ni, scale=100)
    df["asset_turnover"] = _ratio(rev, assets)

    invested = equity + debt.fillna(0) - cash.fillna(0)
    df["roic_pct"] = _ratio(ebit * (1 - ROIC_TAX_RATE), invested, scale=100)

    df["net_debt_to_equity"] = _ratio(ndebt, equity)
    df["debt_to_assets_pct"] = _ratio(debt, assets, scale=100)
    # Quanti anni di cassa libera servono per estinguere il debito netto. E' la
    # domanda a cui il debito/EBIT risponde solo per approssimazione: gli
    # interessi si pagano con la cassa, non con l'utile operativo.
    df["net_debt_to_fcf"] = _ratio(ndebt, fcf)
    df["cash_to_debt_pct"] = _ratio(cash, debt, scale=100)
    df["earnings_yield_pct"] = _ratio(pd.Series(100.0, index=df.index), pe)
    # Dividendi pagati (rendimento × capitalizzazione) sugli utili dello stesso
    # periodo: quanta parte dell'utile esce dall'azienda verso i soci.
    df["payout_ratio_pct"] = _ratio(dy / 100 * mcap, ni, scale=100)
    return df


def load_skipped() -> pd.DataFrame | None:
    """Tickers the build skipped (EDGAR/prices unreachable, etc.)."""
    return _load("skipped.csv")


def load_events() -> pd.DataFrame | None:
    return _load("events.csv", parse_dates=["date"])


def load_filings() -> pd.DataFrame | None:
    """Most recent 10-K/10-Q filings per ticker, with direct links to SEC EDGAR."""
    return _load("filings.csv")


def load_annual() -> pd.DataFrame | None:
    """Last fiscal years per ticker: revenue, earnings, cash flow, balance sheet."""
    return _load("financials_annual.csv")


def load_cagr_detail() -> pd.DataFrame | None:
    """
    Every CAGR with the two endpoints it comes from, so it can be recomputed
    by hand. Produced by build_dataset.py as data/cagr_detail.csv.
    """
    return _load("cagr_detail.csv")


# ===========================================================================
# METRIC DEFINITIONS — one place for label, formula, formatting and polarity
# ===========================================================================

# 'better': "high" = a higher value is better, "low" = lower is better,
#           None   = neither (no colour, no peer comparison judgement).
# 'peer'  : False disables the comparison against the peer group. Set on every
#           ABSOLUTE amount — market cap, revenue, net income, free cash flow,
#           debt. Saying a company's revenue is "+30% vs peers" only says it is
#           bigger, and size is not a quality: it is the same information the
#           user explicitly did not want next to market cap. What does compare
#           meaningfully is the ratios built from those amounts (margins, FCF
#           yield, debt/equity), which keep their delta.
# 'kind'  : how to format, and how to express the gap against the peer group.
#           A percentage is compared in PERCENTAGE POINTS (a ROE of 12% vs an
#           industry at 9% is "+3 pp", not "+33%"), a ratio in relative terms
#           (a P/E of 20 vs an industry at 25 is "-20%"). Mixing the two is the
#           quickest way to make a comparison table lie.
#           money = compact currency (12.9 B$) · pct = percentage · ratio = plain
#           number · usd = a PER-SHARE amount, which is money but must never be
#           abbreviated ("$19.82", never "20 $") · count = a quantity of things,
#           shares in particular, scaled but with no currency symbol.
# 'group' : which statement the figure belongs to — market · income · balance ·
#           cash · returns · growth. It is what drives the layout of the
#           Financials tab: sections are generated from this, so adding a metric
#           here makes it appear in the right place with no further edit.
#
# ADDING A METRIC. Put it in this dictionary with a label, a kind, a polarity
# and a 'help' written in the three-part shape used throughout — what it is,
# the formula, how to read it — then list its key in the relevant section of
# the Financials tab. Everything else (formatting, peer median, delta, colour)
# follows automatically.
METRICS: dict[str, dict] = {

    # ------------------------------------------------------------------ #
    # PRICE — what the market is charging                                 #
    # ------------------------------------------------------------------ #
    "market_cap": dict(
        label="Market cap", kind="money", better=None, peer=False,
        group="market",
        help="**What it is** — the price tag on all the shares put together.\n\n"
             "**Formula** — share price × shares outstanding.\n\n"
             "**How to read it** — size, nothing more. It is not compared with "
             "the peer group because being bigger is not the same as being "
             "better."),
    "enterprise_value": dict(
        label="Enterprise value", kind="money", better=None, peer=False,
        group="market",
        help="**What it is** — what the whole business costs, not just its "
             "shares: buying the equity also means taking on the debt and "
             "getting the cash.\n\n"
             "**Formula** — market cap + total debt − cash.\n\n"
             "**How to read it** — two companies with the same market cap do "
             "not cost the same if one carries debt and the other is sitting "
             "on cash."),
    "pe_ratio": dict(
        label="P/E (trailing)", kind="ratio", better="low", group="market",
        help="**What it is** — how many years of current earnings you are "
             "paying for one share.\n\n"
             "**Formula** — price ÷ trailing-twelve-month EPS.\n\n"
             "**How to read it** — lower is cheaper, but only against the same "
             "kind of company: 30 is expensive for a utility and cheap for "
             "software. Recomputed here from the EPS shown on this page, never "
             "copied from the provider, so it always matches the rest."),
    "forward_pe": dict(
        label="P/E (forward)", kind="ratio", better="low", group="market",
        help="**What it is** — the same ratio, but on the earnings analysts "
             "*expect* over the next twelve months.\n\n"
             "**Formula** — price ÷ consensus next-twelve-month EPS.\n\n"
             "**How to read it** — the only forward-looking number on this "
             "page. Well below the trailing P/E means the market is pricing in "
             "a jump in profits; it is an expectation, not a filed figure."),
    "earnings_yield_pct": dict(
        label="Earnings yield", kind="pct", better="high", group="market",
        help="**What it is** — the P/E turned upside down, so it can be "
             "compared with a bond yield.\n\n"
             "**Formula** — 100 ÷ P/E, i.e. EPS ÷ price.\n\n"
             "**How to read it** — the profit the business earns each year for "
             "every $100 of share price. Under the yield of a government bond, "
             "you are paying for growth you have not seen yet."),
    "ps_ratio": dict(
        label="P/S", kind="ratio", better="low", group="market",
        help="**What it is** — the price paid for each unit of sales.\n\n"
             "**Formula** — market cap ÷ trailing-twelve-month revenue.\n\n"
             "**How to read it** — the one multiple that still works when "
             "profits are temporarily depressed or negative. Only comparable "
             "between companies with similar margins: 1× sales is dear for a "
             "supermarket and cheap for software."),
    "pfcf_ratio": dict(
        label="P/FCF", kind="ratio", better="low", group="market",
        help="**What it is** — the P/E's cash-based twin: the price paid for "
             "each unit of cash the business actually produces.\n\n"
             "**Formula** — market cap ÷ trailing-twelve-month free cash "
             "flow.\n\n"
             "**How to read it** — much higher than the P/E means the reported "
             "profit is not turning into cash — the gap is where accounting "
             "choices hide."),
    "price_to_book": dict(
        label="P/B", kind="ratio", better="low", group="market",
        help="**What it is** — the price paid for each unit of accounting net "
             "worth.\n\n"
             "**Formula** — price ÷ book value per share.\n\n"
             "**How to read it** — below 1 the market values the company at "
             "less than its own balance sheet, which is what Lynch called an "
             "*asset play*. Meaningful for banks and industrials, close to "
             "useless where the real assets are brands and people."),
    "ev_to_ebit": dict(
        label="EV / EBIT", kind="ratio", better="low", group="market",
        help="**What it is** — the debt-aware P/E: the price of the whole "
             "business against what the business earns before financing.\n\n"
             "**Formula** — enterprise value ÷ trailing-twelve-month operating "
             "income.\n\n"
             "**How to read it** — the fairest way to compare two companies "
             "with very different debt loads, because both the numerator and "
             "the denominator ignore who financed what."),
    "peg_ratio": dict(
        label="PEG", kind="ratio", better="low", group="market",
        help="**What it is** — the P/E measured against the growth that is "
             "supposed to justify it.\n\n"
             "**Formula** — P/E ÷ 5-year earnings growth rate.\n\n"
             "**How to read it** — below 1 cheap for the growth on offer, "
             "above 2 expensive. Meaningless when growth is negative, and "
             "therefore left blank."),
    "fcf_yield_pct": dict(
        label="FCF yield", kind="pct", better="high", group="market",
        help="**What it is** — the cash return the business throws off at "
             "today's price, the way a rental yield works for a flat.\n\n"
             "**Formula** — free cash flow (TTM) ÷ market cap.\n\n"
             "**How to read it** — higher is cheaper. It is the honest "
             "counterpart of the earnings yield, because cash cannot be "
             "smoothed by accounting choices."),
    "dividend_yield": dict(
        label="Dividend yield", kind="pct", better="high", group="market",
        help="**What it is** — the part of the return that is paid to you in "
             "cash rather than left inside the company, at the rate the "
             "company is paying **now**.\n\n"
             "**Formula** — the last regular dividend × how many times a year "
             "it is paid, ÷ price. Rebuilt from the actual payment history, "
             "not taken from the provider's field: that field keeps the old "
             "figure for a year after a cut, and swallows one-off special "
             "dividends as if they came back every year.\n\n"
             "**How to read it** — this is the forward-looking rate: a cut "
             "shows up the day it happens. Read it together with the payout "
             "ratio — a high yield paid out of an unsustainable share of "
             "profits is a cut waiting to happen — and with the trailing "
             "figure next to it, which is what was actually paid."),
    "dividend_ttm_yield_pct": dict(
        label="Dividend paid (12m)", kind="pct", better="high", group="market",
        help="**What it is** — everything the company actually distributed "
             "over the last twelve months, special dividends included.\n\n"
             "**Formula** — sum of the dividends paid in the last 365 days ÷ "
             "price.\n\n"
             "**How to read it** — the counterpart of the yield next to it. "
             "Much **higher** means a special dividend was paid, or the "
             "dividend has just been cut; much **lower** means it has just "
             "been raised or reinstated. Specials are excluded from the "
             "headline yield on purpose: by definition they do not repeat."),
    "dividend_rate": dict(
        label="Dividend / share", kind="usd", better="high", peer=False,
        group="market",
        help="**What it is** — the annual dividend one share is entitled to at "
             "the current rate.\n\n"
             "**Formula** — the last regular payment × the number of payments "
             "per year.\n\n"
             "**How to read it** — the number the yield is built from: "
             "dividend ÷ price = yield. Compare it with EPS and with free cash "
             "flow per share to see how much room the payout has."),
    "payout_ratio_pct": dict(
        label="Payout ratio", kind="pct", better=None, group="market",
        help="**What it is** — the slice of profit handed to shareholders as "
             "dividends instead of being reinvested.\n\n"
             "**Formula** — dividends paid (dividend yield × market cap) ÷ net "
             "income (TTM).\n\n"
             "**How to read it** — under 60% comfortable, over 100% the "
             "company is paying more than it earns and is funding the "
             "dividend from cash or debt. Neither high nor low is "
             "intrinsically good: a growing company that pays nothing is doing "
             "the right thing. Blank when there are no profits to share."),
    "beta": dict(
        label="Beta", kind="ratio", better=None, group="market",
        help="**What it is** — how violently the stock moves compared with the "
             "market as a whole.\n\n"
             "**Formula** — regression of the stock's returns on the market's "
             "(provider data).\n\n"
             "**How to read it** — 1 means it moves with the index, 1.5 that "
             "it exaggerates every move by half, below 1 that it is calmer. It "
             "measures volatility, not risk of loss."),

    # ------------------------------------------------------------------ #
    # INCOME STATEMENT — what the company sells and keeps                 #
    # ------------------------------------------------------------------ #
    "revenue_ttm": dict(
        label="Revenue (TTM)", kind="money", better="high", peer=False,
        group="income",
        help="**What it is** — everything the company billed its customers in "
             "the last twelve months.\n\n"
             "**Formula** — the last four discrete quarters filed with the "
             "SEC, added up.\n\n"
             "**How to read it** — the top line: every margin below is a "
             "fraction of this number. Not compared with peers — size is not a "
             "quality."),
    "revenue_latest_fy": dict(
        label="Revenue (latest FY)", kind="money", better="high", peer=False,
        group="income",
        help="**What it is** — revenue for the most recent *complete* fiscal "
             "year, as it appears in the annual report.\n\n"
             "**Formula** — straight from the 10-K income statement.\n\n"
             "**How to read it** — the audited figure. It differs from the TTM "
             "number by however many quarters have been filed since the year "
             "ended."),
    "ebit_ttm": dict(
        label="EBIT (TTM)", kind="money", better="high", peer=False,
        group="income",
        help="**What it is** — the profit the business makes from operating, "
             "before interest and taxes get their share.\n\n"
             "**Formula** — operating income over the last four quarters; "
             "pre-tax income where EBIT is not tagged separately (banks).\n\n"
             "**How to read it** — the cleanest measure of the trading "
             "performance, because it is not affected by how the company is "
             "financed or where it pays tax."),
    "net_income_ttm": dict(
        label="Net income (TTM)", kind="money", better="high", peer=False,
        group="income",
        help="**What it is** — the bottom line: what is left for shareholders "
             "after every cost, interest and tax.\n\n"
             "**Formula** — the last four discrete quarters filed with the "
             "SEC, added up.\n\n"
             "**How to read it** — the numerator of the P/E. It is also the "
             "figure most exposed to one-off items, which is why the page also "
             "shows a normalized version."),
    "eps_ttm": dict(
        label="EPS (TTM)", kind="usd", better="high", peer=False,
        group="income",
        help="**What it is** — the profit attributable to one single share.\n\n"
             "**Formula** — four discrete quarterly EPS figures added up, "
             "restated for stock splits, after arbitration between the SEC "
             "filings and the provider.\n\n"
             "**How to read it** — the number this whole page is built on: the "
             "fair value multiplies it, the P/E divides by it."),
    "operating_margin_pct": dict(
        label="Operating margin", kind="pct", better="high", group="income",
        help="**What it is** — how many cents of each dollar of sales survive "
             "the cost of running the business.\n\n"
             "**Formula** — EBIT ÷ revenue, both trailing twelve months.\n\n"
             "**How to read it** — the best single indicator of pricing power. "
             "Compare it only within an industry, and watch its direction over "
             "the years more than its level."),
    "net_margin_pct": dict(
        label="Net margin", kind="pct", better="high", group="income",
        help="**What it is** — how many cents of each dollar of sales end up as "
             "profit for the shareholder.\n\n"
             "**Formula** — net income ÷ revenue, both trailing twelve "
             "months.\n\n"
             "**How to read it** — the operating margin after interest and "
             "tax. A wide gap between the two says the debt or the tax bill is "
             "eating the business's own performance."),
    "revenue_growth_yoy_pct": dict(
        label="Revenue growth YoY", kind="pct", better="high", group="income",
        help="**What it is** — how much bigger the top line is than a year "
             "ago.\n\n"
             "**Formula** — trailing-twelve-month revenue against the point "
             "closest to twelve months earlier (compared by date, not by "
             "position in the series).\n\n"
             "**How to read it** — the fastest-reacting growth figure on the "
             "page, and the noisiest: one strong quarter moves it."),
    "eps_growth_yoy": dict(
        label="EPS growth YoY", kind="pct", better="high", group="income",
        help="**What it is** — how much more the company earns per share than "
             "a year ago.\n\n"
             "**Formula** — trailing-twelve-month EPS against the point "
             "closest to twelve months earlier.\n\n"
             "**How to read it** — persistently faster than revenue growth "
             "means margins are widening or the share count is shrinking; "
             "persistently slower means the opposite."),
    "revenue_per_share": dict(
        label="Revenue / share", kind="usd", better="high", peer=False,
        group="income",
        help="**What it is** — the sales that belong to one share.\n\n"
             "**Formula** — trailing-twelve-month revenue ÷ shares "
             "outstanding.\n\n"
             "**How to read it** — the per-share view is the only one that "
             "sees dilution: revenue that grows while the share count grows "
             "faster leaves the individual shareholder with less."),
    "shares_outstanding": dict(
        label="Shares outstanding", kind="count", better="low", peer=False,
        group="income",
        help="**What it is** — how many slices the company is cut into.\n\n"
             "**Formula** — the latest share count from the provider.\n\n"
             "**How to read it** — falling over the years means buybacks are "
             "concentrating the profit on fewer shares; rising means dilution, "
             "which quietly eats the growth of every per-share figure."),

    # ------------------------------------------------------------------ #
    # BALANCE SHEET — what it owns and owes                               #
    # ------------------------------------------------------------------ #
    "assets": dict(
        label="Total assets", kind="money", better=None, peer=False,
        group="balance",
        help="**What it is** — everything the company owns, at the value "
             "carried in its books.\n\n"
             "**Formula** — the latest filed balance sheet.\n\n"
             "**How to read it** — on its own it says only how capital-heavy "
             "the business is. What matters is how much of it is financed by "
             "the owners (equity ratio) and what it earns (earning power)."),
    "equity": dict(
        label="Shareholders' equity", kind="money", better="high", peer=False,
        group="balance",
        help="**What it is** — the part of the assets that belongs to the "
             "shareholders once every creditor has been paid.\n\n"
             "**Formula** — total assets − total liabilities, from the latest "
             "filed balance sheet.\n\n"
             "**How to read it** — the accounting floor under the share price, "
             "and the denominator of ROE. Negative equity is not automatically "
             "fatal, but it makes ROE and debt/equity unreadable."),
    "cash": dict(
        label="Cash & equivalents", kind="money", better="high", peer=False,
        group="balance",
        help="**What it is** — money available immediately, plus what can be "
             "turned into money within days.\n\n"
             "**Formula** — the cash line of the latest filed balance "
             "sheet.\n\n"
             "**How to read it** — the buffer that lets a company survive a "
             "bad year without asking anyone's permission. Only meaningful "
             "next to the debt it might have to repay."),
    "total_debt": dict(
        label="Total debt", kind="money", better="low", peer=False,
        group="balance",
        help="**What it is** — every dollar the company has borrowed, whenever "
             "it falls due.\n\n"
             "**Formula** — long-term debt + the current portion and "
             "short-term borrowings, from the latest filed balance sheet.\n\n"
             "**How to read it** — this, not the long-term figure, is the "
             "number that matters: modest long-term debt next to a pile of "
             "commercial paper maturing this year is not a low-debt company."),
    "long_term_debt": dict(
        label="Long term debt", kind="money", better="low", peer=False,
        group="balance",
        help="**What it is** — the borrowings that do not fall due within the "
             "year.\n\n"
             "**Formula** — the non-current debt line of the latest filed "
             "balance sheet; where a company only tags debt including current "
             "maturities, that broader figure is used.\n\n"
             "**How to read it** — the patient part of the debt: it does not "
             "create refinancing pressure this year, but it still costs "
             "interest."),
    "net_debt": dict(
        label="Net debt", kind="money", better="low", peer=False,
        group="balance",
        help="**What it is** — the debt that would still be there if the "
             "company spent all its cash repaying borrowings tomorrow.\n\n"
             "**Formula** — total debt − cash & equivalents.\n\n"
             "**How to read it** — negative is a *net cash* position: the "
             "company owes less than it holds."),
    "equity_ratio_pct": dict(
        label="Equity ratio", kind="pct", better="high", group="balance",
        help="**What it is** — how much of the balance sheet is genuinely "
             "owned rather than borrowed.\n\n"
             "**Formula** — shareholders' equity ÷ total assets.\n\n"
             "**How to read it** — higher means more shock absorption. "
             "Structurally low for banks, whose business *is* lending other "
             "people's money — which is why the comparison shown is against "
             "the same industry, never the whole market."),
    "debt_to_equity": dict(
        label="Debt / equity", kind="ratio", better="low", group="balance",
        help="**What it is** — how many dollars the company has borrowed for "
             "every dollar the shareholders put in.\n\n"
             "**Formula** — long-term debt ÷ shareholders' equity.\n\n"
             "**How to read it** — the classic leverage gauge. Above 1 the "
             "lenders have committed more than the owners; what counts as "
             "normal varies enormously by industry."),
    "net_debt_to_equity": dict(
        label="Net debt / equity", kind="ratio", better="low", group="balance",
        help="**What it is** — the same leverage test, but crediting the "
             "company for the cash it is holding.\n\n"
             "**Formula** — net debt ÷ shareholders' equity.\n\n"
             "**How to read it** — the fairer of the two for cash-rich "
             "companies. Negative means net cash: more money in the bank than "
             "borrowings outstanding."),
    "debt_to_assets_pct": dict(
        label="Debt / assets", kind="pct", better="low", group="balance",
        help="**What it is** — the share of everything the company owns that "
             "was paid for with borrowed money.\n\n"
             "**Formula** — total debt ÷ total assets.\n\n"
             "**How to read it** — the mirror image of the equity ratio, and "
             "readable even where equity is distorted by buybacks or "
             "write-downs."),
    "net_debt_to_fcf": dict(
        label="Net debt / FCF", kind="ratio", better="low", group="balance",
        help="**What it is** — how many years of free cash flow it would take "
             "to repay the net debt, if every spare dollar went to the "
             "lenders.\n\n**Formula** — net debt ÷ free cash flow (TTM).\n\n"
             "**How to read it** — the harshest of the debt tests, and the "
             "most honest: interest is paid with cash, not with operating "
             "profit. Under 3 the debt is comfortably serviceable, above 5 it "
             "is running the company. Blank when free cash flow is negative — "
             "a company burning cash cannot repay anything out of it."),
    "cash_to_debt_pct": dict(
        label="Cash / debt", kind="pct", better="high", group="balance",
        help="**What it is** — how much of the debt could be repaid tomorrow "
             "morning out of the money already in the bank.\n\n"
             "**Formula** — cash & equivalents ÷ total debt.\n\n"
             "**How to read it** — above 100% the company holds more cash than "
             "it owes (net cash). Low is not automatically bad, but it means "
             "the debt has to be repaid out of future profits rather than out "
             "of the balance sheet."),
    "net_debt_to_ebit": dict(
        label="Net debt / EBIT", kind="ratio", better="low", group="balance",
        help="**What it is** — how many years of operating profit it would "
             "take to clear the net debt.\n\n"
             "**Formula** — net debt ÷ trailing-twelve-month EBIT.\n\n"
             "**How to read it** — under 3 comfortable, above 4 the balance "
             "sheet starts dictating the strategy. This is the ratio bank "
             "covenants are usually written on."),
    "book_value_per_share": dict(
        label="Book value / share", kind="usd", better="high", peer=False,
        group="balance",
        help="**What it is** — the accounting net worth attached to one "
             "share.\n\n"
             "**Formula** — shareholders' equity ÷ shares outstanding.\n\n"
             "**How to read it** — for a Lynch asset play this, not the P/E, "
             "is the valuation anchor: the price is compared with the balance "
             "sheet rather than with the earnings."),
    "asset_turnover": dict(
        label="Asset turnover", kind="ratio", better="high", group="balance",
        help="**What it is** — how hard the assets are working: the sales "
             "produced by each dollar on the balance sheet.\n\n"
             "**Formula** — revenue (TTM) ÷ total assets.\n\n"
             "**How to read it** — a retailer turns its assets over several "
             "times a year, a utility a fraction of once. Rising over time "
             "means the same asset base is doing more work."),

    # ------------------------------------------------------------------ #
    # CASH FLOW — what actually reaches the bank account                  #
    # ------------------------------------------------------------------ #
    "ocf_ttm": dict(
        label="Operating cash flow (TTM)", kind="money", better="high",
        peer=False, group="cash",
        help="**What it is** — the cash the ordinary business generated, "
             "before deciding what to invest.\n\n"
             "**Formula** — the last four quarters. EDGAR reports cash flow "
             "year-to-date rather than per quarter, so quarters are recovered "
             "by differencing consecutive cumulative figures.\n\n"
             "**How to read it** — profit is an opinion, this is closer to a "
             "fact. It should track net income over the years; a lasting gap "
             "deserves an explanation."),
    "capex_ttm": dict(
        label="Capital expenditure (TTM)", kind="money", better=None,
        peer=False, group="cash",
        help="**What it is** — the cash spent on plant, equipment and other "
             "long-lived assets, shown as a positive outflow.\n\n"
             "**Formula** — operating cash flow − free cash flow, both "
             "trailing twelve months.\n\n"
             "**How to read it** — neither good nor bad in itself: it is the "
             "cost of staying in business, and for a growing company also the "
             "cost of getting bigger. Judge it as a share of revenue."),
    "fcf_ttm": dict(
        label="Free cash flow (TTM)", kind="money", better="high", peer=False,
        group="cash",
        help="**What it is** — the cash left over once the business has paid "
             "for everything it needs to keep running: the money that can pay "
             "dividends, repay debt or buy back shares.\n\n"
             "**Formula** — operating cash flow − capital expenditure, over "
             "the last four quarters.\n\n"
             "**How to read it** — the number a business is ultimately worth "
             "the discounted sum of. Missing for most banks and insurers, "
             "whose cash flow statement has a different shape."),
    "fcf_latest_fy": dict(
        label="FCF (latest FY)", kind="money", better="high", peer=False,
        group="cash",
        help="**What it is** — free cash flow for the most recent *complete* "
             "fiscal year.\n\n"
             "**Formula** — straight from the annual cash flow statement.\n\n"
             "**How to read it** — the audited counterpart of the TTM figure, "
             "and the one to prefer for businesses with heavy seasonality."),
    "fcf_per_share": dict(
        label="FCF / share", kind="usd", better="high", peer=False,
        group="cash",
        help="**What it is** — the free cash flow that belongs to one "
             "share.\n\n"
             "**Formula** — trailing-twelve-month free cash flow ÷ shares "
             "outstanding.\n\n"
             "**How to read it** — the cash equivalent of EPS. Compare it with "
             "the dividend per share to see how much room the payout has."),
    "fcf_margin_pct": dict(
        label="FCF margin", kind="pct", better="high", group="cash",
        help="**What it is** — how many cents of each dollar of sales end up as "
             "genuinely free cash.\n\n"
             "**Formula** — free cash flow ÷ revenue, both trailing twelve "
             "months.\n\n"
             "**How to read it** — the single hardest margin to manipulate. "
             "Above 15% is a business that finances itself; near zero means "
             "growth has to be paid for with debt or new shares."),
    "ocf_margin_pct": dict(
        label="OCF margin", kind="pct", better="high", group="cash",
        help="**What it is** — the share of sales that turns into operating "
             "cash before any investment.\n\n"
             "**Formula** — operating cash flow ÷ revenue, both trailing "
             "twelve months.\n\n"
             "**How to read it** — read next to the FCF margin: the distance "
             "between the two is exactly how capital-hungry the business is."),
    "fcf_conversion_pct": dict(
        label="FCF conversion", kind="pct", better="high", group="cash",
        help="**What it is** — how much of the accounting profit actually "
             "shows up as cash.\n\n"
             "**Formula** — free cash flow ÷ net income, both trailing twelve "
             "months.\n\n"
             "**How to read it** — around 100% the earnings are real. "
             "Persistently below 60% means profit is being tied up in working "
             "capital or capex; above 100% is common where depreciation "
             "exceeds the cash actually being reinvested. Blank when there is "
             "no profit to convert."),
    "capex_to_revenue_pct": dict(
        label="Capex / revenue", kind="pct", better=None, group="cash",
        help="**What it is** — how much of every dollar of sales has to be "
             "ploughed back into assets.\n\n"
             "**Formula** — capital expenditure ÷ revenue, both trailing "
             "twelve months.\n\n"
             "**How to read it** — low means an asset-light business that can "
             "grow without swallowing cash; high is normal for utilities, "
             "telecoms and heavy industry. Compare only within an industry."),
    "fcf_growth_yoy_pct": dict(
        label="FCF growth YoY", kind="pct", better="high", group="cash",
        help="**What it is** — how much more (or less) free cash the business "
             "produced than in the previous year.\n\n"
             "**Formula** — the latest full fiscal year against the one "
             "before; falls back to the trailing-twelve-month series when only "
             "one fiscal year is on file.\n\n"
             "**How to read it** — the most volatile growth figure on the "
             "page: one large investment year is enough to turn it negative "
             "without anything being wrong."),

    # ------------------------------------------------------------------ #
    # RETURNS — how well the capital is being used                        #
    # ------------------------------------------------------------------ #
    "roe_pct": dict(
        label="ROE", kind="pct", better="high", group="returns",
        help="**What it is** — the return the company earns on the money the "
             "shareholders have left in it.\n\n"
             "**Formula** — net income (TTM) ÷ **average** equity over the "
             "same twelve months — average, not year-end, because a flow "
             "divided by a closing snapshot flatters companies that have just "
             "bought back stock.\n\n"
             "**How to read it** — above 15% sustained is a genuinely good "
             "business. But debt inflates it: always read it next to ROIC and "
             "the equity ratio."),
    "roic_pct": dict(
        label="ROIC", kind="pct", better="high", group="returns",
        help="**What it is** — the return on *all* the capital at work, "
             "borrowed as well as owned. The one profitability measure "
             "leverage cannot flatter.\n\n"
             "**Formula** — EBIT × (1 − 21%) ÷ (equity + total debt − cash). "
             "The 21% US federal rate is applied to every company rather than "
             "each one's own effective rate, which the dataset does not "
             "carry — comparable beats individually exact.\n\n"
             "**How to read it** — compare it with what the capital costs "
             "(roughly 8–10%): above it the company creates value by growing, "
             "below it growth destroys value. Blank when invested capital is "
             "negative."),
    "earning_power_pct": dict(
        label="Earning power", kind="pct", better="high", group="returns",
        help="**What it is** — what the assets earn before financing and tax "
             "get involved. Graham's *earning power* test.\n\n"
             "**Formula** — EBIT (TTM) ÷ total assets; pre-tax income where "
             "EBIT is not tagged (banks).\n\n"
             "**How to read it** — comparable across companies with completely "
             "different debt and tax situations, because it ignores both."),

    # ------------------------------------------------------------------ #
    # GROWTH — the five-year track record                                 #
    # ------------------------------------------------------------------ #
    "cagr_revenue_5y": dict(
        label="Revenue CAGR 5y", kind="pct", better="high", group="growth",
        help="**What it is** — the average yearly rate at which sales have "
             "compounded over five years.\n\n"
             "**Formula** — ((value now ÷ value five years ago) ^ (1/5)) − 1, "
             "on the trailing-twelve-month series.\n\n"
             "**How to read it** — the most reliable growth line, because "
             "revenue is the hardest number to massage. The two endpoints "
             "behind it are shown on the Growth tab."),
    "cagr_eps_5y": dict(
        label="EPS CAGR 5y", kind="pct", better="high", group="growth",
        help="**What it is** — the average yearly rate at which earnings per "
             "share have compounded over five years.\n\n"
             "**Formula** — ((value now ÷ value five years ago) ^ (1/5)) − 1, "
             "on the trailing-twelve-month series.\n\n"
             "**How to read it** — this is the growth the fair multiple is "
             "built on. Left blank when the starting value was a loss: a "
             "compound rate from a negative base means nothing."),
    "cagr_net_income_5y": dict(
        label="Net income CAGR 5y", kind="pct", better="high", group="growth",
        help="**What it is** — five-year compound growth of total profit.\n\n"
             "**Formula** — ((value now ÷ value five years ago) ^ (1/5)) − 1, "
             "on the trailing-twelve-month series.\n\n"
             "**How to read it** — compare it with the EPS rate: EPS growing "
             "faster means buybacks are helping, slower means shares are being "
             "issued."),
    "cagr_fcf_5y": dict(
        label="FCF CAGR 5y", kind="pct", better="high", group="growth",
        help="**What it is** — five-year compound growth of free cash "
             "flow.\n\n"
             "**Formula** — ((value now ÷ value five years ago) ^ (1/5)) − 1, "
             "on the trailing-twelve-month series.\n\n"
             "**How to read it** — the acid test of the earnings growth next "
             "to it. Profits that rise while cash does not are the classic "
             "early warning."),
    "cagr_ocf_5y": dict(
        label="Op. cash flow CAGR 5y", kind="pct", better="high",
        group="growth",
        help="**What it is** — five-year compound growth of operating cash "
             "flow.\n\n"
             "**Formula** — ((value now ÷ value five years ago) ^ (1/5)) − 1, "
             "on the trailing-twelve-month series.\n\n"
             "**How to read it** — steadier than free cash flow, which a "
             "single heavy investment year can distort, so it is the better "
             "read on the underlying trend."),
}

CAGR_METRICS = ["cagr_eps", "cagr_revenue", "cagr_fcf", "cagr_net_income", "cagr_ocf"]
CAGR_LABELS = {"cagr_eps": "EPS", "cagr_revenue": "Revenue", "cagr_fcf": "Free cash flow",
               "cagr_net_income": "Net income", "cagr_ocf": "Operating cash flow"}
CAGR_HORIZONS = [3, 5, 10]


# ===========================================================================
# FORMATTING
# ===========================================================================

def fmt_money(v, digits: int = 1) -> str:
    """
    Compact money: 12.9 B$, 491 M$, -3.2 B$.

    The decimal is dropped above 100 of any unit: "491 M$" carries the same
    information as "491.0 M$" and reads faster in a dense grid.
    """
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    a = abs(v)
    for unit, scale in (("T$", 1e12), ("B$", 1e9), ("M$", 1e6), ("K$", 1e3)):
        if a >= scale:
            scaled = v / scale
            return f"{scaled:,.{0 if abs(scaled) >= 100 else digits}f} {unit}"
    return f"{v:,.0f} $"


def fmt_pct(v, digits: int = 1) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{v:,.{digits}f}%"


def fmt_ratio(v, digits: int = 1) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{v:,.{digits}f}"


def fmt_usd(v, digits: int = 2) -> str:
    """
    Un importo PER AZIONE: "$19.82".

    Non passa da fmt_money di proposito. Abbreviare un valore per azione lo
    rende illeggibile — un EPS di 19,82 dollari stampato "20 $" perde
    esattamente la precisione che serve, visto che il fair value nasce da una
    moltiplicazione su quel numero.
    """
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"${v:,.{digits}f}"


def fmt_count(v) -> str:
    """Una quantita' di cose (azioni): 7.43 B, 512 M. Scalata, senza valuta."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    a = abs(v)
    for unit, scale in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if a >= scale:
            s = v / scale
            return f"{s:,.{0 if abs(s) >= 100 else 2}f} {unit}"
    return f"{v:,.0f}"


_FORMATTERS = {"money": fmt_money, "pct": fmt_pct, "ratio": fmt_ratio,
               "usd": fmt_usd, "count": fmt_count}


def fmt_metric(key: str, v) -> str:
    kind = METRICS.get(key, {}).get("kind", "ratio")
    return _FORMATTERS.get(kind, fmt_ratio)(v)


def _num(v):
    """Value as float, or None when missing/not numeric."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


# ===========================================================================
# PEER GROUP (industry) STATISTICS
# ===========================================================================

@st.cache_data(show_spinner=False)
def peer_medians(fund_df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    """
    Median of every comparable metric within each peer group, plus the group's
    size.

    The MEDIAN, not the mean: valuation multiples have a long right tail (one
    company at a P/E of 900 drags a 20-stock mean up by 45 points) and the mean
    would describe that outlier rather than the group. The count travels with
    the median so the interface can refuse to compare against a group of two.
    """
    cols = [k for k, m in METRICS.items()
            if m.get("peer", True) and k in fund_df.columns]
    grouped = fund_df.groupby(group_col)
    out = grouped[cols].median(numeric_only=True)
    out["_n"] = grouped.size()
    return out


def peer_reference(row: pd.Series, ind_med: pd.DataFrame,
                   sec_med: pd.DataFrame) -> tuple[pd.Series | None, str, int]:
    """
    Chooses the benchmark for one company: its industry when that group is big
    enough, otherwise its sector, otherwise nothing.

    Returns (medians, label, peer count).
    """
    ind = row.get("industry")
    if ind in ind_med.index and ind_med.loc[ind, "_n"] >= MIN_PEERS:
        return ind_med.loc[ind], f"industry «{ind}»", int(ind_med.loc[ind, "_n"])
    sec = row.get("sector")
    if sec in sec_med.index and sec_med.loc[sec, "_n"] >= MIN_PEERS:
        return (sec_med.loc[sec], f"{sec} sector — the «{ind}» industry has "
                f"too few companies here", int(sec_med.loc[sec, "_n"]))
    return None, "no comparable peer group", 0


def peer_gap(key: str, value, peer_value) -> tuple[float | None, str]:
    """
    Gap between a company's metric and its peer group.

    Percentages are compared in PERCENTAGE POINTS, ratios and money in relative
    terms. Returns (gap, formatted text); the sign is always "company minus
    peers".
    """
    v, p = _num(value), _num(peer_value)
    if v is None or p is None:
        return None, ""
    kind = METRICS.get(key, {}).get("kind", "ratio")
    if kind == "pct":
        gap = v - p
        return gap, f"{gap:+,.1f} pp vs peers"
    if p == 0:
        return None, ""
    gap = (v / p - 1) * 100
    return gap, f"{gap:+,.0f}% vs peers"


def delta_color(key: str, gap: float | None) -> str:
    """
    Streamlit paints a positive delta green by default. For "lower is better"
    metrics (P/E, debt) that is exactly backwards, so those get "inverse".
    """
    better = METRICS.get(key, {}).get("better")
    if gap is None or better is None:
        return "off"
    return "normal" if better == "high" else "inverse"


# ===========================================================================
# LOAD + HEADER
# ===========================================================================

hist = load_history()
fund = load_fundamentals()
events = load_events()
skipped_tk = load_skipped()
filings = load_filings()
annual = load_annual()
cagr_detail = load_cagr_detail()

st.title("📈 Lynch Valuation")
st.caption("Real price vs *fair value* (TTM earnings × P/E), with the financial "
           "statements behind it. Below the line = potentially undervalued · "
           "above = overvalued.")

if hist is None or fund is None:
    st.error("Missing data. Run this first:  `python build_dataset.py`")
    st.stop()

has_industry = "industry" in fund.columns
ind_med = peer_medians(fund, "industry") if has_industry else pd.DataFrame()
sec_med = peer_medians(fund, "sector") if "sector" in fund.columns else pd.DataFrame()

# --- data freshness -------------------------------------------------------
_hp = next((DATA_DIR / n for n in ("history.csv.gz", "history.csv")
            if (DATA_DIR / n).exists()), DATA_DIR / "history.csv")
_stamp = datetime.fromtimestamp(_hp.stat().st_mtime).strftime("%b %d, %Y %H:%M")
_n_tick = fund["ticker"].nunique()
_c1, _c2 = st.columns([4, 1])
with _c1:
    _ind_txt = f" · {fund['industry'].nunique()} industries" if has_industry else ""
    st.caption(f"**{_n_tick} tickers**{_ind_txt} on file · updated {_stamp}")
    if _n_tick < 50:
        st.caption("⚠️ This looks like a test dataset. For the whole US market, "
                   "run the Action with **scope = us-all**.")
with _c2:
    if st.button("↻ Reload data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# Consistency check between the two files. A negative historical EPS is NOT by
# itself a problem — plenty of companies genuinely lose money, and on the full
# US market thousands do. It is only suspicious when the fundamentals row says
# this company had NO losses in the last five years: then the two files
# contradict each other, which is the fingerprint of a history rebuilt before
# the stock-split adjustment.
#
# THE WINDOW HAS TO MATCH THE ONE THE BUILD USED, or the check invents
# contradictions. Two traps, both of which produced false alarms on real data:
#   1. Comparing against the whole history flags a company that lost money once a
#      decade ago — Nvidia's 2009 quarter was enough.
#   2. Comparing PRICE dates against the window while the build compares EPS
#      dates: an EPS point just outside the build's window gets carried forward
#      onto prices that fall inside the app's, and the two disagree over nothing.
#      nVent was flagged this way for a loss the build had correctly excluded.
# So the comparison runs on eps_date, per ticker, against that ticker's own last
# EPS date — exactly what had_recent_losses does.
if {"eps", "eps_date"} <= set(hist.columns) and "had_recent_losses" in fund.columns:
    _last = fund.set_index("ticker")["eps_last_date"].to_dict()
    _neg_rows = hist[hist["eps"] < 0][["ticker", "eps_date"]].dropna()
    _ed = pd.to_datetime(_neg_rows["eps_date"], errors="coerce")
    _cut = pd.to_datetime(_neg_rows["ticker"].map(_last), errors="coerce") - \
        pd.DateOffset(years=5)
    _neg = set(_neg_rows.loc[_ed >= _cut, "ticker"].unique())
    _no_loss = set(fund.loc[fund["had_recent_losses"] == False, "ticker"])  # noqa: E712
    _contra = sorted(_neg & _no_loss)
    if _contra:
        st.warning(
            "**Inconsistent data.** These tickers show negative historical EPS "
            "while their summary row reports no losses in the last five years: "
            f"{', '.join(_contra[:8])}"
            f"{f' (+{len(_contra) - 8} more)' if len(_contra) > 8 else ''}. "
            "That contradiction is the fingerprint of CSVs generated before the "
            "stock-split adjustment. Regenerate the data "
            "(`python build_dataset.py`) and press «Reload data»."
        )

# --- navigation -----------------------------------------------------------
# Two important details:
# 1) Widgets not rendered in a run lose their state. Re-writing every key onto
#    itself keeps filters intact when switching views.
# 2) A widget's key can ONLY be changed BEFORE the widget is instantiated:
#    that's why the view-switch request ("_goto_nav") is applied here, at the
#    top of the script.
_PERSIST = ["hist_pe", "hist_years", "hist_scale", "hist_ticker", "hist_eps_basis",
            "screen_model", "screen_val", "screen_sec", "screen_cat", "screen_ind",
            "screen_profit", "screen_search", "screen_mcap", "nav_radio"]
for _k in _PERSIST:
    if _k in st.session_state:
        st.session_state[_k] = st.session_state[_k]


def _dflt(key, value):
    """
    Returns the default value only if the key is NOT already in
    session_state. Passing both `default` and a saved state makes Streamlit
    emit a warning ("created with a default value but also had its value set
    via Session State"): this avoids it while keeping persistence.

    Only for widgets whose "no default" value is a valid state (multiselect:
    None means empty). For selectbox use _init instead — an index of None
    renders an EMPTY selectbox, and the code downstream then works on None.
    """
    return None if key in st.session_state else value


def _init(key, value):
    """Seeds a widget's session_state once, so the widget can be created
    without a default at all — the pattern Streamlit wants when a key is used."""
    if key not in st.session_state:
        st.session_state[key] = value


def _sanitize(key, options: list) -> None:
    """
    Drops from a saved multi-selection anything no longer among the options.

    Streamlit raises when a stored value isn't in the option list, and the
    industry filter changes its options every time the sector filter moves —
    without this, narrowing the sectors crashes the page.
    """
    if key in st.session_state:
        valid = set(options)
        current = st.session_state[key]
        if isinstance(current, list):
            kept = [v for v in current if v in valid]
            if len(kept) != len(current):
                st.session_state[key] = kept


_views = ["Screener", "Details"]
if "nav_radio" not in st.session_state:
    st.session_state["nav_radio"] = "Screener"
# accept the legacy name so an old saved session doesn't land on nothing
if st.session_state.get("nav_radio") == "Historical":
    st.session_state["nav_radio"] = "Details"
if st.session_state.get("_goto_nav"):
    st.session_state["nav_radio"] = st.session_state.pop("_goto_nav")

nav = st.radio("View", _views, horizontal=True, key="nav_radio",
               label_visibility="collapsed")


# ===========================================================================
# SHARED WIDGETS
# ===========================================================================

def metric_grid(items: list[dict], ncols: int = 4) -> None:
    """
    Grid of metric cards. Each item: {label, value, help, delta, delta_color}.
    Laid out in fixed-width rows so the cards line up in a readable table
    rather than a ragged strip.
    """
    for start in range(0, len(items), ncols):
        cols = st.columns(ncols)
        for col, item in zip(cols, items[start:start + ncols]):
            col.metric(item["label"], item["value"],
                       delta=item.get("delta"),
                       delta_color=item.get("delta_color", "off"),
                       help=item.get("help"))


def metric_item(key: str, row: pd.Series, peers: pd.Series | None) -> dict:
    """One metric card, with its gap against the peer group when meaningful."""
    spec = METRICS[key]
    value = row.get(key)
    item = {"label": spec["label"], "value": fmt_metric(key, _num(value)),
            "help": spec["help"]}
    if spec.get("peer", True) and peers is not None and key in peers.index:
        gap, text = peer_gap(key, value, peers.get(key))
        if gap is not None:
            item["delta"] = text
            item["delta_color"] = delta_color(key, gap)
            item["help"] += (f"\n\nPeer median: {fmt_metric(key, _num(peers.get(key)))}.")
    return item


# ===========================================================================
# DETAILS VIEW
# ===========================================================================

def render_normalized_earnings(r: pd.Series, eps_raw: float | None) -> None:
    """
    Utili correnti contro utili normalizzati: perche' un altro sito puo' dare un
    fair value molto diverso.

    Sta nella scheda Valuation, accanto al fair value che spiega: e' una domanda
    sulla valutazione ("perche' altrove leggo un altro numero?"), non sulla
    qualita' del dato. Nella scheda di audit era corretta ma nessuno la trovava.
    """
    norm = _num(r.get("eps_normalized_3y"))
    fvn = _num(r.get("fair_value_norm_pe15"))
    with st.expander("📐 Normalized earnings — why other models give different "
                     "numbers", expanded=False):
        if not (norm and fvn):
            st.caption("Not enough filed history for a 3-year normalization.")
            return
        delta = _num(r.get("eps_vs_normalized_pct"))
        # Lo SCARTO CONTRO IL PREZZO come per gli altri due fair value. Un
        # valore equo senza il confronto con il prezzo e' un numero e basta:
        # sono i due modelli accanto al grafico a dirlo, e questo terzo deve
        # rispondere alla stessa domanda nello stesso modo, o non si puo'
        # mettere in fila con loro.
        price_now = _num(r.get("current_price"))
        disc_n = ((price_now / fvn - 1) * 100) if (fvn and price_now) else None
        cA, cB, cC, cD = st.columns(4)
        cA.metric("TTM EPS (raw)", fmt_usd(eps_raw),
                  help="**What it is** — trailing-twelve-month EPS exactly as "
                       "filed, one-off items included.\n\n**How to read it** — "
                       "this is the figure the rest of the page uses unless you "
                       "switch the EPS basis above.")
        cB.metric("3-year normalized EPS", fmt_usd(norm),
                  help="**What it is** — earnings stripped of the good and bad "
                       "years.\n\n**Formula** — the MEDIAN of the "
                       "trailing-twelve-month EPS over the last 3 years. "
                       "Median, not mean: one corrupted data point would drag a "
                       "mean and produce a fair value in the millions.\n\n"
                       "**How to read it** — the closer it is to the raw EPS, "
                       "the more repeatable the current profit.")
        cC.metric("Normalized fair value P/E15", fmt_usd(fvn),
                  help="**Formula** — 3-year normalized EPS × 15.\n\n"
                       "**How to read it** — the value a model that smooths "
                       "earnings would arrive at. This is usually the number "
                       "other sites are showing when they disagree with us.")
        cD.metric("Price vs normalized fair value",
                  f"{disc_n:+.0f}%" if disc_n is not None else "—",
                  delta=("below fair value" if disc_n is not None and disc_n < 0
                         else "above fair value" if disc_n is not None else None),
                  delta_color=("normal" if (disc_n is not None and disc_n < 0)
                               else "inverse" if disc_n is not None else "off"),
                  help="**Formula** — price ÷ normalized fair value − 1, the "
                       "same comparison shown for the other two fair values "
                       "above.\n\n**How to read it** — when this discount and "
                       "the raw P/E 15 one point in opposite directions, the "
                       "whole verdict on the stock rests on whether the current "
                       "earnings are repeatable.")
        if delta is not None and abs(delta) > 25:
            st.warning(
                f"Current EPS is **{delta:+.0f}%** vs the 3-year median. Such a "
                "wide gap indicates earnings affected by one-off items (gains "
                "on equity stakes, write-downs, cyclical peaks). Models that "
                "*normalize* earnings produce a very different fair value in "
                "these cases, since we use raw GAAP earnings. Switch the EPS "
                "basis above to see the normalized version of this whole page."
            )
        else:
            st.caption("Current EPS is in line with the 3-year median: the raw "
                       "fair value is reliable.")


def category_header(r: pd.Series) -> None:
    """
    La categoria Lynch, il suo multiplo e QUANTO fidarsi — in testa alla scheda.

    Sta in cima e non in fondo perche' e' la tesi: tutto quello che segue nella
    scheda di valutazione discende da qui. Prima era sotto il grafico e sotto
    sei KPI, cioe' dopo la risposta veniva la domanda.
    """
    cat = r.get("lynch_category")
    if not cat or pd.isna(cat):
        return
    conf = str(r.get("lynch_confidence") or "").lower()
    badge = CONFIDENCE_BADGE.get(conf, "")
    left, right = st.columns([3, 1])
    with left:
        st.markdown(f"### {lynch_icon(cat)} {cat}")
        if pd.notna(r.get("lynch_note")):
            st.caption(str(r.get("lynch_note")))
    with right:
        if badge:
            st.markdown(f"&nbsp;\n\n**{badge}**")
            note = r.get("lynch_confidence_note")
            if pd.notna(note) and str(note):
                st.caption(str(note))


def fair_value_derivation(r: pd.Series, use_norm: bool) -> dict:
    """
    Il conto del fair value di categoria, riga per riga, e il valore che ne esce.

    Ogni passaggio e' visibile: quale grandezza si moltiplica (utili correnti,
    utili normalizzati o patrimonio per azione), per quale multiplo, e da dove
    viene quel multiplo. Un fair value che non si puo' rifare a mano non e'
    verificabile, ed e' esattamente il caso in cui un numero assurdo passa
    inosservato.
    """
    anchor = str(r.get("lynch_anchor") or "earnings")
    eps_base = str(r.get("lynch_eps_base") or "current")
    fair_pe = _num(r.get("lynch_fair_pe"))
    fair_pb = _num(r.get("lynch_fair_pb"))
    eps_cur = _num(r.get("eps_ttm"))
    eps_nrm = _num(r.get("eps_normalized_3y"))
    bvps = _num(r.get("book_value_per_share"))
    price = _num(r.get("current_price"))

    # Il selettore "EPS basis" della scheda vince sulla base scelta dal modello:
    # se il lettore chiede di vedere tutto normalizzato, anche questo conto lo e'.
    use_normalized = use_norm or eps_base == "normalized"

    rows, value = [], None
    if anchor == "book":
        rows.append(("Book value per share", fmt_ratio(bvps, 2)))
        rows.append(("× Fair price-to-book", fmt_ratio(fair_pb, 2)))
        if bvps and fair_pb:
            value = bvps * fair_pb
    elif anchor == "earnings" and fair_pe:
        base = eps_nrm if use_normalized else eps_cur
        label = ("3-year normalized EPS" if use_normalized else "EPS (TTM)")
        rows.append((label, f"${base:,.2f}" if base else "—"))
        rows.append(("× Fair P/E for the category", fmt_ratio(fair_pe, 1)))
        if base and base > 0:
            value = base * fair_pe
    # NIENTE MARKDOWN NELLE CELLE: st.dataframe non lo interpreta e stampa gli
    # asterischi cosi' come sono. L'enfasi si fa con il testo, non con la sintassi.
    rows.append(("= FAIR VALUE", f"${value:,.2f}" if value else "n/a"))
    if price:
        rows.append(("Current price", f"${price:,.2f}"))
        if value:
            rows.append(("Price vs fair value",
                         f"{(price / value - 1) * 100:+.0f}%"))
    return {"rows": rows, "value": value, "anchor": anchor,
            "normalized": use_normalized}


def render_valuation_chart(ticker: str, company: str, use_norm_eps: bool,
                           pe: int, years: str, scale: str) -> pd.DataFrame | None:
    """Prezzo reale contro fair value nel tempo. Un solo asse: sono entrambi $."""
    p = charts.palette()
    d = hist[hist["ticker"] == ticker].copy().sort_values("date")
    if d.empty:
        st.info("No price history on file for this ticker.")
        return None
    # La mediana mobile a 3 anni si calcola sulla serie INTERA, prima di
    # tagliarla per "History length": altrimenti i primi punti visibili
    # userebbero una finestra incompleta.
    d["eps_norm3y"] = (d.set_index("date")["eps"]
                       .rolling("1095D", min_periods=1).median().to_numpy())
    if years != "All":
        cutoff = d["date"].max() - pd.DateOffset(years=int(years))
        d = d[d["date"] >= cutoff]

    if use_norm_eps:
        d["eps_used"] = d["eps_norm3y"]
        d["fv"] = np.where(d["eps_used"] > 0, d["eps_used"] * pe, np.nan)
    else:
        d["eps_used"] = d["eps"]
        d["fv"] = pd.to_numeric(d[f"fair_value_pe{pe}"], errors="coerce")
    d = d[d["price"].notna()]
    if d.empty:
        st.info("No price history on file for this ticker.")
        return None
    d["premium_pct"] = np.where(d["fv"] > 0, (d["price"] / d["fv"] - 1) * 100, np.nan)

    eps_label = "Normalized EPS (3y median)" if use_norm_eps else "TTM EPS"
    custom = np.column_stack((
        d["fv"].round(2).to_numpy(dtype=float),
        (d["price"] - d["fv"]).round(2).to_numpy(dtype=float),
        d["premium_pct"].round(1).to_numpy(dtype=float),
        d["eps_used"].round(2).to_numpy(dtype=float),
        np.where(d["price"] < d["fv"], "BELOW fair value", "ABOVE fair value"),
    ))

    fig = go.Figure()
    # Il PREZZO prende lo slot 1 della tavolozza in quanto grandezza principale;
    # il fair value lo slot 2. Non si scambiano fra una scheda e l'altra.
    fig.add_trace(go.Scatter(
        x=d["date"], y=d["price"], name="Price",
        line=dict(color=p["series"][0], width=2), customdata=custom,
        hovertemplate=(
            "Price: <b>$%{y:,.2f}</b><br>"
            "Fair value: $%{customdata[0]:,.2f}<br>"
            "Difference: $%{customdata[1]:+,.2f} (%{customdata[2]:+.1f}%)<br>"
            "%{customdata[4]}<br>"
            f"{eps_label}: " + "$%{customdata[3]:,.2f}"
            "<extra></extra>"),
    ))
    fv_name = f"Fair value (P/E {pe})" + (" · normalized EPS" if use_norm_eps else "")
    fig.add_trace(go.Scatter(
        x=d["date"], y=d["fv"], name=fv_name,
        line=dict(color=p["series"][1], width=2), connectgaps=False,
        hoverinfo="skip"))

    # Periodi di utili negativi. UNA SOLA ETICHETTA, sulla prima banda: la
    # legenda in didascalia spiega cosa sono le bande, e ripetere "negative
    # earnings" su ognuna produceva scritte sovrapposte e illeggibili su titoli
    # come Alcoa, che ne ha sette.
    neg = d[d["eps"] <= 0]
    if not neg.empty:
        gaps = neg["date"].diff().dt.days.fillna(0) > 40
        for n, (_, g) in enumerate(neg.groupby(gaps.cumsum())):
            # I parametri dell'annotazione si passano SOLO alla prima banda.
            # `annotation_text=None` non disattiva l'annotazione: Plotly la crea
            # comunque e le mette il testo predefinito, e sul grafico comparivano
            # tre etichette "new text" accanto a quella giusta.
            extra = dict(annotation_text="negative earnings",
                         annotation_position="top left",
                         annotation_font=dict(size=10, color=p["muted"])) if n == 0 else {}
            fig.add_vrect(x0=g["date"].min(), x1=g["date"].max(),
                          fillcolor=p["band"], line_width=0, layer="below",
                          **extra)

    if events is not None:
        ev = events[(events["ticker"] == ticker)
                    & (events["date"] >= d["date"].min())
                    & (events["date"] <= d["date"].max())]
        for _, e in ev.iterrows():
            fig.add_vline(x=e["date"],
                          line=dict(color=p["muted"], width=1, dash="dot"))
            fig.add_annotation(x=e["date"], yref="paper", y=1.0,
                               text=f"{str(e['type']).capitalize()} {e['detail']}",
                               showarrow=False, font=dict(size=9, color=p["muted"]),
                               textangle=-90, xanchor="left", yanchor="top")

    charts.style(fig, height=460, ylab="$")
    fig.update_yaxes(type="log" if scale == "Log" else "linear")
    st.plotly_chart(fig, use_container_width=True,
                    key="chart_valuation_price")
    st.caption(
        f"**Blue** = market price · **orange** = fair value (EPS × P/E {pe}). "
        "Dotted lines = corporate events. Shaded band = negative earnings, where "
        "no fair value exists and the line breaks."
    )
    return d


def render_valuation_tab(ticker: str, row: pd.DataFrame) -> None:
    """
    "Quanto vale?" — e nient'altro.

    La scheda raccoglie tutto cio' che risponde a quella domanda e SOLO quello:
    la categoria e il suo multiplo, il grafico, il conto del fair value, i
    multipli confrontati con l'industria (che stavano in Financials, dove
    misuravano la qualita' dell'azienda invece del suo prezzo) e l'arbitraggio
    dell'EPS (che stava in Data quality, dove nessuno lo trovava pur essendo il
    numero da cui dipende ogni riga di questa pagina).
    """
    if row.empty:
        st.info("No summary row for this ticker in fundamentals.csv.")
        return
    r = row.iloc[0]
    company = r.get("company", ticker)

    # Un avviso che va PRIMA di tutto: se il simbolo di mercato non e' piu'
    # valido, il prezzo — e quindi ogni multiplo e ogni sconto in questa pagina
    # — potrebbe riferirsi a uno strumento diverso dai bilanci mostrati.
    if bool(r.get("stale_symbol")) or str(r.get("classification_source")) in ("sic", "none"):
        stale = bool(r.get("stale_symbol"))
        st.warning(
            ("**The market data provider has no profile for this symbol.** "
             if stale else
             "**Sector and industry are guessed from the SEC SIC code**, "
             "because the provider had no profile for this symbol. ")
            + ("The financial statements come from SEC EDGAR via the company's "
               "CIK and are correct, but the price — and therefore every "
               "multiple and discount on this page — comes from a symbol the "
               "provider does not recognise. That usually means the ticker has "
               "changed. Verify the current symbol before using these figures. "
               if stale else
               "The peer comparison and the Lynch category both depend on that "
               "classification, so treat both as provisional. The cyclical "
               "multiple is deliberately not applied on a guessed sector."),
            icon="⚠️")

    category_header(r)
    st.divider()

    _init("hist_pe", PE_OPTIONS[0])
    _init("hist_years", "All")
    _init("hist_scale", "Log")
    c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
    with c1:
        pe = st.selectbox("Fair value at P/E", PE_OPTIONS, key="hist_pe",
                          format_func=lambda x: f"P/E {x}")
    with c2:
        years = st.selectbox("History", ["All", "15", "10", "5", "3", "1"],
                             key="hist_years",
                             format_func=lambda x: x if x == "All" else f"{x}y")
    with c3:
        scale = st.selectbox("Y-axis", ["Log", "Linear"], key="hist_scale")
    with c4:
        eps_basis_choice = st.radio(
            "EPS basis", ["Current (TTM)", "Normalized (3-year median)"],
            horizontal=True, key="hist_eps_basis",
            help="**Normalized** smooths earnings inflated or depressed by "
                 "one-off items: it uses the median TTM EPS over the 3 years "
                 "preceding each point instead of the current value.")
    use_norm_eps = eps_basis_choice.startswith("Normalized")

    price_now = _num(r.get("current_price"))
    eps_shown = (_num(r.get("eps_normalized_3y")) if use_norm_eps
                 else _num(r.get("eps_ttm")))
    eps_label = "Normalized EPS (3y)" if use_norm_eps else "EPS (TTM)"

    if use_norm_eps:
        fv_fix = _num(r.get("fair_value_norm_pe15"))
        fv_fix_label = "Fair value P/E 15 (norm.)"
        fv_fix_help = "Normalized EPS (3-year median) × 15."
        disc_fix = ((price_now / fv_fix - 1) * 100) if (fv_fix and price_now) else None
    else:
        fv_fix = _num(r.get("fair_value_pe15"))
        fv_fix_label = "Fair value (P/E 15)"
        fv_fix_help = "TTM EPS × 15 — the classic Lynch earnings line."
        disc_fix = _num(r.get("discount_vs_fv15_pct"))

    deriv = fair_value_derivation(r, use_norm_eps)
    fv_cat = deriv["value"]
    disc_cat = ((price_now / fv_cat - 1) * 100) if (fv_cat and price_now) else None

    # DUE FILE DA QUATTRO, non una da sei o da otto.
    #
    # La riga di sopra e' il PREZZO e i due fair value che gli si confrontano;
    # quella di sotto e' come si legge quel confronto. Il fair value a P/E 15
    # sta qui accanto a quello di categoria — prima esisteva solo nella tabella
    # in fondo alla pagina, e chi guardava i KPI in cima vedeva un solo modello
    # senza sapere che ce n'erano due. Otto riquadri su una riga sola, invece,
    # comprimono le etichette fino a renderle illeggibili sotto i 1400 px.
    _pe_prov, _pe_div = _num(r.get("pe_ratio_provider")), _num(r.get("pe_divergence_pct"))
    _pe_help = ("**What it is** — how many years of current earnings you are "
                "paying for one share.\n\n**Formula** — price ÷ the EPS shown "
                "on this page, both from the same row.")
    if _pe_prov is not None:
        _pe_help += f"\n\n**Note** — the provider reports {_pe_prov:.1f}"
        _pe_help += (", computed on its own EPS, which the arbitration did not "
                     "select." if _pe_div is not None and _pe_div >= 1
                     else "; consistent with ours.")
    lr_v = _num(r.get("lynch_ratio"))

    def _disc_delta(v):
        """Etichetta e colore di uno scarto prezzo/fair value."""
        if v is None:
            return None, "off"
        return ("below fair value" if v < 0 else "above fair value",
                "normal" if v < 0 else "inverse")

    _d_cat_txt, _d_cat_col = _disc_delta(disc_cat)
    _d_fix_txt, _d_fix_col = _disc_delta(disc_fix)
    # Sotto un fair value la didascalia dice DOVE STA IL PREZZO rispetto a
    # quel valore, non "sopra/sotto" e basta: su un riquadro il cui numero e'
    # gia' il fair value, "above fair value" si legge come se a stare sopra
    # fosse il valore mostrato.
    _p_cat = f"price {disc_cat:+.0f}%" if disc_cat is not None else None
    _p_fix = f"price {disc_fix:+.0f}%" if disc_fix is not None else None

    m = st.columns(4)
    m[0].metric("Price", fmt_usd(price_now),
                help="**What it is** — the last market price on file.\n\n"
                     "**How to read it** — everything on this page compares "
                     "this number with a value estimated from the earnings.")
    m[1].metric(eps_label, fmt_usd(eps_shown),
                help="**What it is** — the profit attributable to one share.\n\n"
                     "**How to read it** — the number every figure on this page "
                     "is built on: the fair value multiplies it, the P/E "
                     "divides by it.")
    m[2].metric("Fair value (category)", fmt_usd(fv_cat) if fv_cat else "n/a",
                delta=_p_cat, delta_color=_d_cat_col,
                help="**What it is** — the fair value from this company's own "
                     "Lynch category: a multiple adapted to how fast it "
                     "grows.\n\n**Formula** — the earnings base × the fair "
                     "multiple for the category (see *How this fair value is "
                     "built*, below the chart).\n\n**How to read it** — the "
                     "more informative of the two models, provided the "
                     "confidence badge above is not red.")
    m[3].metric(fv_fix_label, fmt_usd(fv_fix) if fv_fix else "n/a",
                delta=_p_fix, delta_color=_d_fix_col,
                help="**What it is** — the classic Lynch earnings line: the "
                     "same multiple for every company on earth.\n\n"
                     "**Formula** — " + fv_fix_help + "\n\n"
                     "**How to read it** — a quick, blunt filter. It penalises "
                     "fast growers and flatters slow ones, so a large gap "
                     "against the category figure is expected, not an error.")

    m2 = st.columns(4)
    m2[0].metric("Discount vs category",
                 f"{disc_cat:+.0f}%" if disc_cat is not None else "—",
                 delta=_d_cat_txt, delta_color=_d_cat_col,
                 help="**Formula** — price ÷ category fair value − 1.\n\n"
                      "**How to read it** — negative means the market is "
                      "asking less than the model's value; positive means it "
                      "is asking more.")
    m2[1].metric("Discount vs P/E 15",
                 f"{disc_fix:+.0f}%" if disc_fix is not None else "—",
                 delta=_d_fix_txt, delta_color=_d_fix_col,
                 help="**Formula** — price ÷ P/E 15 fair value − 1.\n\n"
                      "**How to read it** — the same comparison against the "
                      "fixed line. When the two discounts disagree, the "
                      "company's growth is far from the 15× average.")
    m2[2].metric("Current P/E", fmt_ratio(_num(r.get("pe_ratio"))), help=_pe_help)
    m2[3].metric("Lynch ratio", fmt_ratio(lr_v, 2),
                 delta=("attractive" if lr_v and lr_v > 1.5 else
                        "fair" if lr_v and lr_v >= 1 else
                        "expensive" if lr_v else None),
                 delta_color="normal" if (lr_v and lr_v >= 1) else "inverse",
                 help="**What it is** — Lynch's own quick verdict: what you "
                      "get (growth plus dividend) against what you pay "
                      "(the P/E).\n\n**Formula** — (growth % + dividend %) ÷ "
                      "P/E.\n\n**How to read it** — above 1.5 attractive, "
                      "around 1 fair, below 1 expensive. It is the reciprocal "
                      "of the PEG, so the reading flips.")

    d = render_valuation_chart(ticker, company, use_norm_eps, pe, years, scale)

    # ------------------- COME NASCE QUESTO FAIR VALUE -------------------
    #
    # RICHIUSO DI DEFAULT. Sono due tabelle di aritmetica: servono a chi vuole
    # rifare il conto, non a chi sta leggendo la pagina per la prima volta, e
    # aperte spingevano il confronto con i pari sotto due schermate. Il
    # titolo del riquadro porta gia' i due risultati, cosi' aprirlo e' una
    # scelta informata e non un sondaggio.
    st.divider()
    # I DOLLARI VANNO PROTETTI. L'etichetta di un expander e' markdown, e in
    # markdown due "$" sulla stessa riga aprono e chiudono una formula LaTeX:
    # "category $25.20 · P/E 15 $75.75" veniva reso come una formula, con i due
    # importi in tondo matematico e il "·" trasformato in operatore.
    _fv_summary = " · ".join(
        [f"category {fmt_usd(fv_cat)}" if fv_cat else "category n/a",
         f"P/E 15 {fmt_usd(fv_fix)}" if fv_fix else "P/E 15 n/a"]).replace("$", r"\$")
    with st.expander(f"🧮 How this fair value is built — {_fv_summary}",
                     expanded=False):
        left, right = st.columns([1, 1])
        with left:
            st.markdown("**The category model** — adapts the multiple to the "
                        "type of company")
            st.dataframe(
                pd.DataFrame(deriv["rows"], columns=["Step", "Value"]),
                use_container_width=True, hide_index=True)
            basis = r.get("lynch_pe_basis")
            if pd.notna(basis):
                st.caption(f"Multiple from: {basis}")
            if deriv["anchor"] == "book":
                st.caption(
                    "This company is valued on its **assets**, not its "
                    "earnings: that is what a Lynch asset play is. The P/E is "
                    "not the tool here, so no earnings multiple is shown.")
            elif deriv["anchor"] == "none":
                st.caption(
                    "No valuation anchor: the company has no positive "
                    "earnings, current or normalized, and does not trade below "
                    "book value. Stating that is more useful than producing a "
                    "number from nothing — assess the cash burn and the "
                    "balance sheet.")
        with right:
            st.markdown("**The fixed model** — P/E 15 for everyone, the classic "
                        "Lynch earnings line")
            eps_used_fix = eps_shown
            fix_rows = [
                (("3-year normalized EPS" if use_norm_eps else "EPS (TTM)"),
                 fmt_usd(eps_used_fix) if eps_used_fix else "—"),
                ("× Fixed P/E", "15"),
                ("= FAIR VALUE", fmt_usd(fv_fix) if fv_fix else "—"),
                ("Price vs fair value",
                 f"{disc_fix:+.0f}%" if disc_fix is not None else "—"),
            ]
            st.dataframe(pd.DataFrame(fix_rows, columns=["Step", "Value"]),
                         use_container_width=True, hide_index=True)
            st.caption(fv_fix_help + " A quick filter, identical for every "
                       "company: it penalises fast growers and flatters slow "
                       "ones.")

        if fv_fix and fv_cat:
            spread = abs(fv_cat / fv_fix - 1) * 100
            if spread > 35:
                st.info(
                    f"The two models diverge by **{spread:.0f}%**. That is "
                    "normal when the category multiple is far from 15 — a fast "
                    "grower or a slow grower. The per-category figure is the "
                    "more informative of the two; check the confidence badge "
                    "above before leaning on it.")
            else:
                st.caption("The two models agree within 35%: a robust signal.")

    # ------------------- MULTIPLI CONTRO L'INDUSTRIA -------------------
    st.divider()
    peers, peer_label, n_peers = peer_reference(r, ind_med, sec_med)
    st.markdown("#### What the market is charging, versus its peers")
    if peers is not None:
        st.caption(
            f"Every multiple below compared with the **median of the "
            f"{peer_label}** ({n_peers} companies in this dataset). A P/E of 30 "
            "is expensive for a utility and cheap for software, so the absolute "
            "number is half the story.")
    metric_grid([metric_item(k, r, peers) for k in
                 ("pe_ratio", "forward_pe", "ps_ratio", "pfcf_ratio")])
    metric_grid([metric_item(k, r, peers) for k in
                 ("price_to_book", "peg_ratio", "ev_to_ebit", "fcf_yield_pct")
                 if k in r.index])

    render_normalized_earnings(r, _num(r.get("eps_ttm")))
    render_eps_arbitration(ticker, r, compact=True)

    if d is not None:
        st.download_button("📥 Download price history (CSV)", d.to_csv(index=False),
                           f"{ticker}_pe{pe}.csv", "text/csv")


def annual_for(ticker: str) -> pd.DataFrame | None:
    """Gli esercizi depositati per un ticker, in ordine cronologico."""
    if annual is None:
        return None
    a = annual[annual["ticker"] == ticker].copy()
    if a.empty:
        return None
    a = a.sort_values("fy_end")
    a["year"] = a["fy_end"].astype(str).str.slice(0, 4)
    return a


def _has(a: pd.DataFrame | None, col: str) -> bool:
    return a is not None and col in a.columns and a[col].notna().any()


def _col(a: pd.DataFrame, col: str):
    return a[col] if col in a.columns else None


def kpi_row(r: pd.Series, peers: pd.Series | None, keys, ncols: int = 4) -> None:
    """
    Una fila di riquadri, saltando in silenzio le voci che questa societa' non
    ha.

    Il salto silenzioso e' il punto: una banca non deposita il capex, una
    societa' senza debito non ha un rapporto debito/mezzi propri. Mostrare il
    riquadro vuoto suggerisce un guasto del programma; ometterlo dice la
    verita', cioe' che quella voce qui non esiste.
    """
    items = [metric_item(k, r, peers) for k in keys
             if k in METRICS and k in r.index and _num(r.get(k)) is not None]
    if items:
        metric_grid(items, ncols)


def _fy_table(a: pd.DataFrame, spec: list[tuple[str, str, object]],
              extra: list[tuple[str, object]] | None = None) -> pd.DataFrame:
    """
    Un pezzo di bilancio come depositato: una riga per esercizio.

    `spec` e' [(colonna, etichetta, formattatore)] e le colonne assenti spariscono
    invece di produrre una colonna di trattini. `extra` aggiunge voci CALCOLATE
    (un margine, un rapporto) gia' pronte come coppie (etichetta, valori).
    """
    show = pd.DataFrame({"Fiscal year": a["fy_end"].astype(str)})
    for col, label, fmt in spec:
        if col in a.columns and a[col].notna().any():
            show[label] = a[col].map(fmt)
    for label, values in (extra or []):
        show[label] = values
    return show


def _pct_series(num, den) -> list[str]:
    """Percentuale riga per riga per le tabelle annuali, con il trattino dove
    il conto non si puo' fare."""
    if num is None or den is None:
        return []
    out = []
    for n, d in zip(num, den):
        out.append(fmt_pct(n / d * 100) if (pd.notna(n) and pd.notna(d) and d) else "—")
    return out


def render_financials_tab(ticker: str, r: pd.Series) -> None:
    """
    "Quanto e' solida l'azienda?" — la salute del business, non il suo prezzo.

    ORGANIZZATA COME UN BILANCIO, non come un elenco di numeri interessanti.
    Le voci stanno in quattro schede annidate che ricalcano i tre prospetti —
    conto economico, stato patrimoniale, rendiconto finanziario — piu' i
    rapporti che nascono incrociandoli, piu' la crescita a cinque anni. E' la
    stessa suddivisione che si trova nella relazione annuale della societa': chi
    cerca il debito sa gia' dove guardare, senza scorrere una pagina unica in
    cui margini, cassa e multipli si alternavano.

    I multipli di valutazione restano ANCHE qui, nella scheda Ratios, ma come
    rapporti fra prezzo e conti, non come giudizio sul titolo: il giudizio sta
    in Valuation, accanto al fair value che lo produce.

    La provenienza dei dati non e' piu' in fondo a questa scheda: e' in Data
    quality, dove c'e' gia' la tabella che dichiara formula e tag XBRL di ogni
    voce. Erano due risposte alla stessa domanda in due punti diversi.
    """
    peers, peer_label, n_peers = peer_reference(r, ind_med, sec_med)
    a = annual_for(ticker)

    # ---------------- INTESTAZIONE: LE QUATTRO GRANDEZZE ----------------
    kpi_row(r, peers, ("revenue_ttm", "ebit_ttm", "net_income_ttm", "fcf_ttm"))
    if peers is not None:
        st.caption(
            f"Trailing twelve months, from the company's own SEC filings. "
            f"Where a delta is shown it is measured against the **median of "
            f"the {peer_label}** ({n_peers} companies in this dataset): "
            "percentages compared in percentage points (pp), ratios in "
            "relative terms. Absolute amounts carry no delta — size is not a "
            "quality.")
    else:
        st.caption(
            f"Trailing twelve months, from the company's own SEC filings. No "
            f"peer group with at least {MIN_PEERS} companies for this ticker in "
            "the current dataset, so no industry comparison is shown.")

    basis = r.get("financials_basis")
    if isinstance(basis, str) and basis.startswith("annual:"):
        st.info(
            "For **" + basis.split(":", 1)[1].replace(",", ", ") + "** this "
            "company files only annual figures, so those values are the latest "
            "full fiscal year rather than the last twelve months.", icon="🗓️")
    if _num(r.get("fcf_ttm")) is None and _num(r.get("revenue_ttm")) is not None:
        st.info(
            "No free cash flow for this company: it doesn't tag capital "
            "expenditure in a comparable way — normal for banks and insurers, "
            "whose cash flow statement has a different structure. Everything "
            "built on FCF is blank throughout the app.", icon="ℹ️")

    t_inc, t_bal, t_cash, t_ratio, t_growth = st.tabs(
        ["🧾 Income statement", "🏦 Balance sheet", "💵 Cash flow",
         "📐 Ratios", "📈 Growth (5y)"])

    # =================================================================== #
    # CONTO ECONOMICO                                                     #
    # =================================================================== #
    with t_inc:
        st.markdown("##### What it sells, and what it keeps")
        kpi_row(r, peers, ("revenue_ttm", "ebit_ttm", "net_income_ttm",
                           "eps_ttm"))
        kpi_row(r, peers, ("operating_margin_pct", "net_margin_pct",
                           "revenue_growth_yoy_pct", "eps_growth_yoy"))
        kpi_row(r, peers, ("revenue_per_share", "shares_outstanding",
                           "revenue_latest_fy", "earnings_yield_pct"))

        if a is not None:
            st.markdown("###### Revenue and profit, year by year")
            fig = charts.bars(a["year"], [("Revenue", _col(a, "revenue")),
                                          ("EBIT", _col(a, "ebit")),
                                          ("Net income", _col(a, "net_income"))],
                              ylab="$", height=300)
            if fig is not None:
                st.plotly_chart(fig, use_container_width=True,
                                key="fin_income_bars")
            # I MARGINI IN UN GRAFICO SEPARATO, non su un secondo asse
            # verticale. Sovrapporre una percentuale a un valore in miliardi
            # obbliga a scegliere un allineamento arbitrario fra le due scale, e
            # il lettore ci legge una correlazione che nei dati non c'e'. Due
            # grafici che condividono l'asse dei tempi dicono la stessa cosa
            # senza mentire.
            if _has(a, "revenue") and _has(a, "net_income"):
                net_m = np.where((a["revenue"] > 0) & a["net_income"].notna(),
                                 a["net_income"] / a["revenue"] * 100, np.nan)
                op_m = (np.where((a["revenue"] > 0) & a["ebit"].notna(),
                                 a["ebit"] / a["revenue"] * 100, np.nan)
                        if _has(a, "ebit") else None)
                mfig = charts.lines(a["year"],
                                    [("Net margin", net_m),
                                     ("Operating margin", op_m)],
                                    ylab="% of revenue", height=230, pct=True)
                if mfig is not None:
                    st.plotly_chart(mfig, use_container_width=True,
                                    key="fin_income_margins")
                    st.caption(
                        "Margins get their own chart rather than a second "
                        "vertical axis: percentages and billions on one panel "
                        "would need an arbitrary alignment between the two "
                        "scales, and that alignment reads as a correlation "
                        "that isn't in the data. Watch the **direction** more "
                        "than the level — a margin sliding for three years is "
                        "a business losing pricing power.")

            st.markdown("###### As filed")
            extra = []
            if _has(a, "revenue") and _has(a, "net_income"):
                extra.append(("Net margin",
                              _pct_series(a["net_income"], a["revenue"])))
            if _has(a, "revenue") and _has(a, "ebit"):
                extra.append(("Operating margin",
                              _pct_series(a["ebit"], a["revenue"])))
            st.dataframe(
                _fy_table(a, [("revenue", "Revenue", fmt_money),
                              ("ebit", "EBIT", fmt_money),
                              ("net_income", "Net income", fmt_money),
                              ("eps", "EPS", fmt_usd)], extra),
                use_container_width=True, hide_index=True)
            st.caption(
                "One row per complete fiscal year, from the annual report "
                "(10-K). Fiscal years don't always end in December: the "
                "closing date is the company's own. The EPS column is the "
                "**filed** figure, restated for stock splits only — it does "
                "not carry the anchoring factor that aligns the chart to "
                "today's EPS, so it can differ slightly from the TTM figures "
                "elsewhere.")
        else:
            st.caption("No annual financials on file for this ticker.")

    # =================================================================== #
    # STATO PATRIMONIALE                                                  #
    # =================================================================== #
    with t_bal:
        st.markdown("##### What it owns")
        kpi_row(r, peers, ("assets", "equity", "book_value_per_share",
                           "asset_turnover"))
        kpi_row(r, peers, ("equity_ratio_pct", "roe_pct", "price_to_book",
                           "enterprise_value"))

        # ---------------- DEBITO: UNA SEZIONE SUA ----------------
        #
        # DICHIARATA ANCHE QUANDO MANCA. Le schede vuote vengono saltate in
        # silenzio (una banca non deposita il capex, e va bene cosi'), ma il
        # debito e' l'unica voce che il lettore va a cercare per NOME: se le
        # sue schede spariscono senza dire niente, la pagina sembra sostenere
        # che la societa' non ha debiti. Qui, quando il dato non c'e', lo si
        # scrive — e il 30% del dataset non lo ha, perche' non tutte le
        # societa' taggano il debito in modo confrontabile.
        st.markdown("##### What it owes")
        _debt_keys = ("total_debt", "long_term_debt", "net_debt", "cash")
        _has_debt = any(_num(r.get(k)) is not None for k in _debt_keys[:3])
        if _has_debt:
            kpi_row(r, peers, _debt_keys)
            kpi_row(r, peers, ("debt_to_equity", "net_debt_to_equity",
                               "debt_to_assets_pct", "cash_to_debt_pct"))
            st.markdown("###### How heavy that debt is")
            kpi_row(r, peers, ("net_debt_to_ebit", "net_debt_to_fcf",
                               "earning_power_pct", "fcf_ttm"))
            st.caption(
                "The two coverage ratios answer the same question in two "
                "currencies: **net debt / EBIT** measures the debt in years of "
                "operating profit, **net debt / FCF** in years of cash actually "
                "left over. The second is the stricter one, because interest "
                "is paid with cash and not with an accounting result. Negative "
                "net debt means the company holds more cash than it owes.")
        else:
            st.info(
                "**No debt figures on file for this company.** It does not tag "
                "borrowings in a way that can be compared across companies — "
                "common for banks and insurers, whose liabilities are deposits "
                "and technical reserves rather than debt. It does **not** mean "
                "the company is debt-free: check the balance sheet in the "
                "latest 10-K, linked from the Filings tab.", icon="🏦")

        # Il grafico chiesto: debito, cassa e cassa libera insieme. E' il
        # confronto che dice se il debito e' sostenibile — quanto se ne deve,
        # quanto se ne potrebbe ripagare subito, quanto se ne ripaga ogni anno
        # — e per questo il free cash flow, che e' voce di rendiconto, sta
        # anche qui: da solo il debito non si giudica.
        if a is not None and (_has(a, "total_debt") or _has(a, "cash")
                              or _has(a, "fcf")):
            st.markdown("###### Debt, cash and free cash flow, year by year")
            fig = charts.bars(a["year"],
                              [("Total debt", _col(a, "total_debt")),
                               ("Cash & equivalents", _col(a, "cash")),
                               ("Free cash flow", _col(a, "fcf"))],
                              ylab="$", height=300)
            if fig is not None:
                st.plotly_chart(fig, use_container_width=True,
                                key="fin_balance_debt_bars")
                st.caption(
                    "The three bars are the whole debt question: how much is "
                    "owed, how much could be repaid this morning out of cash, "
                    "and how much is repaid every year out of the cash the "
                    "business generates. Debt rising while free cash flow "
                    "falls is the pattern worth catching early.")
        elif a is not None:
            st.caption(
                "ℹ️ Debt and cash are not in the annual file for this company. "
                "Regenerate the dataset (`python build_dataset.py`) to add "
                "them — they are read from the balance sheet tags the company "
                "files.")

        if a is not None and (_has(a, "equity") or _has(a, "assets")):
            st.markdown("###### Owned versus borrowed, year by year")
            fig = charts.bars(a["year"], [("Total assets", _col(a, "assets")),
                                          ("Equity", _col(a, "equity"))],
                              ylab="$", height=250)
            if fig is not None:
                st.plotly_chart(fig, use_container_width=True,
                                key="fin_balance_equity_bars")
                st.caption(
                    "The gap between the two bars is everything the company "
                    "owes to somebody else. Widening year after year means the "
                    "balance sheet is growing on borrowed money.")

        if _num(r.get("enterprise_value")):
            st.markdown("###### From share price to price of the business")
            fig = charts.capital_structure(_num(r.get("market_cap")),
                                           _num(r.get("total_debt")),
                                           _num(r.get("cash")))
            if fig is not None:
                st.plotly_chart(fig, use_container_width=True,
                                key="fin_capital_structure")
                st.caption(
                    "Market cap + total debt − cash = **enterprise value**: "
                    "the price of the whole business rather than of its shares "
                    "alone. It is what makes two companies with the same "
                    "market cap but opposite balance sheets comparable.")

        if a is not None:
            st.markdown("###### As filed")
            extra = []
            if _has(a, "equity") and _has(a, "assets"):
                extra.append(("Equity ratio",
                              _pct_series(a["equity"], a["assets"])))
            if _has(a, "total_debt") and _has(a, "cash"):
                extra.append(("Net debt",
                              [fmt_money(d - c) if (pd.notna(d) and pd.notna(c))
                               else "—" for d, c in zip(a["total_debt"], a["cash"])]))
            st.dataframe(
                _fy_table(a, [("assets", "Total assets", fmt_money),
                              ("equity", "Equity", fmt_money),
                              ("total_debt", "Total debt", fmt_money),
                              ("cash", "Cash", fmt_money)], extra),
                use_container_width=True, hide_index=True)
            st.caption(
                "Balance sheet figures are a **snapshot** on the closing date, "
                "not a total for the year: unlike revenue or cash flow they "
                "cannot be added up across periods.")

    # =================================================================== #
    # RENDICONTO FINANZIARIO                                              #
    # =================================================================== #
    with t_cash:
        st.markdown("##### What actually reaches the bank account")
        kpi_row(r, peers, ("ocf_ttm", "capex_ttm", "fcf_ttm", "fcf_latest_fy"))
        kpi_row(r, peers, ("ocf_margin_pct", "fcf_margin_pct",
                           "fcf_conversion_pct", "capex_to_revenue_pct"))
        st.markdown("###### What it does with that cash")
        kpi_row(r, peers, ("fcf_per_share", "fcf_yield_pct", "dividend_yield",
                           "payout_ratio_pct"))
        kpi_row(r, peers, ("dividend_rate", "dividend_ttm_yield_pct"))
        _dv = _num(r.get("dividend_yield"))
        _dt = _num(r.get("dividend_ttm_yield_pct"))
        if bool(r.get("dividend_has_special")) and _dv is not None and _dt is not None \
                and _dt > _dv * 1.3:
            st.caption(
                "💡 This company paid **more than its regular dividend** over "
                "the last twelve months: the gap between the two figures above "
                "is a special or variable dividend. The headline yield "
                "deliberately leaves it out — a one-off payment is not a rate "
                "of return you can expect again.")
        elif _dv is not None and _dt is not None and _dt > _dv * 1.3:
            st.caption(
                "💡 The trailing figure is well above the current rate: the "
                "dividend has been **cut**, and the yield above already "
                "reflects the new, lower payment.")
        elif _dv is not None and _dt is not None and _dv > _dt * 1.3:
            st.caption(
                "💡 The current rate is well above what was paid over the last "
                "year: the dividend has just been **raised or reinstated**.")

        if a is not None and (_has(a, "ocf") or _has(a, "fcf")):
            st.markdown("###### Cash in, cash invested, cash left")
            capex = (-a["capex"] if _has(a, "capex") else None)
            fig = charts.bars(a["year"],
                              [("Operating cash flow", _col(a, "ocf")),
                               ("Capital expenditure", capex),
                               ("Free cash flow", _col(a, "fcf"))],
                              ylab="$", height=300)
            if fig is not None:
                st.plotly_chart(fig, use_container_width=True,
                                key="fin_cash_bars")
                st.caption(
                    "Capital expenditure is drawn negative because that is "
                    "what it does to cash: operating cash flow minus it leaves "
                    "free cash flow. EDGAR reports cash flow year-to-date "
                    "rather than per quarter, so quarters are recovered by "
                    "differencing consecutive cumulative figures before "
                    "summing.")

        if a is not None:
            st.markdown("###### As filed")
            extra = []
            if _has(a, "fcf") and _has(a, "revenue"):
                extra.append(("FCF margin", _pct_series(a["fcf"], a["revenue"])))
            if _has(a, "fcf") and _has(a, "net_income"):
                extra.append(("FCF conversion",
                              _pct_series(a["fcf"], a["net_income"])))
            st.dataframe(
                _fy_table(a, [("ocf", "Op. cash flow", fmt_money),
                              ("capex", "Capex", fmt_money),
                              ("fcf", "Free cash flow", fmt_money),
                              ("dividends_paid", "Dividends paid", fmt_money)],
                          extra),
                use_container_width=True, hide_index=True)
            st.caption(
                "Cash flow is the least negotiable part of a set of accounts: "
                "revenue and profit depend on when a sale is recognised, this "
                "depends on when the money moved.")
            st.download_button("📥 Download annual financials (CSV)",
                               a.to_csv(index=False), f"{ticker}_financials.csv",
                               "text/csv")

    # =================================================================== #
    # RAPPORTI                                                            #
    # =================================================================== #
    with t_ratio:
        st.markdown("##### Everything expressed as a ratio")
        st.caption(
            "A ratio is what makes two companies of completely different size "
            "comparable — and what makes the peer median below meaningful. "
            "Hover any label for the formula and how to read it.")

        st.markdown("###### Price ratios — what the market is charging")
        kpi_row(r, peers, ("pe_ratio", "forward_pe", "ps_ratio", "pfcf_ratio"))
        kpi_row(r, peers, ("price_to_book", "ev_to_ebit", "peg_ratio",
                           "earnings_yield_pct"))

        st.markdown("###### Return on capital — how well the money is used")
        kpi_row(r, peers, ("roe_pct", "roic_pct", "earning_power_pct",
                           "asset_turnover"))

        st.markdown("###### Margins — how much of a sale survives")
        kpi_row(r, peers, ("operating_margin_pct", "net_margin_pct",
                           "fcf_margin_pct", "fcf_conversion_pct"))

        st.markdown("###### Cash and balance sheet")
        kpi_row(r, peers, ("fcf_yield_pct", "equity_ratio_pct",
                           "debt_to_equity", "net_debt_to_equity"))
        kpi_row(r, peers, ("net_debt_to_ebit", "net_debt_to_fcf",
                           "debt_to_assets_pct", "cash_to_debt_pct"))

        if peers is not None:
            st.markdown("###### Side by side with the peer group")
            rows = []
            for key in ("pe_ratio", "ps_ratio", "pfcf_ratio", "fcf_yield_pct",
                        "fcf_margin_pct", "earning_power_pct",
                        "operating_margin_pct", "net_margin_pct", "roe_pct",
                        "roic_pct", "equity_ratio_pct", "debt_to_equity",
                        "net_debt_to_ebit", "net_debt_to_fcf"):
                if key not in r.index or key not in peers.index:
                    continue
                gap, text = peer_gap(key, r.get(key), peers.get(key))
                rows.append({
                    "Ratio": METRICS[key]["label"],
                    ticker: fmt_metric(key, _num(r.get(key))),
                    "Peer median": fmt_metric(key, _num(peers.get(key))),
                    "Gap": text or "—",
                    "Better when": {"high": "higher", "low": "lower"}.get(
                        METRICS[key].get("better"), "—"),
                })
            if rows:
                # Altezza fissata sul numero di righe: il default di
                # st.dataframe ne mostra dieci e mette le altre dietro una
                # barra di scorrimento interna, che in una tabella di confronto
                # nasconde proprio le ultime voci — struttura patrimoniale e
                # debito — senza che si veda che ci sono.
                st.dataframe(pd.DataFrame(rows), use_container_width=True,
                             hide_index=True, height=(len(rows) + 1) * 35 + 3)
            st.caption(
                f"Benchmark: the median of the **{peer_label}** ({n_peers} "
                "companies). The peer group is built from the tickers in "
                "**this dataset**, not from the whole market: with a partial "
                "universe the median describes the companies actually loaded. "
                "Percentages are compared in percentage points, ratios in "
                "relative terms — mixing the two is the quickest way to make a "
                "comparison table lie.")
        else:
            st.caption(
                f"No peer group with at least {MIN_PEERS} companies for this "
                "ticker in the current dataset, so no side-by-side comparison "
                "is available.")

    # =================================================================== #
    # CRESCITA A CINQUE ANNI                                              #
    # =================================================================== #
    with t_growth:
        st.markdown("##### Five-year compound growth")
        st.caption(
            "Each rate is computed on the trailing-twelve-month series, so it "
            "compares twelve full months with twelve full months. A rate is "
            "blank when the starting value was zero or negative: a compound "
            "rate from a loss has no meaning. The **Growth** tab shows the two "
            "endpoints behind every one of these numbers.")
        kpi_row(r, peers, ("cagr_revenue_5y", "cagr_eps_5y",
                           "cagr_net_income_5y", "cagr_fcf_5y"))
        kpi_row(r, peers, ("cagr_ocf_5y", "revenue_growth_yoy_pct",
                           "eps_growth_yoy", "fcf_growth_yoy_pct"))

        if a is not None:
            st.markdown("###### Who grew, and by how much")
            fig = charts.indexed(a["year"],
                                 [("Revenue", _col(a, "revenue")),
                                  ("Net income", _col(a, "net_income")),
                                  ("Free cash flow", _col(a, "fcf")),
                                  ("EPS", _col(a, "eps"))])
            if fig is not None:
                st.plotly_chart(fig, use_container_width=True,
                                key="fin_growth_indexed")
                skipped = getattr(fig, "_skipped", [])
                note = ("Every series rebased to 100 at the first fiscal year "
                        "shown, so quantities of completely different size — "
                        "revenue in billions, EPS in dollars — sit on **one** "
                        "axis and can actually be compared. The lines "
                        "separating tell the story: EPS above revenue means "
                        "buybacks and margins are working, cash below profit "
                        "means the earnings aren't turning into money.")
                if skipped:
                    note += (" Not shown: " + ", ".join(skipped) + " — the "
                             "first year is negative, zero, or far below that "
                             "series' own usual level. An index built on a base "
                             "like that measures how unusual the first year "
                             "was, not growth.")
                st.caption(note)


def render_growth_tab(ticker: str, r: pd.Series) -> None:
    """
    "Sta crescendo, e quanto?" — la scheda che alimenta il multiplo.

    Qui e' arrivato l'audit dei CAGR, che stava in Data quality: gli estremi da
    cui nasce un tasso di crescita non sono una questione di provenienza del
    dato, sono la crescita stessa. E qui si dichiara, in testa, QUALE misura di
    crescita ha determinato il multiplo equo nella scheda Valuation — perche' e'
    la stessa domanda vista dai due lati.
    """
    a = annual_for(ticker)

    # ---------------- LA MISURA CHE DECIDE IL MULTIPLO ----------------
    st.markdown("#### The growth rate behind the valuation")
    g_used = _num(r.get("growth_5y_cagr"))
    g_trend = _num(r.get("growth_5y_trend"))
    g_cagr = _num(r.get("growth_5y_cagr_raw"))
    basis = r.get("growth_basis")

    k = st.columns(4)
    k[0].metric("Growth used for the multiple", fmt_pct(g_used),
                help="**What it is** — the growth rate that sets this "
                     "company's fair P/E, under Lynch's PEG = 1 rule.\n\n"
                     "**Formula** — picked from a ladder: 5-year trend first, "
                     "then the 5-year CAGR, then the 3-year windows. The first "
                     "measure that can be computed wins.\n\n**How to read it** "
                     "— change this number and the fair value on the Valuation "
                     "tab moves with it. It is the single most consequential "
                     "figure in the model.")
    k[1].metric("5-year trend", fmt_pct(g_trend),
                help="**What it is** — the growth rate that best describes the "
                     "whole five-year path, not just its two ends.\n\n"
                     "**Formula** — least-squares regression on log(EPS) over "
                     "five years.\n\n**How to read it** — it uses every point "
                     "in the window, so one odd quarter barely moves it. This "
                     "is why it sits first on the ladder.")
    k[2].metric("5-year CAGR", fmt_pct(g_cagr),
                help="**What it is** — the compound rate between the first and "
                     "last point, ignoring everything in between.\n\n"
                     "**Formula** — ((EPS now ÷ EPS five years ago) ^ (1/5)) − "
                     "1.\n\n**How to read it** — shown next to the trend so "
                     "the difference is visible: when the two disagree widely, "
                     "one of the two endpoints is not representative.")
    k[3].metric("EPS growth YoY", fmt_pct(_num(r.get("eps_growth_yoy"))),
                help="**What it is** — the most recent year of growth, on its "
                     "own.\n\n**Formula** — trailing-twelve-month EPS against "
                     "the point closest to one year earlier, compared by date "
                     "and not by position in the series.\n\n**How to read it** "
                     "— far above the five-year rate means the growth is "
                     "accelerating, or that one exceptional quarter is in the "
                     "window.")
    if pd.notna(basis):
        st.caption(f"Measure actually used: **{basis}**.")
    if g_trend is not None and g_cagr is not None and abs(g_trend - g_cagr) > 10:
        st.info(
            f"The trend ({g_trend:+.0f}%) and the endpoint CAGR ({g_cagr:+.0f}%) "
            f"differ by {abs(g_trend - g_cagr):.0f} points. That gap means one of "
            "the two endpoints is not representative — usually a depressed "
            "starting quarter, which inflates the CAGR. The model uses the trend "
            "precisely so that a single quarter cannot set the fair multiple.")

    # ---------------- MATRICE DEI CAGR ----------------
    st.divider()
    st.markdown("#### Compound growth, three windows")
    st.caption(
        "Each rate is computed on the trailing-twelve-month series, so it "
        "compares twelve full months with twelve full months — never a quarter "
        "against a quarter, which would measure seasonality. A rate is left "
        "blank when the starting value is zero or negative: a compound rate "
        "from a loss has no meaning.")
    cagr_rows = []
    for base in CAGR_METRICS:
        rowd = {"Metric": CAGR_LABELS[base]}
        any_val = False
        for h in CAGR_HORIZONS:
            v = _num(r.get(f"{base}_{h}y"))
            rowd[f"{h}y CAGR"] = fmt_pct(v) if v is not None else "—"
            any_val = any_val or v is not None
        if any_val:
            cagr_rows.append(rowd)
    if cagr_rows:
        st.dataframe(pd.DataFrame(cagr_rows), use_container_width=True,
                     hide_index=True)
    else:
        st.info("Not enough filed history to compute compound growth rates.")

    peers, peer_label, n_peers = peer_reference(r, ind_med, sec_med)
    metric_grid([metric_item(k_, r, peers) for k_ in
                 ("cagr_eps_5y", "cagr_revenue_5y", "cagr_fcf_5y",
                  "cagr_net_income_5y")])
    if peers is not None:
        st.caption(f"Deltas vs the median of the {peer_label}.")

    # ---------------- CHI E' CRESCIUTO DI PIU' ----------------
    if a is not None:
        st.divider()
        st.markdown("#### Who grew, and by how much")
        fig = charts.indexed(a["year"], [("Revenue", _col(a, "revenue")),
                                         ("Net income", _col(a, "net_income")),
                                         ("Free cash flow", _col(a, "fcf")),
                                         ("EPS", _col(a, "eps"))])
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True,
                            key="growth_indexed")
            skipped = getattr(fig, "_skipped", [])
            note = ("Every series rebased to 100 at the first fiscal year shown, "
                    "so quantities of completely different size — revenue in "
                    "billions, EPS in dollars — sit on **one** axis and can "
                    "actually be compared. A second vertical scale would have "
                    "made their alignment arbitrary.")
            if skipped:
                note += (" Not shown: " + ", ".join(skipped) + " — the first "
                         "year is negative, zero, or far below that series' own "
                         "usual level. An index built on a base like that "
                         "measures how unusual the first year was, not growth.")
            st.caption(note)

        # ---------------- PER AZIONE ----------------
        st.markdown("#### Per share")
        st.caption(
            "The only view that accounts for dilution and buybacks: a company "
            "whose revenue grows while its share count grows faster is not "
            "getting bigger for the person holding one share.")
        fig = charts.lines(a["year"], [("EPS", _col(a, "eps"))],
                           ylab="$ per share", height=260, suffix="")
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True,
                            key="growth_eps_line")
        peers_ps, _, _ = peer_reference(r, ind_med, sec_med)
        kpi_row(r, peers_ps, ("eps_ttm", "revenue_per_share", "fcf_per_share",
                              "book_value_per_share"))
        kpi_row(r, peers_ps, ("shares_outstanding",), ncols=4)

    # ---------------- GLI ESTREMI DI OGNI TASSO ----------------
    #
    # RICHIUSO. E' una tabella di quindici righe di controllo — metrica,
    # finestra, i due estremi, gli anni, il tasso — che serve a rifare il conto
    # a mano quando un numero sorprende, non a leggere la crescita. Aperta,
    # occupava piu' spazio di tutto il resto della scheda.
    st.divider()
    with st.expander("🔎 Every growth rate, with the two numbers it comes from",
                     expanded=False):
        render_cagr_audit(ticker, r)


# Come si ottiene ogni voce della scheda finanziaria: formula in chiaro, da
# dove vengono gli ingredienti e, dove esiste, il concetto XBRL con cui
# verificarla sulle API della SEC. E' il materiale del sanity check: senza la
# formula un numero non si controlla, e senza la fonte non si sa nemmeno cosa
# controllare.
METRIC_PROVENANCE: tuple[tuple[str, str, str, str], ...] = (
    # (voce, formula, fonte, chiave in fin["concepts"])
    ("Revenue (TTM)", "sum of the last 4 discrete quarters",
     "SEC XBRL company facts", "revenue"),
    ("Net income (TTM)", "sum of the last 4 discrete quarters",
     "SEC XBRL company facts", "net_income"),
    ("EBIT (TTM)", "sum of the last 4 discrete quarters; pre-tax income where "
     "EBIT is not tagged (banks)", "SEC XBRL company facts", "ebit"),
    ("Operating cash flow (TTM)",
     "quarters recovered by differencing the year-to-date figures, then summed",
     "SEC XBRL company facts", "ocf"),
    ("Capital expenditure (TTM)", "same year-to-date differencing as cash flow",
     "SEC XBRL company facts", "capex"),
    ("Free cash flow", "operating cash flow − capital expenditure, aligned by date",
     "derived from the two above", ""),
    ("Total assets", "latest filed balance sheet", "SEC XBRL company facts", "assets"),
    ("Shareholders' equity", "latest filed balance sheet",
     "SEC XBRL company facts", "equity"),
    ("Long term debt", "latest filed balance sheet",
     "SEC XBRL company facts", "long_term_debt"),
    ("EPS (TTM)", "sum of 4 discrete quarterly EPS, split-adjusted; arbitrated "
     "against the provider's trailing EPS", "SEC XBRL + market data provider", ""),
    ("ROE", "net income TTM ÷ average equity over the same 12 months",
     "derived", ""),
    ("ROIC", "EBIT TTM × (1 − 21%) ÷ (equity + total debt − cash). The 21% US "
     "federal rate is applied to every company, not each one's effective rate",
     "derived", ""),
    ("Equity ratio", "equity ÷ total assets", "derived", ""),
    ("Debt / assets", "total debt ÷ total assets", "derived", ""),
    ("Net debt / equity", "(total debt − cash) ÷ equity", "derived", ""),
    ("Asset turnover", "revenue TTM ÷ total assets", "derived", ""),
    ("Earning power", "EBIT TTM ÷ total assets", "derived", ""),
    ("Net / operating margin", "net income or EBIT ÷ revenue, both TTM",
     "derived", ""),
    ("FCF / OCF margin", "free cash flow or operating cash flow ÷ revenue, "
     "both TTM", "derived", ""),
    ("FCF conversion", "free cash flow ÷ net income, both TTM; blank on a loss",
     "derived", ""),
    ("Capex (TTM) · capex / revenue",
     "operating cash flow − free cash flow; then ÷ revenue", "derived", ""),
    ("P/E (trailing)", "price ÷ the EPS above (recomputed, never copied)",
     "derived", ""),
    ("Earnings yield", "100 ÷ trailing P/E", "derived", ""),
    ("P/S · P/FCF · FCF yield", "market cap ÷ revenue, ÷ FCF; FCF ÷ market cap",
     "derived", ""),
    ("Payout ratio", "(dividend yield × market cap) ÷ net income TTM",
     "derived from provider + filings", ""),
    ("Market cap", "provider field, or price × shares outstanding when missing",
     "market data provider", ""),
    ("P/E (forward)", "price ÷ analyst consensus EPS — an expectation, not a filing",
     "market data provider", ""),
    ("Next earnings", "provider's expected date",
     "market data provider", ""),
    ("Dividend yield", "annual dividend per share ÷ price",
     "market data provider", ""),
)


def render_metric_provenance(ticker: str, r: pd.Series) -> None:
    """Tabella: ogni metrica, la sua formula, la sua fonte, il tag XBRL usato."""
    st.markdown("**3. Where the data comes from — every figure, its formula "
                "and its source**")
    st.caption(
        "This is the table the **Financials** tab points to. Everything there "
        "is computed from the company's own SEC filings, except the rows "
        "marked as provider data — no filing contains a share price. The "
        "**XBRL concept** column names the exact tag read for this company: "
        "more than one means the company changed tag over time (ASC 606 moved "
        "all revenue to a new tag in 2018) and the series spans that boundary. "
        "The *Raw filed data* link opens the untouched facts the SEC holds for "
        "that tag, which is where a suspicious number is settled."
    )
    used: dict[str, str] = {}
    raw = r.get("concepts_used")
    if isinstance(raw, str) and raw:
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                used[k] = v

    cik = r.get("cik")
    cik_str = None
    if pd.notna(cik):
        try:
            cik_str = str(int(float(cik))).zfill(10)
        except (TypeError, ValueError):
            cik_str = None

    rows = []
    for label, formula, source, key in METRIC_PROVENANCE:
        concept = used.get(key, "") if key else ""
        link = ""
        if concept and cik_str:
            # companyconcept espone i fatti grezzi depositati per QUEL tag: e' il
            # posto dove il numero si verifica, non una pagina di riepilogo.
            first = concept.split("+")[0]
            link = (f"https://data.sec.gov/api/xbrl/companyconcept/"
                    f"CIK{cik_str}/us-gaap/{first}.json")
        rows.append({"Figure": label, "Formula": formula, "Source": source,
                     "XBRL concept": concept.replace("+", " + ") or "—",
                     "Verify": link})
    st.dataframe(
        pd.DataFrame(rows), use_container_width=True, hide_index=True,
        column_config={"Verify": st.column_config.LinkColumn(
            "Raw filed data", display_text="open ↗")})

    links = []
    if cik_str:
        links.append(f"[All company facts (one JSON)]"
                     f"(https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_str}.json)")
        links.append(f"[Filing history]"
                     f"(https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                     f"&CIK={cik_str}&type=10-K&dateb=&owner=include&count=40)")
    links.append(f"[Financial statements viewer]"
                 f"(https://www.sec.gov/cgi-bin/viewer?action=view&ticker={ticker})")
    links.append(f"[Provider page](https://finance.yahoo.com/quote/{ticker}/financials)")
    st.markdown("🔗 " + " · ".join(links))

    basis = r.get("financials_basis")
    if isinstance(basis, str) and basis.startswith("annual:"):
        st.caption("⚠️ For **" + basis.split(":", 1)[1].replace(",", ", ") +
                   "** this company files only annual figures: those rows are "
                   "the latest full fiscal year, not the last twelve months.")


def render_cagr_audit(ticker: str, r: pd.Series) -> None:
    """
    Ogni CAGR con i due estremi da cui nasce, per rifare il conto a mano.

    Un tasso di crescita e' il numero piu' facile da far sembrare qualunque cosa:
    basta scegliere l'anno di partenza. Mostrare gli estremi rende la scelta
    ispezionabile — e rende immediatamente visibile il caso in cui un "+98%
    l'anno" dipende da una base di partenza depressa.
    """
    st.caption(
        "The audit trail behind every rate on this page: a growth rate is the "
        "easiest number in finance to make say anything, because you only have "
        "to choose the starting year. Showing both endpoints makes that choice "
        "inspectable.")
    if cagr_detail is None:
        st.caption("Not available: regenerate the dataset to produce "
                   "`data/cagr_detail.csv`.")
        return
    d = cagr_detail[cagr_detail["ticker"] == ticker]
    if d.empty:
        st.caption("No growth rate was calculable for this ticker — the starting "
                   "value was zero or negative on every window.")
        return

    def _fmt_val(metric, v):
        if pd.isna(v):
            return "—"
        return f"${v:,.2f}" if metric == "eps" else fmt_money(v)

    show = pd.DataFrame({
        "Metric": d["metric"].map(CAGR_LABELS.get).fillna(d["metric"]),
        "Window": d["horizon_years"].map(lambda h: f"{int(h)}y"),
        "Series": d["series_basis"].map({"ttm": "TTM (rolling 12m)",
                                         "annual": "fiscal years"}),
        "From": [f"{sd}  {_fmt_val(m, sv)}" for sd, sv, m
                 in zip(d["start_date"], d["start_value"], d["metric"])],
        "To": [f"{ed}  {_fmt_val(m, ev)}" for ed, ev, m
               in zip(d["end_date"], d["end_value"], d["metric"])],
        "Years": d["span_years"].map(lambda v: f"{v:.2f}"),
        "Rate": d["cagr_pct"].map(lambda v: f"{v:+.2f}%"),
    })
    st.dataframe(show, use_container_width=True, hide_index=True)
    st.caption(
        "Check any row with `((To ÷ From) ^ (1 ÷ Years)) − 1`. The starting point "
        "is the one **closest to** the requested window, not the first inside it, "
        "so *Years* lands near the nominal window instead of a quarter short. A "
        "window is missing from this table when its starting value was zero or "
        "negative: a compound rate from a loss has no meaning, so none is shown "
        "anywhere in the app. *Series* says whether the rate is measured on "
        "rolling twelve-month figures or on fiscal years — rates on different "
        "bases are not directly comparable."
    )
    n_missing = len(CAGR_METRICS) * len(CAGR_HORIZONS) - len(d)
    if n_missing > 0:
        st.caption(f"{n_missing} of {len(CAGR_METRICS) * len(CAGR_HORIZONS)} "
                   "metric/window combinations are not calculable for this "
                   "company and are therefore blank throughout the app.")


def render_eps_arbitration(ticker: str, r: pd.Series, compact: bool = False) -> None:
    """
    Da dove viene l'EPS, e cosa fare quando le due fonti non concordano.

    Compare in DUE posti, di proposito e con due livelli di dettaglio. Nella
    scheda Valuation in forma compatta, perche' l'EPS e' il numeratore di tutto
    cio' che quella scheda mostra e chi guarda un fair value ha diritto di
    vedere subito se il numero da cui nasce e' conteso. In Data quality per
    esteso, con la procedura per disambiguare.
    """
    g = lambda k: r[k] if k in r.index and pd.notna(r[k]) else None  # noqa: E731
    eps_used = _num(g("eps_ttm"))
    price_x = _num(g("current_price"))
    div = _num(g("eps_divergence_pct"))
    flag = g("eps_flag")

    if compact:
        contested = flag == "check"
        label = ("⚠️ The two EPS sources disagree — open for detail"
                 if contested else "✓ EPS sources agree — where this number comes from")
        with st.expander(label, expanded=False):
            src = pd.DataFrame({
                "Source": ["yfinance (trailingEps)", "SEC EDGAR (rebuilt TTM)",
                           "Used on this page"],
                "EPS": [f"${v:.2f}" if v is not None else "—"
                        for v in (_num(g("eps_ttm_yf")), _num(g("eps_ttm_edgar")),
                                  eps_used)]})
            st.dataframe(src, use_container_width=True, hide_index=True)
            if g("eps_source"):
                st.caption(f"Chosen automatically: **{g('eps_source')}**"
                           + (f" · divergence {div:.1f}%" if div is not None else ""))
            if contested:
                st.warning(
                    "Over 15% apart. The full disambiguation procedure — implied "
                    "P/E test, comparison with the normalized figure, links to "
                    "the filings — is on the **Data quality** tab.")
        return

    st.markdown("**1. Where the EPS comes from (the two sources compared)**")
    src = pd.DataFrame({
        "Source": ["yfinance (trailingEps)", "SEC EDGAR (rebuilt TTM)",
                   "Used in the calculation"],
        "EPS": [f"${v:.2f}" if v is not None else "—"
                for v in (_num(g("eps_ttm_yf")), _num(g("eps_ttm_edgar")), eps_used)],
    })
    st.dataframe(src, use_container_width=True, hide_index=True)
    if g("eps_source"):
        st.caption(f"Source chosen automatically: **{g('eps_source')}**")
    if g("eps_basis") == "edgar-derived":
        st.info("**Derived EPS.** " + (g("eps_basis_note") or ""), icon="ℹ️")
    elif g("eps_basis") == "edgar-stale":
        st.warning("**SEC data one period behind.** " + (g("eps_basis_note") or ""),
                   icon="🕒")
    _age = _num(g("eps_series_age_days"))
    if _age is not None:
        st.caption(f"Most recent SEC data point: {int(_age)} days ago · "
                   f"series ends {g('eps_last_date') or '—'}")
    if g("classification_source") and g("classification_source") != "yfinance":
        st.caption(f"ℹ️ Sector/industry resolved via SEC SIC code "
                   f"({g('sic')} — {g('sic_description') or 'n/a'}) because the "
                   "market data provider left them empty.")
    if g("missing_inputs"):
        st.caption("⚠️ Inputs unavailable for this ticker, declared rather than "
                   "treated as zero: " + str(g("missing_inputs")).replace(";", ", "))

    if div is None:
        return
    msg = f"Divergence between sources: **{div:.1f}%** · status: `{flag}`"
    if flag != "check":
        st.success(msg + " — the sources agree.")
        return
    st.warning(msg + " — over 15%: worth verifying.")
    rows_chk = []
    for name, val in (("yfinance", _num(g("eps_ttm_yf"))),
                      ("SEC EDGAR", _num(g("eps_ttm_edgar")))):
        if val and val > 0 and price_x:
            ipe = price_x / val
            verdict = ("plausible" if 2 <= ipe <= 60 else
                       "suspicious" if ipe <= 150 else "implausible")
            rows_chk.append({"Source": name, "EPS": f"${val:.2f}",
                             "Implied P/E": f"{ipe:.1f}", "Assessment": verdict})
    if rows_chk:
        st.markdown("**How to disambiguate** — the implied P/E "
                    "(price ÷ EPS) is the quickest test:")
        st.dataframe(pd.DataFrame(rows_chk), use_container_width=True,
                     hide_index=True)
    norm_x = _num(g("eps_normalized_3y"))
    st.markdown("\n\n".join([
        "**1. Look at the implied P/E.** A P/E over 150 or under 2 almost "
        "always signals a wrong EPS, not an extreme stock.",
        f"**2. Compare with history.** The 3-year normalized EPS is "
        f"{('$%.2f' % norm_x) if norm_x else 'unavailable'}: the correct source "
        "usually resembles it.",
        "**3. Typical causes.** yfinance sometimes reports a single quarter's "
        "EPS instead of TTM, or doesn't update after a split. EDGAR can be "
        "wrong when the Q4 reconstruction runs into restated data.",
        "**4. Verify at the source.** The official figure is in the latest "
        "10-Q/10-K on SEC EDGAR (link below): look for *Diluted earnings per "
        "share* in the income statement.",
    ]))
    st.markdown(
        f"🔗 [SEC filings for {ticker}]"
        f"(https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
        f"&ticker={ticker}&type=10-Q&dateb=&owner=include&count=10) · "
        f"[Yahoo Finance page](https://finance.yahoo.com/quote/{ticker}/"
        f"key-statistics)")


def render_quality_tab(ticker: str, r: pd.Series) -> None:
    """
    "Da dove viene ogni numero, e cosa non fidarsi."

    Questa scheda contiene ORA solo provenienza e affidabilita'. Prima ospitava
    anche il conto del fair value, la derivazione dell'EPS e gli estremi di ogni
    CAGR: tre cose che sono valutazione e crescita, non qualita' del dato, e che
    infatti nessuno andava a cercare qui. Sono nelle schede a cui appartengono;
    quello che resta e' il materiale per non fidarsi del programma.
    """
    g = lambda k: r[k] if k in r.index and pd.notna(r[k]) else None  # noqa: E731

    st.caption(
        "**Where every number on this page comes from.** The financial "
        "statements are rebuilt from the company's own SEC filings (XBRL "
        "company facts); price, market cap, the analyst consensus and the "
        "earnings date come from the market data provider, because no filing "
        "contains a share price. Everything else is derived from those two by "
        "the formulas listed below. The fair value derivation lives on "
        "**Valuation**, the growth endpoints on **Growth**, and the filings "
        "themselves on **Filings** — each next to the thing it explains. What "
        "is left here is the material for checking the machine rather than the "
        "company.")

    render_eps_arbitration(ticker, r, compact=False)

    st.markdown("**2. How the classification was decided**")
    cls_rows = [
        ("Lynch category", str(g("lynch_category") or "—")),
        ("Confidence", str(g("lynch_confidence") or "—")),
        ("Why that confidence", str(g("lynch_confidence_note") or "—")),
        ("Valuation anchor", str(g("lynch_anchor") or "—")),
        ("Earnings base used", str(g("lynch_eps_base") or "—")),
        ("Multiple basis", str(g("lynch_pe_basis") or "—")),
        ("Growth measure used", str(g("growth_basis") or "—")),
        ("Earnings volatility", fmt_ratio(_num(g("earnings_volatility")), 0)),
        ("Loss periods in last 5y", str(int(_num(g("loss_periods_5y")) or 0))),
        ("Distinct loss episodes", str(int(_num(g("loss_episodes_5y")) or 0))),
        ("Years since last loss", fmt_ratio(_num(g("years_since_last_loss")), 1)),
    ]
    st.dataframe(pd.DataFrame(cls_rows, columns=["Input", "Value"]),
                 use_container_width=True, hide_index=True)
    st.caption(
        "These are the inputs the classifier actually saw. Earnings volatility "
        "is a **median absolute deviation** of year-on-year changes, not a "
        "standard deviation: one isolated shock — a 2020 collapse, a one-off "
        "write-down — must not make a stable company look cyclical. Loss "
        "*episodes* are counted separately from loss *periods* because a single "
        "bad year smeared across four trailing-twelve-month readings is one "
        "crisis, not four.")

    render_metric_provenance(ticker, r)

    st.markdown("**4. Data quality and provenance**")
    meta = pd.DataFrame({
        "Item": ["EPS series method", "EPS data points available", "Last EPS data point",
                 "Splits adjusted", "Anchoring factor", "Price source",
                 "Sector/industry source", "Exchange", "SIC code"],
        # all values as strings: a mixed-type column breaks Arrow serialization
        "Value": [
            str(g("eps_method") or "—"),
            str(int(_num(g("eps_points")))) if _num(g("eps_points")) else "—",
            str(g("eps_last_date") or "—"),
            str(int(_num(g("n_splits")))) if _num(g("n_splits")) is not None else "—",
            f"{_num(g('eps_scale_applied')):.3f}" if _num(g("eps_scale_applied")) else "none",
            str(g("price_source") or "—"),
            str(g("classification_source") or "—"),
            str(g("exchange") or "—"),
            f"{g('sic')} — {g('sic_description')}" if g("sic") else "—",
        ],
    })
    st.dataframe(meta, use_container_width=True, hide_index=True)

    ev_t = events[events["ticker"] == ticker] if events is not None else None
    if ev_t is not None and not ev_t.empty:
        st.markdown("**5. Corporate events detected**")
        show_ev = ev_t.copy()
        show_ev["date"] = show_ev["date"].dt.strftime("%b %d, %Y")
        show_ev["type"] = show_ev["type"].str.capitalize()
        st.dataframe(show_ev[["date", "type", "detail"]]
                     .rename(columns={"date": "Date", "type": "Type",
                                      "detail": "Detail"}),
                     use_container_width=True, hide_index=True)

    st.caption(
        "The filings these figures were read from are on the **📄 Filings** "
        "tab, with a direct link to each document on SEC EDGAR.")


# Cosa contiene ciascun modulo, in una riga. Un elenco di sigle non dice a chi
# legge quale documento aprire, ed e' esattamente la domanda che si fa in questa
# scheda: "dove sta il numero che voglio verificare?".
# NIENTE MARKDOWN: st.dataframe stampa gli asterischi cosi' come sono.
FORM_DESCRIPTIONS = {
    "10-K": "Annual report, audited — the source of the yearly tables.",
    "10-Q": "Quarterly report, unaudited — four of these make the TTM figures.",
    "8-K": "Material event — acquisition, results release, change of auditor.",
    "20-F": "Annual report of a foreign issuer — the 10-K equivalent.",
    "40-F": "Annual report of a Canadian issuer (MJDS).",
    "6-K": "Interim report of a foreign issuer — the 10-Q equivalent.",
    "S-1": "Registration statement — a first sale of shares to the public.",
    "DEF 14A": "Proxy statement — pay, board, shareholder-meeting motions.",
}


def render_filings_tab(ticker: str, r: pd.Series) -> None:
    """
    I documenti depositati alla SEC — una scheda propria, non una coda della
    scheda di audit.

    Stavano in fondo a Data quality come sesta sezione, dopo l'arbitraggio
    dell'EPS, la classificazione, la tabella delle formule e i metadati: cioe'
    nel punto in cui si arriva solo scorrendo tutto il resto. Ma un deposito
    alla SEC non e' una diagnostica del programma, e' la FONTE: e' il documento
    che si apre quando un numero non convince, ed e' anche l'unico modo per
    sapere se sono uscite trimestrali piu' recenti dei dati caricati qui.
    """
    fil_t = filings[filings["ticker"] == ticker] if filings is not None else None

    st.markdown("#### The documents every figure on this page is read from")
    st.caption(
        "This dashboard never types a number in by hand: the financial "
        "statements are parsed from the XBRL data attached to these filings. "
        "Open one and you are looking at exactly what the company told the "
        "regulator — the same income statement, the same balance sheet.")

    last_eps = r.get("eps_last_date")
    age = _num(r.get("eps_series_age_days"))
    nxt = r.get("next_earnings_date")
    c = st.columns(3)
    c[0].metric("Most recent SEC data point", str(last_eps) if pd.notna(last_eps)
                else "—",
                help="**What it is** — the period end of the newest figure "
                     "used on this page.\n\n**How to read it** — if a quarter "
                     "has been reported after this date, this company's data "
                     "is one period behind and the discount shown may already "
                     "be stale.")
    c[1].metric("Age of that data", f"{int(age)} days" if age is not None else "—",
                help="**What it is** — how long ago that period ended.\n\n"
                     "**How to read it** — anything much beyond 100 days means "
                     "a quarter is probably missing: check the filing list "
                     "below against it.")
    c[2].metric("Next earnings date", str(nxt) if pd.notna(nxt) else "—",
                help="**What it is** — the date the provider expects the next "
                     "results.\n\n**How to read it** — an expectation, not a "
                     "commitment: it is the one date on this page that has not "
                     "happened yet.")

    st.markdown("##### Recent 10-K and 10-Q filings")
    if fil_t is None or fil_t.empty:
        st.info("No recent 10-K/10-Q filings on file for this ticker yet. The "
                "build stores the latest filings per ticker in "
                "`data/filings.csv`; regenerate the dataset to populate it.",
                icon="📄")
    else:
        show_fil = fil_t.rename(columns={"form": "Form", "filing_date": "Filed",
                                         "period_date": "Period"})
        if "Form" in show_fil.columns:
            show_fil["What it is"] = show_fil["Form"].map(
                lambda f: FORM_DESCRIPTIONS.get(str(f).strip(), "—"))
        cols = [c_ for c_ in ("Form", "What it is", "Filed", "Period", "url")
                if c_ in show_fil.columns]
        st.dataframe(
            show_fil[cols], use_container_width=True, hide_index=True,
            column_config={
                "url": st.column_config.LinkColumn("Document",
                                                   display_text="Open ↗"),
                "What it is": st.column_config.Column(width="large"),
                "Filed": st.column_config.Column(
                    help="When the document was submitted to the SEC."),
                "Period": st.column_config.Column(
                    help="The fiscal period the document covers. This, not the "
                         "filing date, is what the figures refer to — a 10-K "
                         "filed in February reports the year that ended in "
                         "December."),
            })
        st.caption(
            "**Filed** is when it reached the SEC, **Period** is what it "
            "covers: the gap between the two is the reporting lag, usually "
            "four to eight weeks. A 10-K filed today does not mean today's "
            "numbers.")

    st.markdown("##### Go to the source")
    cik = r.get("cik")
    cik_str = None
    if pd.notna(cik):
        try:
            cik_str = str(int(float(cik))).zfill(10)
        except (TypeError, ValueError):
            cik_str = None

    links = [
        (f"All filings for {ticker} on EDGAR",
         f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
         f"&ticker={ticker}&type=10-K&dateb=&owner=include&count=40",
         "The complete filing history, every form type."),
        (f"Quarterly reports (10-Q) for {ticker}",
         f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
         f"&ticker={ticker}&type=10-Q&dateb=&owner=include&count=10",
         "Where to check a contested EPS: look for *Diluted earnings per "
         "share* in the income statement."),
        ("Financial statements viewer",
         f"https://www.sec.gov/cgi-bin/viewer?action=view&ticker={ticker}",
         "The SEC's own reader: the statements rendered as tables, no XBRL "
         "needed."),
    ]
    if cik_str:
        links.append(
            ("All company facts (one JSON)",
             f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_str}.json",
             "Every tagged figure this company has ever filed — the raw "
             "material this dashboard is built from."))
    links.append(
        ("Provider page", f"https://finance.yahoo.com/quote/{ticker}/financials",
         "The market data side: price, market cap, analyst consensus."))

    st.dataframe(
        pd.DataFrame([{"Source": n, "What you find there": d, "Link": u}
                      for n, u, d in links]),
        use_container_width=True, hide_index=True,
        column_config={"Link": st.column_config.LinkColumn(
            "Link", display_text="open ↗")})

    st.caption(
        "Corporate events read out of these filings — splits above all — are "
        "listed on the **🔍 Data quality** tab, because what they affect is the "
        "comparability of the series rather than the accounts themselves.")


if nav == "Details":
    tickers = sorted(hist["ticker"].unique())
    if not tickers:
        st.error("No tickers in the history file.")
        st.stop()
    pre = st.session_state.get("goto_ticker")
    if pre in tickers:
        default_tk = pre
    else:
        under = fund[fund["valuation"] == "Undervalued"]["ticker"].tolist()
        default_tk = next((t for t in under if t in tickers), tickers[0])
    _init("hist_ticker", default_tk)
    if st.session_state["hist_ticker"] not in tickers:
        st.session_state["hist_ticker"] = default_tk   # dataset changed under us

    top = st.columns([2, 3])
    with top[0]:
        ticker = st.selectbox("Ticker", tickers, key="hist_ticker")
    row = fund[fund["ticker"] == ticker]
    with top[1]:
        if not row.empty:
            r0 = row.iloc[0]
            bits = [f"**{r0.get('company', ticker)}**"]
            if pd.notna(r0.get("sector")):
                bits.append(str(r0["sector"]))
            if pd.notna(r0.get("industry")):
                bits.append(f"*{r0['industry']}*")
            if pd.notna(r0.get("lynch_category")):
                bits.append(f"{lynch_icon(r0['lynch_category'])} {r0['lynch_category']}")
            st.markdown("&nbsp;\n\n" + " · ".join(bits))

    # L'ORDINE E' UN PERCORSO: quanto vale, com'e' fatta, quanto cresce, da
    # dove vengono i documenti, quanto fidarsi dei dati. I depositi alla SEC
    # hanno una scheda propria — erano l'ultima sezione di Data quality, cioe'
    # visibili solo a chi scorreva fino in fondo una pagina di diagnostica,
    # mentre sono la fonte di tutto il resto.
    tab_val, tab_fin, tab_growth, tab_fil, tab_qual = st.tabs(
        ["📉 Valuation", "📊 Financials", "📈 Growth (5y)", "📄 Filings",
         "🔍 Data quality"])
    with tab_val:
        render_valuation_tab(ticker, row)
    if row.empty:
        for tab in (tab_fin, tab_growth, tab_fil, tab_qual):
            with tab:
                st.info("No summary row for this ticker in fundamentals.csv.")
    else:
        r_series = row.iloc[0]
        with tab_fin:
            render_financials_tab(ticker, r_series)
        with tab_growth:
            render_growth_tab(ticker, r_series)
        with tab_fil:
            render_filings_tab(ticker, r_series)
        with tab_qual:
            render_quality_tab(ticker, r_series)


# ===========================================================================
# SCREENER VIEW
# ===========================================================================

if nav == "Screener":
    f = fund.copy()

    # P/E gap against the stock's own peer group: the same P/E means opposite
    # things in software and in banking, so the absolute number is only half
    # the information. Computed on the FULL dataset, before any filter, so the
    # benchmark doesn't shift as the user narrows the selection.
    #
    # SAME FALLBACK AS THE DETAILS PAGE. An industry with four members is not a
    # benchmark, but leaving the cell empty was worse: the Details tab compared
    # that company against its sector and showed a number, while the Screener
    # showed a dash for the same row. On the S&P 500 that hit 31% of rows —
    # 111 industries for 501 companies, so 69 of them never reach five members.
    # Now both views use the same ladder, industry → sector → nothing, and the
    # table marks a sector-based figure with an asterisk instead of hiding it.
    if has_industry and "pe_ratio" in f.columns:
        f["_ind_pe"] = f["industry"].map(ind_med["pe_ratio"])
        f["_ind_n"] = f["industry"].map(ind_med["_n"]).fillna(0)
        f["_sec_pe"] = f["sector"].map(sec_med["pe_ratio"]) if not sec_med.empty else np.nan
        f["_sec_n"] = (f["sector"].map(sec_med["_n"]).fillna(0)
                       if not sec_med.empty else 0)

        use_ind = (f["_ind_n"] >= MIN_PEERS) & f["_ind_pe"].notna() & (f["_ind_pe"] > 0)
        use_sec = (~use_ind) & (f["_sec_n"] >= MIN_PEERS) & \
                  f["_sec_pe"].notna() & (f["_sec_pe"] > 0)
        bench = np.where(use_ind, f["_ind_pe"], np.where(use_sec, f["_sec_pe"], np.nan))
        f["pe_peer_basis"] = np.where(use_ind, "industry",
                                      np.where(use_sec, "sector", ""))
        with np.errstate(invalid="ignore", divide="ignore"):
            f["pe_vs_industry_pct"] = np.where(
                f["pe_ratio"].notna() & ~pd.isna(bench),
                (f["pe_ratio"] / bench - 1) * 100, np.nan)

    with st.expander("📖 Lynch category definitions & how classification works",
                     expanded=False):
        st.caption(
            "Every ticker is classified into one of Peter Lynch's company types "
            "(from *One Up on Wall Street*), and the category decides the "
            "**valuation anchor** — usually a fair P/E, but not always. Checks "
            "run in this order: the first match wins, because the conditions "
            "that invalidate the P/E have to be tested before the growth bands. "
            "A loss-making company growing 40% is not a fast grower — that 40% "
            "is a rebound."
        )
        st.markdown(
            "**1. 🔧 Turnaround** — *currently* loss-making, **or** back to "
            "profit less than a year ago, **or** more than one distinct loss "
            "episode in five years with the last one still recent. Anchor: "
            "**P/E 10 on 3-year normalized earnings** — a recovery multiple, "
            "not a PEG multiple. Where not even the normalized figure is "
            "positive, no anchor is given and the app says so.\n\n"
            "**2. 🏗️ Asset Play** — price-to-book below 1. Anchor: **book "
            "value per share** (fair P/B = 1). The P/E is not the tool here; "
            "Lynch's point is that the balance sheet is the floor.\n\n"
            "**3. 🔄 Cyclical** — a structurally cyclical sector (Energy, Basic "
            "Materials, Industrials, Consumer Cyclical, Real Estate) **and** "
            "erratic earnings. Anchor: **P/E 12 on normalized (mid-cycle) "
            "earnings**, since a low P/E at the top of a cycle is a warning, "
            "not a bargain.\n\n"
            "**4. 🚀 Fast Grower** — growth above 20% measured on a window with "
            "no losses. Fair P/E = growth rate, **capped at 25**. If the same "
            "growth is measured across a loss, the category becomes "
            "**🏛️ Stalwart (recovering)** with the multiple held at 15.\n\n"
            "**5. 🏛️ Stalwart** — growth between 10% and 20%. Fair P/E = growth "
            "rate. Split by size into **Stalwart** (market cap over $50B), "
            "**Stalwart (mid cap)** and **Stalwart (size unknown)**.\n\n"
            "**6. 🐢 Slow Grower** — growth below 10%. Fair P/E = growth + "
            "dividend yield, **floored at 6 and capped at 12**. When growth is "
            "negative the category becomes **Slow Grower (declining earnings)**: "
            "the growth term contributes zero — no credit for shrinking — and "
            "the multiple, built from the dividend and the floor, is applied to "
            "normalized earnings.\n\n"
            "**❓ Unclassified** — growth not calculable on any of the four "
            "windows tried. No category multiple; the fixed P/E 15 line is the "
            "only reading available."
        )
        st.caption(
            "**PEG = 1 principle:** for the growth-based categories the fair "
            "P/E tracks the company's own growth rate, so it **varies stock by "
            "stock even within one category**. The growth figure itself comes "
            "from a ladder — 5-year log-linear trend first, then the 5-year "
            "CAGR, then the 3-year windows — because a multiple set by two "
            "endpoints is a multiple set by two quarters."
        )
        if "lynch_confidence" in f.columns:
            st.caption(
                "**Confidence** accompanies every classification (🟢 high · 🟡 "
                "medium · 🔴 low) and reflects how the growth was measured, how "
                "erratic the earnings are, and whether there were losses in the "
                "window. It is on the Valuation tab of each ticker."
            )
        st.caption(
            "**The Cyclical test is decided on the INDUSTRY, not the sector.** "
            "A sector is too coarse for a rule that imposes a fixed multiple: "
            "*Industrials* holds both steel mills and payroll processors, "
            "*Consumer Cyclical* holds carmakers and Amazon, *Real Estate* "
            "holds offices and cell-tower REITs. Industries whose earnings do "
            "not follow the commodity or capex cycle — internet retail, travel, "
            "defence, healthcare and residential REITs, business services — are "
            "excluded even inside a cyclical sector. And a sector **guessed "
            "from the SEC SIC code**, because the provider had no profile for "
            "that symbol, never triggers the rule at all: a guess is not enough "
            "to impose a P/E."
        )

    def _multi_filter(label: str, options: list[str], key: str, fmt=None):
        """
        Reusable multi-select filter (Sector / Category): aligned "All"/"None"
        buttons, a counter of how many are selected, and — if available — the
        clickable st.pills widget, otherwise an equivalent multiselect.
        """
        # A saved selection can name options that no longer exist (the dataset
        # was rebuilt with a different universe): Streamlit raises on those.
        _sanitize(key, options)
        top = st.columns([3, 1, 1])
        top[0].markdown(f"**{label}**")
        if top[1].button("All", use_container_width=True, key=f"{key}_all"):
            st.session_state[key] = list(options)
            st.rerun()
        if top[2].button("None", use_container_width=True, key=f"{key}_none"):
            st.session_state[key] = []
            st.rerun()
        kwargs = dict(default=_dflt(key, options), key=key,
                      label_visibility="collapsed")
        if fmt:
            kwargs["format_func"] = fmt
        if hasattr(st, "pills"):
            sel = st.pills(label, options, selection_mode="multi", **kwargs)
        else:
            sel = st.multiselect(label, options, **kwargs)
        sel = sel or []
        dot = "🟢" if sel else "⚪"
        st.caption(f"{dot} {len(sel)} / {len(options)} selected")
        return sel

    c1, c2, c3 = st.columns(3)
    with c1:
        model = st.radio("Valuation model", ["Per category (PEG)", "Fixed P/E 15"],
                         horizontal=True, key="screen_model",
                         help="Per category: multiple adapted to the type of "
                              "company. P/E 15: quick filter, the same for everyone.")
    with c2:
        vals = st.multiselect("Valuation", ["Undervalued", "Overvalued", "N/A"],
                              default=_dflt("screen_val",
                                            ["Undervalued", "Overvalued", "N/A"]),
                              key="screen_val",
                              help="N/A = categories without a multiple "
                                   "(Turnaround, Asset Play): the P/E isn't a "
                                   "valid basis. Remove N/A to hide them.")
    with c3:
        # Il valore iniziale si semina in session_state invece di passarlo come
        # `value`: la casella e' fra quelle riscritte su se stesse a ogni run
        # per non perdere lo stato, e dichiarare le due cose insieme fa
        # scrivere a Streamlit un avviso a ogni rerun.
        _init("screen_profit", True)
        only_profit = st.checkbox("Positive EPS only", key="screen_profit")
        _n_neg = int((f["eps_ttm"] <= 0).sum()) if "eps_ttm" in f.columns else 0
        st.caption(f"{'Hides' if only_profit else 'Shows'} {_n_neg} tickers with EPS ≤ 0")

    c4, c5 = st.columns(2)
    with c4:
        with st.container(border=True):
            sectors = sorted(f["sector"].dropna().unique())
            sec = _multi_filter("Sector", sectors, "screen_sec")
    with c5:
        with st.container(border=True):
            if "lynch_category" in f.columns:
                # LE OPZIONI VENGONO DAI DATI, non da un elenco scritto a mano.
                # Prima erano una costante, e ogni categoria non elencata
                # spariva silenziosamente dallo Screener: undici societa'
                # classificate "Stalwart (dimensione non disponibile)" non erano
                # selezionabili in alcun modo e non comparivano in nessun
                # filtro, nemmeno premendo "All". LYNCH_ORDER decide solo
                # l'ORDINE; quello che non vi compare finisce in fondo.
                present = sorted(f["lynch_category"].dropna().unique())
                opts = ([c for c in LYNCH_ORDER if c in present]
                        + [c for c in present if c not in LYNCH_ORDER])
                cat_sel = _multi_filter("Lynch category", opts, "screen_cat",
                                        fmt=lambda c: f"{lynch_icon(c)} {c}")
            else:
                cat_sel = None

    c6, c7 = st.columns([3, 2])
    with c6:
        # Industry is a long list (hundreds on the full market), so it gets a
        # searchable multiselect rather than pills — and an empty selection
        # means "all", which keeps the common case one click away.
        ind_sel = []
        if has_industry:
            ind_options = sorted(f.loc[f["sector"].isin(sec), "industry"]
                                 .dropna().unique()) if sec else sorted(
                f["industry"].dropna().unique())
            _sanitize("screen_ind", ind_options)
            ind_sel = st.multiselect(
                "Industry", ind_options, default=_dflt("screen_ind", []),
                key="screen_ind",
                help="Leave empty for all industries in the selected sectors. "
                     "The list follows the sector filter.")
    with c7:
        search = st.text_input("Search ticker or company", key="screen_search",
                               placeholder="e.g. AAPL, Apple")

    use_peg = model.startswith("Per category")
    val_col = "valuation_peg" if (use_peg and "valuation_peg" in f.columns) else "valuation"
    disc_col = ("discount_vs_peg_pct" if (use_peg and "discount_vs_peg_pct" in f.columns)
                else "discount_vs_fv15_pct")
    fv_col_s = ("fair_value_peg" if (use_peg and "fair_value_peg" in f.columns)
                else "fair_value_pe15")

    f = f[f[val_col].isin(vals) & f["sector"].isin(sec)]
    if cat_sel is not None:
        f = f[f["lynch_category"].isin(cat_sel)]
    if ind_sel:
        f = f[f["industry"].isin(ind_sel)]
    if only_profit:
        f = f[f["eps_ttm"] > 0]
    if search:
        q = search.strip().lower()
        f = f[f["ticker"].str.lower().str.contains(q, na=False)
              | f["company"].astype(str).str.lower().str.contains(q, na=False)]

    f = f.sort_values(disc_col, na_position="last")

    label = ("per-category multiple" if use_peg else "fixed P/E 15")
    st.markdown(f"**{len(f)} tickers** — sorted from most discounted · model: {label}")
    if skipped_tk is not None and not skipped_tk.empty:
        with st.expander(f"⚠️ {len(skipped_tk)} tickers missing from the dataset "
                         "(skipped during data collection)"):
            st.caption(
                "These tickers don't appear in the Screener because the build "
                "couldn't use their data (no EPS on EDGAR, prices unreachable, "
                "ticker not resolved, below the market-cap floor…) — it's not a "
                "problem with the filters above."
            )
            st.dataframe(skipped_tk, use_container_width=True, hide_index=True)

    # EPS reliability indicator: v sources agree, ! worth checking, etc.
    flag_icon = {"ok": "✓", "check": "⚠ check", "yf-only": "~ yf",
                 "edgar-only": "~ edgar", "no-eps": "—"}
    f = f.copy()
    f["_rel"] = f.get("eps_flag", pd.Series(index=f.index)).map(flag_icon).fillna("—")

    cols_show = ["ticker", "company", "sector", "industry", "lynch_category",
                 "lynch_confidence",
                 "current_price", "eps_ttm", "lynch_fair_pe", fv_col_s, disc_col,
                 "pe_ratio", "pe_vs_industry_pct", "growth_5y_cagr",
                 "cagr_revenue_5y", "peg_ratio", "lynch_ratio", "dividend_yield",
                 "roe_pct", "fcf_yield_pct", val_col, "_rel"]
    cols_show = [c for c in cols_show if c in f.columns]
    show = f[cols_show].copy()
    names = {"ticker": "Ticker", "company": "Company", "sector": "Sector",
             "industry": "Industry", "lynch_category": "Category",
             "lynch_confidence": "Conf.",
             "current_price": "Price", "eps_ttm": "EPS used",
             fv_col_s: "Fair Value", disc_col: "Discount %",
             "pe_ratio": "Cur. P/E", "pe_vs_industry_pct": "Δ P/E vs ind.",
             "lynch_fair_pe": "× Fair P/E", "growth_5y_cagr": "EPS growth 5y",
             "cagr_revenue_5y": "Rev. CAGR 5y", "peg_ratio": "PEG",
             "lynch_ratio": "Lynch ratio", "dividend_yield": "Div %",
             "roe_pct": "ROE", "fcf_yield_pct": "FCF yield",
             val_col: "Valuation", "_rel": "EPS data"}
    show.columns = [names.get(c, c) for c in cols_show]

    def color_val(v):
        if v == "Undervalued":
            return "background-color: #dcfce7; color:#166534"
        if v == "Overvalued":
            return "background-color: #fee2e2; color:#991b1b"
        return ""

    def color_rel(v):
        if isinstance(v, str) and "check" in v:
            return "color:#b45309; font-weight:600"
        return "color:#6b7280"

    def color_lr(v):
        if not isinstance(v, (int, float)) or pd.isna(v):
            return ""
        if v > 1.5:
            return "color:#166534; font-weight:600"
        if v < 1:
            return "color:#991b1b"
        return ""

    def color_peg(v):
        # INVERSE convention vs the Lynch ratio: low PEG = cheap
        if not isinstance(v, (int, float)) or pd.isna(v):
            return ""
        if v < 1:
            return "color:#166534; font-weight:600"
        if v > 2:
            return "color:#991b1b"
        return ""

    def color_pe_gap(v):
        # cheaper than the industry median = green, richer = red
        if not isinstance(v, (int, float)) or pd.isna(v):
            return ""
        if v <= -20:
            return "color:#166534; font-weight:600"
        if v >= 20:
            return "color:#991b1b"
        return ""

    COL_FORMATS = {
        "Price": "${:,.2f}", "EPS used": "${:,.2f}", "Fair Value": "${:,.2f}",
        "Discount %": "{:+.0f}%", "Cur. P/E": "{:.1f}", "Δ P/E vs ind.": "{:+.0f}%",
        "× Fair P/E": "{:.0f}", "EPS CAGR 5y": "{:+.1f}%", "Rev. CAGR 5y": "{:+.1f}%",
        "PEG": "{:.2f}", "Lynch ratio": "{:.2f}", "Div %": "{:.2f}%",
        "ROE": "{:.1f}%", "FCF yield": "{:.2f}%",
    }

    def _cell(spec: str | None, v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return "—"
        if spec is None:
            return str(v)
        try:
            return spec.format(v)
        except (ValueError, TypeError):
            return str(v)

    # LA TABELLA E' COSTRUITA DI STRINGHE, non di numeri formattati dallo Styler.
    #
    # Ne' `Styler.format(..., na_rep="—")` ne' un formatter callable arrivano
    # fino allo schermo: Streamlit rende comunque la stringa "None" nelle celle
    # vuote. E le celle vuote qui sono normalissime — il fair P/E di una
    # Turnaround, il FCF yield di una banca, il dividendo di chi non lo paga, il
    # confronto con un'industria che ha meno di cinque societa'. Una tabella
    # cosparsa di "None" si legge come un errore del programma invece che come
    # un'informazione mancante. Formattando qui, cio' che si vede e' certo.
    disp = pd.DataFrame({c: [_cell(COL_FORMATS.get(c), v) for v in show[c]]
                         for c in show.columns}, index=show.index)

    # Un confronto contro il settore, non contro l'industria, va detto: e' un
    # gruppo piu' largo e quindi meno stringente. L'asterisco lo dichiara senza
    # rubare una colonna, ed e' spiegato nel tooltip e nella legenda.
    if "Δ P/E vs ind." in disp.columns and "pe_peer_basis" in f.columns:
        disp["Δ P/E vs ind."] = [
            txt + ("*" if (basis == "sector" and txt != "—") else "")
            for txt, basis in zip(disp["Δ P/E vs ind."], f["pe_peer_basis"])]

    # Il colore viene deciso sui valori NUMERICI originali, non sulle stringhe.
    css = pd.DataFrame("", index=show.index, columns=show.columns)
    for col, fn in (("Valuation", color_val), ("EPS data", color_rel),
                    ("Lynch ratio", color_lr), ("PEG", color_peg),
                    ("Δ P/E vs ind.", color_pe_gap)):
        if col in show.columns:
            css[col] = [fn(v) for v in show[col]]
    styled = disp.style.apply(lambda _: css, axis=None)

    # DUE TABELLE, UNA ACCANTO ALL'ALTRA NEL TEMPO.
    #
    # Quella avanzata (AG Grid) porta filtro per colonna, colonne bloccate e
    # ordinamento sui numeri veri invece che sulle stringhe formattate. Ma e'
    # un componente di terze parti, quindi la classica resta selezionabile: se
    # il componente si rompe con un aggiornamento di Streamlit, lo Screener
    # continua a funzionare cambiando una voce, non aspettando una patch.
    _table_opts = ["Advanced (AG Grid)", "Classic"] if HAS_AGGRID else ["Classic"]
    _init("screen_table_style", _table_opts[0])
    chosen_tk = None
    if HAS_AGGRID:
        st.radio("Table", _table_opts, horizontal=True, key="screen_table_style",
                 label_visibility="collapsed",
                 help="**Advanced**: a filter box under every column heading, "
                      "ticker and company frozen while you scroll, sorting on "
                      "the real numbers. **Classic**: Streamlit's own table.")
    use_grid = HAS_AGGRID and st.session_state["screen_table_style"].startswith("Advanced")

    if use_grid:
        st.caption("💡 **Click a row** to open that ticker's Details page · "
                   "type in the boxes under the headings to filter each column "
                   "· drag a column edge to resize · shift-click a heading to "
                   "sort by more than one.")
        chosen_tk = screener_grid.render(
            show, f["pe_peer_basis"] if "pe_peer_basis" in f.columns else None,
            MIN_PEERS)
    else:
        st.caption("💡 **Click on a row** to open that ticker's Details page.")
        sel = st.dataframe(styled, use_container_width=True, height=480,
                           hide_index=True,
                           on_select="rerun", selection_mode="single-row",
                           key="screen_table",
                           column_config={
                               "Δ P/E vs ind.": st.column_config.Column(
                                   help="Trailing P/E versus the MEDIAN P/E of "
                                        "the same industry in this dataset. "
                                        "Negative = cheaper than its peers. An "
                                        "asterisk means the benchmark is the "
                                        "whole SECTOR, used when the industry "
                                        f"has fewer than {MIN_PEERS} companies "
                                        "here — a wider, less demanding "
                                        "comparison. Blank when neither group "
                                        "reaches that size."),
                           })
        rows_sel = (sel.get("selection", {}) or {}).get("rows", []) if sel else []
        if rows_sel:
            idx = rows_sel[0]
            if 0 <= idx < len(f):
                chosen_tk = f.iloc[idx]["ticker"]

    # clicking a row -> go to that ticker's Details view. We leave the request
    # in "_goto_nav": it gets applied at the start of the next run, before the
    # navigation radio is created.
    #
    # SI NAVIGA SOLO QUANDO LA SELEZIONE CAMBIA, non ogni volta che c'e' una
    # selezione. AG Grid la conserva: senza questo confronto, tornando allo
    # Screener dopo aver aperto un titolo la riga risulterebbe ancora
    # selezionata e la pagina rimbalzerebbe subito di nuovo sul dettaglio,
    # rendendo impossibile tornare all'elenco.
    if chosen_tk and st.session_state.get("_last_row_click") != chosen_tk:
        st.session_state["_last_row_click"] = chosen_tk
        st.session_state["goto_ticker"] = chosen_tk
        # the Details selectbox isn't instantiated in this run (we're in
        # the Screener view), so setting its key is allowed
        st.session_state["hist_ticker"] = chosen_tk
        st.session_state["_goto_nav"] = "Details"
        st.rerun()
    if not chosen_tk:
        st.session_state.pop("_last_row_click", None)

    st.caption(
        "**EPS used × fair P/E = Fair Value** — the formula is visible in the "
        "table. **Δ P/E vs ind.** compares the stock's P/E with the median of "
        "its own industry: a P/E of 30 is expensive for a utility and cheap for "
        "software, so the absolute number alone is half the story. An "
        "**asterisk** means the industry had fewer than "
        f"{MIN_PEERS} companies here and the comparison falls back to the whole "
        "sector. **PEG** = "
        "P/E ÷ growth → *below 1 cheap*, above 2 expensive. **Lynch ratio** = "
        "(growth + dividend) ÷ P/E → the **reciprocal** of PEG, so the reading "
        "flips: *above 1.5 attractive*, below 1 expensive. **EPS data**: ✓ "
        "sources agree, ⚠ worth checking."
    )

    # Full export with readable names. NB: the download icon that appears
    # hovering over the table only exports the VISIBLE columns; this button
    # also exports every diagnostic and growth column.
    exp_cols = {
        "ticker": "Ticker", "company": "Company", "sector": "Sector",
        "industry": "Industry", "exchange": "Exchange", "sic": "SIC",
        "sic_description": "SIC description",
        "classification_source": "Sector/industry source",
        "stale_symbol": "Provider has no profile (symbol may have changed)",
        "lynch_category": "Lynch category", "current_price": "Price",
        "market_cap": "Market cap",
        "eps_ttm": "EPS used", "eps_source": "EPS source",
        "eps_ttm_yf": "EPS yfinance", "eps_ttm_edgar": "EPS EDGAR",
        "eps_divergence_pct": "EPS divergence %", "eps_flag": "EPS quality",
        "eps_basis": "EPS basis", "eps_basis_note": "EPS basis note",
        "eps_series_age_days": "SEC data age (days)",
        "missing_inputs": "Unavailable inputs",
        "eps_normalized_3y": "3y normalized EPS",
        "lynch_fair_pe": "Fair P/E", "lynch_anchor": "Valuation anchor",
        "lynch_eps_base": "Earnings base", "lynch_fair_pb": "Fair P/B",
        "lynch_confidence": "Confidence",
        "lynch_confidence_note": "Confidence reason",
        "lynch_pe_basis": "Multiple basis",
        "growth_basis": "Growth measure used",
        "growth_5y_trend": "EPS growth 5y trend %",
        "growth_5y_cagr_raw": "EPS growth 5y CAGR %",
        "loss_periods_5y": "Loss periods 5y",
        "loss_episodes_5y": "Loss episodes 5y",
        "years_since_last_loss": "Years since last loss",
        "total_debt": "Total debt", "cash": "Cash", "net_debt": "Net debt",
        "enterprise_value": "Enterprise value", "ev_to_ebit": "EV/EBIT",
        "net_debt_to_ebit": "Net debt/EBIT",
        "fair_value_peg": "Category fair value",
        "discount_vs_peg_pct": "Category discount %",
        "fair_value_pe15": "Fair value P/E15",
        "discount_vs_fv15_pct": "P/E15 discount %",
        "fair_value_norm_pe15": "Normalized fair value",
        "pe_ratio": "Current P/E", "pe_ratio_provider": "Current P/E (provider)",
        "pe_divergence_pct": "P/E divergence %", "forward_pe": "Forward P/E",
        "pe_vs_industry_pct": "P/E vs peer median %",
        "pe_peer_basis": "P/E peer basis",
        "ps_ratio": "P/S", "pfcf_ratio": "P/FCF", "price_to_book": "P/B",
        "roe_pct": "ROE %", "equity_ratio_pct": "Equity ratio %",
        "earning_power_pct": "Earning power %", "net_margin_pct": "Net margin %",
        "operating_margin_pct": "Operating margin %",
        "debt_to_equity": "Debt/equity", "long_term_debt": "Long term debt",
        "revenue_ttm": "Revenue TTM", "revenue_latest_fy": "Revenue latest FY",
        "net_income_ttm": "Net income TTM", "ebit_ttm": "EBIT TTM",
        "ocf_ttm": "Operating cash flow TTM", "fcf_ttm": "FCF TTM",
        "fcf_latest_fy": "FCF latest FY", "fcf_yield_pct": "FCF yield %",
        "fcf_growth_yoy_pct": "FCF growth YoY %",
        "revenue_growth_yoy_pct": "Revenue growth YoY %",
        "equity": "Shareholders equity", "assets": "Total assets",
        "book_value_per_share": "Book value per share",
        "revenue_per_share": "Revenue per share", "fcf_per_share": "FCF per share",
        "growth_5y_cagr": "EPS growth 5y CAGR %",
        "eps_growth_yoy": "EPS growth YoY %",
        "peg_ratio": "PEG", "lynch_ratio": "Lynch ratio",
        "earnings_volatility": "Earnings volatility",
        "dividend_yield": "Dividend %", "beta": "Beta",
        "next_earnings_date": "Next earnings date",
        "shares_outstanding": "Shares outstanding",
        "valuation_peg": "Category valuation", "valuation": "P/E15 valuation",
        "eps_method": "EPS method", "price_source": "Price source",
        "financials_basis": "Financial statements basis",
    }
    # every CAGR the build produced, whatever the horizons
    for base in CAGR_METRICS:
        for h in CAGR_HORIZONS:
            exp_cols[f"{base}_{h}y"] = f"{CAGR_LABELS[base]} CAGR {h}y %"
    avail = [c for c in exp_cols if c in f.columns]
    export_df = f[avail].rename(columns={c: exp_cols[c] for c in avail})
    st.download_button(
        "📥 Export full screener (CSV)", export_df.to_csv(index=False),
        f"lynch_screener_{datetime.now():%Y%m%d}.csv", "text/csv",
        help="Every column in the dataset: valuation, financial statement "
             "figures, all CAGRs (3/5/10 years for EPS, revenue, free cash "
             "flow, net income and operating cash flow) and the diagnostics.")

st.divider()
st.caption("⚠️ Educational tool, not financial advice. Fair value at a fixed "
           "P/E is a simplification: also weigh growth, debt, sector, and cycle.")
