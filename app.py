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
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

DATA_DIR = Path("data")
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
METRICS: dict[str, dict] = {
    "market_cap": dict(
        label="Market Cap", kind="money", better=None, peer=False,
        help="Share price × shares outstanding, from the market data provider. "
             "Not compared against the industry: size is not a quality."),
    "pe_ratio": dict(
        label="P/E (trailing)", kind="ratio", better="low",
        help="Price ÷ trailing-twelve-month EPS. The EPS is the one selected by "
             "the source arbitration and shown throughout this page, so the "
             "ratio is always internally consistent — it is recomputed here, "
             "not copied from the provider."),
    "forward_pe": dict(
        label="P/E (forward)", kind="ratio", better="low",
        help="Price ÷ next-twelve-month EPS **estimated by analysts** "
             "(yfinance consensus). The only forward-looking figure on this "
             "page: it is an expectation, not a filed number."),
    "ps_ratio": dict(
        label="P/S (ttm)", kind="ratio", better="low",
        help="Market cap ÷ trailing-twelve-month revenue. Revenue is rebuilt "
             "from the SEC filings by summing the last four discrete quarters."),
    "pfcf_ratio": dict(
        label="P/FCF", kind="ratio", better="low",
        help="Market cap ÷ trailing-twelve-month free cash flow."),
    "roe_pct": dict(
        label="ROE", kind="pct", better="high",
        help="Net income (TTM) ÷ **average** shareholders' equity over the same "
             "twelve months — average, not closing, because a flow divided by "
             "a year-end snapshot overstates the return of companies that just "
             "bought back stock. Both figures come from SEC filings."),
    "equity_ratio_pct": dict(
        label="Equity ratio", kind="pct", better="high",
        help="Shareholders' equity ÷ total assets. How much of the balance "
             "sheet is owned rather than borrowed. Structurally low for banks "
             "— which is why the comparison shown is against the same industry, "
             "not the whole market."),
    "earning_power_pct": dict(
        label="Earning power", kind="pct", better="high",
        help="Operating income, EBIT (TTM) ÷ total assets. What the assets earn "
             "before financing and taxes, so it is comparable across companies "
             "with different debt and tax situations. Where EBIT is not tagged "
             "(banks), pre-tax income is used."),
    "long_term_debt": dict(
        label="Long term debt", kind="money", better="low", peer=False,
        help="Non-current debt from the latest balance sheet filed with the "
             "SEC. Where a company only tags debt including current "
             "maturities, that broader figure is used."),
    "debt_to_equity": dict(
        label="Debt / equity", kind="ratio", better="low",
        help="Long-term debt ÷ shareholders' equity."),
    "fcf_yield_pct": dict(
        label="FCF yield", kind="pct", better="high",
        help="Free cash flow (TTM) ÷ market cap. The cash return the business "
             "generates for its current price — the cash-based counterpart of "
             "the earnings yield."),
    "fcf_growth_yoy_pct": dict(
        label="FCF growth YoY", kind="pct", better="high",
        help="Change in free cash flow over the latest full fiscal year versus "
             "the one before. Falls back to the trailing-twelve-month series "
             "when only one fiscal year is on file."),
    "fcf_ttm": dict(
        label="FCF (TTM)", kind="money", better="high", peer=False,
        help="Operating cash flow − capital expenditure, over the last four "
             "quarters. EDGAR reports cash flow year-to-date, not per quarter, "
             "so quarters are recovered by differencing consecutive cumulative "
             "figures before summing."),
    "fcf_latest_fy": dict(
        label="FCF (latest FY)", kind="money", better="high", peer=False,
        help="Free cash flow of the most recent complete fiscal year, straight "
             "from the annual cash flow statement."),
    "revenue_ttm": dict(
        label="Revenue (TTM)", kind="money", better="high", peer=False,
        help="Trailing-twelve-month revenue, summed from the last four "
             "discrete quarters filed with the SEC."),
    "net_income_ttm": dict(
        label="Net income (TTM)", kind="money", better="high", peer=False,
        help="Trailing-twelve-month net income from SEC filings."),
    "net_margin_pct": dict(
        label="Net margin", kind="pct", better="high",
        help="Net income ÷ revenue, both trailing twelve months."),
    "operating_margin_pct": dict(
        label="Operating margin", kind="pct", better="high",
        help="EBIT ÷ revenue, both trailing twelve months."),
    "dividend_yield": dict(
        label="Dividend yield", kind="pct", better="high",
        help="Annual dividend per share ÷ price. Computed from the dividend "
             "rate in dollars rather than taken from the provider's yield "
             "field, which is inconsistent about percent vs fraction."),
    "price_to_book": dict(
        label="P/B", kind="ratio", better="low",
        help="Price ÷ book value per share. Below 1 the market values the "
             "company under its accounting net worth — a Lynch 'asset play'."),
    "peg_ratio": dict(
        label="PEG", kind="ratio", better="low",
        help="P/E ÷ 5-year earnings growth. Below 1 cheap for the growth, "
             "above 2 expensive."),
    "beta": dict(
        label="Beta", kind="ratio", better=None,
        help="Volatility of the stock relative to the market (provider data)."),
    "cagr_eps_5y": dict(
        label="EPS CAGR 5y", kind="pct", better="high",
        help="Compound annual growth rate of trailing-twelve-month EPS over "
             "five years. Undefined — and left blank — when the starting value "
             "is a loss: a compound rate from a negative base means nothing."),
    "cagr_revenue_5y": dict(
        label="Revenue CAGR 5y", kind="pct", better="high",
        help="Compound annual growth rate of trailing-twelve-month revenue "
             "over five years."),
    "cagr_fcf_5y": dict(
        label="FCF CAGR 5y", kind="pct", better="high",
        help="Compound annual growth rate of trailing-twelve-month free cash "
             "flow over five years."),
    "cagr_net_income_5y": dict(
        label="Net income CAGR 5y", kind="pct", better="high",
        help="Compound annual growth rate of trailing-twelve-month net income "
             "over five years."),
    "cagr_ocf_5y": dict(
        label="Op. cash flow CAGR 5y", kind="pct", better="high",
        help="Compound annual growth rate of trailing-twelve-month operating "
             "cash flow over five years."),
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


def fmt_metric(key: str, v) -> str:
    kind = METRICS.get(key, {}).get("kind", "ratio")
    return {"money": fmt_money, "pct": fmt_pct, "ratio": fmt_ratio}[kind](v)


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
        cA, cB, cC = st.columns(3)
        cA.metric("TTM EPS (raw)", f"${eps_raw:,.2f}" if eps_raw else "—",
                  help="Trailing-twelve-month EPS as filed, with no smoothing.")
        cB.metric("3-year normalized EPS", f"${norm:,.2f}",
                  help="MEDIAN of the trailing-twelve-month EPS over the last 3 "
                       "years. Median, not mean: a single corrupted data point "
                       "would drag a mean and produce a fair value in the "
                       "millions.")
        cC.metric("Normalized fair value P/E15", f"${fvn:,.2f}",
                  help="3-year normalized EPS × 15.")
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


def render_valuation_tab(ticker: str, row: pd.DataFrame) -> None:
    """Price vs fair-value chart, headline metrics, Lynch category panel."""
    _init("hist_pe", PE_OPTIONS[0])
    _init("hist_years", "All")
    _init("hist_scale", "Log")
    c1, c2, c3 = st.columns(3)
    with c1:
        pe = st.selectbox("Fair value at P/E", PE_OPTIONS, key="hist_pe",
                          format_func=lambda x: f"P/E {x}")
    with c2:
        years = st.selectbox("History length", ["All", "15", "10", "5", "3", "1"],
                             key="hist_years",
                             format_func=lambda x: x if x == "All" else f"{x} years")
    with c3:
        scale = st.selectbox("Y-axis scale", ["Log", "Linear"], key="hist_scale")

    eps_basis_choice = st.radio(
        "EPS basis for fair value",
        ["Current (TTM)", "Normalized (3-year median)"],
        horizontal=True, key="hist_eps_basis",
        help="**Normalized** smooths out earnings inflated or depressed by "
             "one-off items (e.g. gains on equity stakes): it uses the median "
             "TTM EPS over the 3 years preceding each point instead of the "
             "current value.",
    )
    use_norm_eps = eps_basis_choice.startswith("Normalized")

    # FULL series for the ticker: the 3-year rolling median must be computed
    # here, BEFORE truncating by "History length" — otherwise the earliest
    # visible points would use an incomplete window.
    d = hist[hist["ticker"] == ticker].copy().sort_values("date")
    d["eps_norm3y"] = (d.set_index("date")["eps"]
                       .rolling("1095D", min_periods=1).median().to_numpy())
    if years != "All":
        cutoff = d["date"].max() - pd.DateOffset(years=int(years))
        d = d[d["date"] >= cutoff]

    fv_col = f"fair_value_pe{pe}"
    # We do NOT drop rows without a fair value: we keep them as "gaps" (NaN)
    # so the line visibly breaks during periods of negative earnings.
    d = d.copy()
    if use_norm_eps:
        d["eps_used"] = d["eps_norm3y"]
        d["fv"] = np.where(d["eps_used"] > 0, d["eps_used"] * pe, np.nan)
    else:
        d["eps_used"] = d["eps"]
        d["fv"] = pd.to_numeric(d[fv_col], errors="coerce")
    d = d[d["price"].notna()]
    if d.empty:
        st.info("No price history on file for this ticker.")
        return

    d["premium_pct"] = np.where(d["fv"] > 0, (d["price"] / d["fv"] - 1) * 100, np.nan)

    fig = go.Figure()
    eps_label = "Normalized EPS (3y median)" if use_norm_eps else "TTM EPS"
    # tooltip customdata, rounded HERE: with unified hovermode Plotly doesn't
    # always apply the template's formatting to customdata fields.
    custom = np.column_stack((
        d["fv"].round(2).to_numpy(dtype=float),
        (d["price"] - d["fv"]).round(2).to_numpy(dtype=float),
        d["premium_pct"].round(1).to_numpy(dtype=float),
        d["eps_used"].round(2).to_numpy(dtype=float),
        np.where(d["price"] < d["fv"], "BELOW fair value", "ABOVE fair value"),
    ))

    fv_name = f"Fair value (P/E {pe})" + (" · normalized EPS" if use_norm_eps else "")
    fig.add_trace(go.Scatter(
        x=d["date"], y=d["fv"], name=fv_name,
        line=dict(color="#2563eb", width=2), connectgaps=False,
        hoverinfo="skip",          # the full tooltip lives on the price trace
    ))
    fig.add_trace(go.Scatter(
        x=d["date"], y=d["price"], name="Price",
        line=dict(color="#16a34a", width=2), customdata=custom,
        hovertemplate=(
            "<b>%{x|%d %b %Y}</b><br><br>"
            "Price:       <b>$%{y:,.2f}</b><br>"
            "Fair value:  $%{customdata[0]:,.2f}<br>"
            "Difference:  $%{customdata[1]:+,.2f}  (%{customdata[2]:+.1f}%)<br><br>"
            "Price is %{customdata[4]}<br>"
            f"{eps_label} for this period: " + "$%{customdata[3]:,.2f}"
            "<extra></extra>"
        ),
    ))

    # shade periods with negative earnings (fair value not calculable)
    neg = d[d["eps"] <= 0]
    if not neg.empty:
        gaps = neg["date"].diff().dt.days.fillna(0) > 40
        for _, g in neg.groupby(gaps.cumsum()):
            fig.add_vrect(x0=g["date"].min(), x1=g["date"].max(),
                          fillcolor="rgba(239,68,68,0.10)", line_width=0,
                          annotation_text="negative earnings",
                          annotation_position="top left", annotation_font_size=10)

    # corporate event markers (splits, dilutions, buybacks)
    if events is not None:
        ev = events[(events["ticker"] == ticker)
                    & (events["date"] >= d["date"].min())
                    & (events["date"] <= d["date"].max())]
        style = {"split": ("#0891b2", "Split"), "dilution": ("#ea580c", "Dilution"),
                 "buyback": ("#059669", "Buyback")}
        for _, e in ev.iterrows():
            color, label = style.get(e["type"], ("#6b7280", e["type"]))
            fig.add_vline(x=e["date"], line=dict(color=color, width=1, dash="dot"))
            fig.add_annotation(x=e["date"], yref="paper", y=1.0,
                               text=f"{label} {e['detail']}", showarrow=False,
                               font=dict(size=9, color=color), textangle=-90,
                               xanchor="left", yanchor="top")

    company = row["company"].iloc[0] if not row.empty else ticker
    fig.update_layout(
        title=f"{ticker} — {company}", template="plotly_white", height=520,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.03, x=0),
        margin=dict(t=90, r=20, b=40, l=60),
    )
    fig.update_yaxes(title="$", type="log" if scale == "Log" else "linear")
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "**Green** = market price · **Blue** = fair value (EPS × chosen P/E). "
        "Dotted vertical lines = corporate events (splits, dilutions, "
        "buybacks). Red band = negative earnings: fair value can't be "
        "calculated and the line breaks."
    )

    if row.empty:
        return
    r = row.iloc[0]
    price_now = _num(r.get("current_price"))
    eps_norm_today = _num(r.get("eps_normalized_3y"))
    fair_pe_cat = _num(r.get("lynch_fair_pe"))

    # Shared fair-value figures for BOTH the metric row below and the category
    # panel, so the EPS-basis toggle updates the whole page consistently.
    if use_norm_eps:
        fv_fix = _num(r.get("fair_value_norm_pe15"))
        fv_fix_label = "Fair value P/E15 (norm.)"
        fv_fix_help = ("Normalized EPS (3-year median) × P/E 15 — independent "
                       "of the P/E chosen above for the chart.")
        disc_fix = ((price_now / fv_fix - 1) * 100) if (fv_fix and price_now) else None
        fv_cat = (eps_norm_today * fair_pe_cat) if (eps_norm_today and fair_pe_cat) else None
    else:
        fv_fix = _num(r.get("fair_value_pe15"))
        fv_fix_label = "Fair value P/E15"
        fv_fix_help = "TTM EPS × 15 — the classic Lynch earnings line."
        disc_fix = _num(r.get("discount_vs_fv15_pct"))
        fv_cat = _num(r.get("fair_value_peg"))
    disc_cat = (((price_now / fv_cat - 1) * 100) if (fv_cat and price_now)
                else (_num(r.get("discount_vs_peg_pct")) if not use_norm_eps else None))

    # L'EPS e' il numeratore di tutto quello che c'e' in questa pagina — il fair
    # value E il P/E — e mancava fra i KPI: si leggeva "fair value 195$" senza
    # vedere il numero da cui deriva. Va accanto al prezzo, non sepolto nel tab
    # di audit.
    eps_shown = _num(r.get("eps_normalized_3y")) if use_norm_eps else _num(r.get("eps_ttm"))
    eps_label = "Normalized EPS (3y)" if use_norm_eps else "EPS (TTM)"
    eps_help = (
        "Median of the trailing-twelve-month EPS over the last 3 years — the "
        "basis selected by the toggle above, and the one multiplied by the "
        "target P/E to get the fair value."
        if use_norm_eps else
        "Trailing-twelve-month diluted earnings per share, the figure chosen by "
        "the arbitration between SEC EDGAR and the market data provider. It is "
        "the number multiplied by the target P/E to get the fair value, and the "
        "denominator of the P/E shown here — the whole page is built on it.")
    if not use_norm_eps and r.get("eps_source"):
        eps_help += f" Source used: {r.get('eps_source')}."

    m = st.columns(6)
    m[0].metric("Price", f"${price_now:,.2f}" if price_now else "—")
    m[1].metric(eps_label, f"${eps_shown:,.2f}" if eps_shown else "—", help=eps_help)
    m[2].metric(fv_fix_label, f"${fv_fix:,.2f}" if fv_fix else "—", help=fv_fix_help)
    m[3].metric("Discount/Premium",
                f"{disc_fix:+.0f}%" if disc_fix is not None else "—",
                delta=("below fair value" if disc_fix is not None and disc_fix < 0
                       else "above fair value" if disc_fix is not None else None),
                delta_color="normal" if (disc_fix is not None and disc_fix < 0) else "inverse")
    _pe_prov, _pe_div = _num(r.get("pe_ratio_provider")), _num(r.get("pe_divergence_pct"))
    _pe_help = "Price ÷ the EPS shown on this page, both from the same row."
    if _pe_prov is not None:
        _pe_help += f" Provider (yfinance) reports {_pe_prov:.1f}"
        _pe_help += (f", a {_pe_div:.0f}% difference — it is computed on the "
                     "provider's own EPS, which the arbitration did not select."
                     if _pe_div is not None and _pe_div >= 1 else "; consistent.")
    m[4].metric("Current P/E", fmt_ratio(_num(r.get("pe_ratio"))), help=_pe_help)
    m[5].metric("EPS growth YoY", fmt_pct(_num(r.get("eps_growth_yoy"))),
                help="Change in trailing-twelve-month EPS versus the point "
                     "closest to one year earlier — compared by date, not by "
                     "position in the series.")

    render_normalized_earnings(r, eps_shown if not use_norm_eps else _num(r.get("eps_ttm")))

    # ---------------- LYNCH CATEGORY + MODEL COMPARISON ----------------
    cat = r.get("lynch_category")
    if not cat or pd.isna(cat):
        return
    st.markdown(f"### {lynch_icon(cat)} {cat}")
    if pd.notna(r.get("lynch_note")):
        st.caption(r.get("lynch_note"))

    k = st.columns(6)
    fv_cat_label = "Fair value (category, norm.)" if use_norm_eps else "Fair value (category)"
    fv_cat_help = "EPS × the fair P/E assigned to the category"
    if use_norm_eps:
        fv_cat_help += " (normalized EPS, matches the toggle above)"
    k[0].metric(fv_cat_label, f"${fv_cat:,.2f}" if fv_cat else "n/a", help=fv_cat_help)
    k[1].metric("Discount / Premium",
                f"{disc_cat:+.0f}%" if disc_cat is not None else "—",
                delta=("below fair value" if disc_cat is not None and disc_cat < 0
                       else "above fair value" if disc_cat is not None else None),
                delta_color="normal" if (disc_cat is not None and disc_cat < 0) else "inverse")
    k[2].metric("5-year growth (CAGR)", fmt_pct(_num(r.get("growth_5y_cagr"))))
    k[3].metric("Fair P/E for category", fmt_ratio(fair_pe_cat, 0) if fair_pe_cat else "n/a",
                help=str(r.get("lynch_pe_basis") or ""))
    lr_v = _num(r.get("lynch_ratio"))
    k[4].metric("Lynch ratio", fmt_ratio(lr_v, 2),
                delta=("attractive" if lr_v and lr_v > 1.5 else
                       "fair" if lr_v and lr_v >= 1 else "expensive" if lr_v else None),
                delta_color="normal" if (lr_v and lr_v >= 1) else "inverse",
                help="(growth % + dividend %) ÷ P/E · >1.5 attractive, ~1 fair, <1 expensive")
    k[5].metric("Earnings volatility", fmt_ratio(_num(r.get("earnings_volatility")), 0),
                help="Standard deviation of year-over-year earnings changes. >40 = erratic")

    comp = pd.DataFrame({
        "Model": [
            "Lynch Chart (fixed P/E 15)" + (" · normalized EPS" if use_norm_eps else ""),
            (f"Lynch Fair Value (P/E {fair_pe_cat:.0f} = category)"
             if fair_pe_cat else "Lynch Fair Value (not applicable)"),
            "Current price",
        ],
        "Value": [f"${fv_fix:,.2f}" if fv_fix else "—",
                  f"${fv_cat:,.2f}" if fv_cat else "n/a",
                  f"${price_now:,.2f}" if price_now else "—"],
        "Price deviation": [f"{disc_fix:+.0f}%" if disc_fix is not None else "—",
                            f"{disc_cat:+.0f}%" if disc_cat is not None else "—", "—"],
    })
    st.dataframe(comp, use_container_width=True, hide_index=True)

    if fv_fix and fv_cat:
        spread = abs(fv_cat / fv_fix - 1) * 100
        if spread > 35:
            st.info(
                f"The two models diverge by **{spread:.0f}%**. The fixed P/E 15 "
                "is a quick filter, the same for everyone; the per-category "
                "model adapts the multiple to the type of company. When they "
                "diverge this much, trust the second one and check the "
                "Data quality tab."
            )
        else:
            st.caption("The two models agree: a robust valuation signal.")

    st.download_button("📥 Download price history (CSV)", d.to_csv(index=False),
                       f"{ticker}_pe{pe}.csv", "text/csv")


def render_financials_tab(ticker: str, r: pd.Series) -> None:
    """
    Financial data as filed with the SEC, each figure compared with the median
    of the same industry.
    """
    peers, peer_label, n_peers = peer_reference(r, ind_med, sec_med)

    st.markdown(f"#### {r.get('company', ticker)} — key figures")
    if peers is not None:
        st.caption(
            f"Deltas are measured against the **median of the {peer_label}** "
            f"({n_peers} companies in this dataset). Median rather than mean "
            "because valuation multiples have a long tail and one extreme "
            "company would define the average. Percentages are compared in "
            "percentage points (pp), ratios and amounts in relative terms. "
            "Market cap has no delta: size is not a quality."
        )
    else:
        st.caption(
            f"No peer group with at least {MIN_PEERS} companies for this "
            "ticker in the current dataset, so no industry comparison is "
            "shown. Build a wider universe (scope = us-all) to get one."
        )

    # --- row 1: the valuation block, mirroring the classic data sheet ---
    metric_grid([metric_item(k, r, peers) for k in
                 ("market_cap", "pe_ratio", "forward_pe", "ps_ratio")])
    metric_grid([metric_item(k, r, peers) for k in
                 ("roe_pct", "equity_ratio_pct", "earning_power_pct", "long_term_debt")])

    fcf_items = [metric_item(k, r, peers) for k in
                 ("fcf_yield_pct", "fcf_growth_yoy_pct", "fcf_latest_fy")]
    nxt = r.get("next_earnings_date")
    fcf_items.append({
        "label": "Next earnings",
        "value": str(nxt)[:10] if nxt and pd.notna(nxt) else "—",
        "help": "Next expected earnings date from the market data provider. "
                "An expectation, not a filed date — companies confirm it a few "
                "weeks ahead.",
    })
    metric_grid(fcf_items)

    st.divider()
    st.markdown("##### Profitability and balance sheet")
    metric_grid([metric_item(k, r, peers) for k in
                 ("revenue_ttm", "net_income_ttm", "operating_margin_pct", "net_margin_pct")])
    metric_grid([metric_item(k, r, peers) for k in
                 ("fcf_ttm", "debt_to_equity", "price_to_book", "dividend_yield")])

    if _num(r.get("fcf_ttm")) is None and _num(r.get("revenue_ttm")) is not None:
        st.caption(
            "ℹ️ No free cash flow for this company: it doesn't tag capital "
            "expenditure in a comparable way — normal for banks and insurers, "
            "whose cash flow statement has a different structure."
        )
    basis = r.get("financials_basis")
    if isinstance(basis, str) and basis.startswith("annual:"):
        st.caption(
            "ℹ️ For **" + basis.split(":", 1)[1].replace(",", ", ") + "** this "
            "company files only annual figures, so the values above are the "
            "latest full fiscal year rather than the last twelve months. Stated "
            "here rather than silently mixed with the trailing figures of "
            "companies that report quarterly."
        )

    # --- peer comparison table ---
    if peers is not None:
        st.divider()
        st.markdown("##### Side by side with the peer group")
        rows = []
        for key in ("pe_ratio", "forward_pe", "ps_ratio", "pfcf_ratio", "roe_pct",
                    "equity_ratio_pct", "earning_power_pct", "net_margin_pct",
                    "fcf_yield_pct", "debt_to_equity", "cagr_eps_5y", "cagr_revenue_5y"):
            if key not in r.index or key not in peers.index:
                continue
            gap, text = peer_gap(key, r.get(key), peers.get(key))
            rows.append({
                "Metric": METRICS[key]["label"],
                ticker: fmt_metric(key, _num(r.get(key))),
                "Peer median": fmt_metric(key, _num(peers.get(key))),
                "Gap": text or "—",
                "Better when": {"high": "higher", "low": "lower"}.get(
                    METRICS[key].get("better"), "—"),
            })
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption(
            "The peer group is built from the tickers in **this dataset**, not "
            "from the whole market: with a partial universe the median "
            "describes the companies actually loaded. The number of peers is "
            "stated above so you can judge how much weight it deserves."
        )

    st.caption(
        "Source: figures are computed from the companies' own SEC filings "
        "(XBRL company facts) — revenue, earnings, cash flow and balance sheet "
        "are summed from the filed quarters. Price, market cap, forward P/E and "
        "the earnings date come from the market data provider, since no filing "
        "contains them. Hover any label for its exact formula."
    )


def render_growth_tab(ticker: str, r: pd.Series) -> None:
    """Five-year track record: annual figures, growth rates, margins."""
    st.markdown("#### Compound growth")
    st.caption(
        "Each rate is computed on the trailing-twelve-month series, so it "
        "compares twelve full months with twelve full months — never a quarter "
        "against a quarter, which would measure seasonality. A rate is left "
        "blank when the starting value is zero or negative: a compound rate "
        "from a loss has no meaning."
    )

    cagr_rows = []
    for base in CAGR_METRICS:
        row = {"Metric": CAGR_LABELS[base]}
        any_val = False
        for h in CAGR_HORIZONS:
            v = _num(r.get(f"{base}_{h}y"))
            row[f"{h}y CAGR"] = fmt_pct(v) if v is not None else "—"
            any_val = any_val or v is not None
        if any_val:
            cagr_rows.append(row)
    if cagr_rows:
        st.dataframe(pd.DataFrame(cagr_rows), use_container_width=True, hide_index=True)
    else:
        st.info("Not enough filed history to compute compound growth rates.")

    peers, peer_label, n_peers = peer_reference(r, ind_med, sec_med)
    grid = [metric_item(k, r, peers) for k in
            ("cagr_eps_5y", "cagr_revenue_5y", "cagr_fcf_5y", "cagr_net_income_5y")]
    metric_grid(grid)
    if peers is not None:
        st.caption(f"Deltas vs the median of the {peer_label}.")

    # --- annual table + charts ---
    if annual is None:
        st.info("Annual financials not available. Regenerate the dataset to "
                "produce `data/financials_annual.csv`.")
        return
    a = annual[annual["ticker"] == ticker].copy()
    if a.empty:
        st.info("No annual financial data on file for this ticker.")
        return
    a = a.sort_values("fy_end")

    st.divider()
    st.markdown("#### Last fiscal years, as filed")

    show = pd.DataFrame({
        "Fiscal year": a["fy_end"].astype(str),
        "Revenue": a.get("revenue").map(fmt_money) if "revenue" in a else "—",
        "Net income": a.get("net_income").map(fmt_money) if "net_income" in a else "—",
        "EPS": a.get("eps").map(lambda v: f"${v:,.2f}" if pd.notna(v) else "—")
        if "eps" in a else "—",
        "Op. cash flow": a.get("ocf").map(fmt_money) if "ocf" in a else "—",
        "Capex": a.get("capex").map(fmt_money) if "capex" in a else "—",
        "Free cash flow": a.get("fcf").map(fmt_money) if "fcf" in a else "—",
    })
    if "revenue" in a and "net_income" in a:
        show["Net margin"] = [
            fmt_pct(ni / rev * 100) if (pd.notna(ni) and pd.notna(rev) and rev) else "—"
            for ni, rev in zip(a["net_income"], a["revenue"])]
    st.dataframe(show, use_container_width=True, hide_index=True)
    st.caption(
        "Each row is one complete fiscal year from the annual report (10-K). "
        "Fiscal years do not always end in December: the closing date is the "
        "company's own. The EPS column is the **filed** figure, restated for "
        "stock splits only — it does not carry the anchoring factor that aligns "
        "the chart's level to today's EPS, so it can differ slightly from the "
        "TTM figures on the other tabs."
    )

    series = [("revenue", "Revenue", "#2563eb"), ("net_income", "Net income", "#16a34a"),
              ("fcf", "Free cash flow", "#a855f7")]
    available = [(c, lbl, col) for c, lbl, col in series
                 if c in a.columns and a[c].notna().any()]
    if available:
        fig = go.Figure()
        for c, lbl, color in available:
            fig.add_trace(go.Bar(x=a["fy_end"].astype(str), y=a[c], name=lbl,
                                 marker_color=color,
                                 hovertemplate="%{x}<br>" + lbl + ": %{y:$,.0f}<extra></extra>"))
        fig.update_layout(template="plotly_white", height=330, barmode="group",
                          legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                          margin=dict(t=40, r=20, b=40, l=60), yaxis_title="$")
        st.plotly_chart(fig, use_container_width=True)

    if "eps" in a.columns and a["eps"].notna().any():
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(x=a["fy_end"].astype(str), y=a["eps"], name="EPS",
                                  mode="lines+markers", line=dict(color="#ea580c", width=2),
                                  hovertemplate="%{x}<br>EPS: $%{y:,.2f}<extra></extra>"))
        fig2.update_layout(template="plotly_white", height=280, yaxis_title="$ per share",
                           margin=dict(t=40, r=20, b=40, l=60),
                           legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
        st.plotly_chart(fig2, use_container_width=True)

    st.download_button("📥 Download annual financials (CSV)", a.to_csv(index=False),
                       f"{ticker}_financials.csv", "text/csv")


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
    ("Equity ratio", "equity ÷ total assets", "derived", ""),
    ("Earning power", "EBIT TTM ÷ total assets", "derived", ""),
    ("Net / operating margin", "net income or EBIT ÷ revenue, both TTM",
     "derived", ""),
    ("P/E (trailing)", "price ÷ the EPS above (recomputed, never copied)",
     "derived", ""),
    ("P/S · P/FCF · FCF yield", "market cap ÷ revenue, ÷ FCF; FCF ÷ market cap",
     "derived", ""),
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
    st.markdown("**4. How every financial figure is computed, and from what**")
    st.caption(
        "Everything on the Financials tab is derived from the company's own SEC "
        "filings, except the four rows marked as provider data — no filing "
        "contains a share price. The **XBRL concept** column names the exact tag "
        "read for this company: more than one means the company changed tag over "
        "time (ASC 606 moved all revenue to a new tag in 2018) and the series "
        "spans that boundary."
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
    st.markdown("**5. Every growth rate, with the two numbers it comes from**")
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


def render_quality_tab(ticker: str, r: pd.Series) -> None:
    """Where every number comes from, and what to distrust."""
    g = lambda k: r[k] if k in r.index and pd.notna(r[k]) else None  # noqa: E731

    st.markdown("**1. Current fair value derivation**")
    eps_used = _num(g("eps_ttm"))
    price_x = _num(g("current_price"))
    if eps_used:
        calc = pd.DataFrame({
            "Target P/E": PE_OPTIONS,
            "Fair value": [round(eps_used * p, 2) for p in PE_OPTIONS],
            "vs price": [f"{(price_x / (eps_used * p) - 1) * 100:+.0f}%"
                         if price_x else "—" for p in PE_OPTIONS],
        })
        st.dataframe(calc, use_container_width=True, hide_index=True)
        st.caption(f"Formula: TTM EPS ${eps_used:.2f} × target P/E."
                   + (f" Current price ${price_x:,.2f}." if price_x else ""))

    st.markdown("**2. Where the EPS comes from (the two sources compared)**")
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
        # Serie completa ma in ritardo di un trimestre nelle API XBRL della SEC.
        # Il ticker resta nel dataset — buttarlo via nasconderebbe una societa'
        # con dati validi — a patto di dirlo qui.
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

    div = _num(g("eps_divergence_pct"))
    flag = g("eps_flag")
    if div is not None:
        msg = f"Divergence between sources: **{div:.1f}%** · status: `{flag}`"
        if flag == "check":
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
                "**1. Look at the implied P/E.** A P/E over 150 or under 2 "
                "almost always signals a wrong EPS, not an extreme stock.",
                f"**2. Compare with history.** The 3-year normalized EPS is "
                f"{('$%.2f' % norm_x) if norm_x else 'unavailable'}: the correct "
                "source usually resembles it.",
                "**3. Typical causes.** yfinance sometimes reports a single "
                "quarter's EPS instead of TTM, or doesn't update after a split. "
                "EDGAR can be wrong when the Q4 reconstruction runs into "
                "restated data.",
                "**4. Verify at the source.** The official figure is in the "
                "latest 10-Q/10-K on SEC EDGAR (link below): look for *Diluted "
                "earnings per share* in the income statement.",
            ]))
            st.markdown(
                f"🔗 [SEC filings for {ticker}]"
                f"(https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                f"&ticker={ticker}&type=10-Q&dateb=&owner=include&count=10) · "
                f"[Yahoo Finance page](https://finance.yahoo.com/quote/{ticker}/"
                f"key-statistics)")
        else:
            st.success(msg + " — the sources agree.")

    st.markdown("**3. Normalized earnings**")
    st.caption("Moved to the **Valuation** tab, next to the fair value it "
               "explains — it answers \"why does another site give a different "
               "number?\", which is a question about the valuation, not about "
               "data quality.")

    render_metric_provenance(ticker, r)
    render_cagr_audit(ticker, r)

    st.markdown("**6. Data quality and provenance**")
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
        st.markdown("**7. Corporate events detected**")
        show_ev = ev_t.copy()
        show_ev["date"] = show_ev["date"].dt.strftime("%b %d, %Y")
        show_ev["type"] = show_ev["type"].str.capitalize()
        st.dataframe(show_ev[["date", "type", "detail"]]
                     .rename(columns={"date": "Date", "type": "Type", "detail": "Detail"}),
                     use_container_width=True, hide_index=True)

    st.markdown("**8. Recent SEC filings**")
    fil_t = filings[filings["ticker"] == ticker] if filings is not None else None
    if fil_t is None or fil_t.empty:
        st.caption("No recent 10-K/10-Q filings on file for this ticker yet.")
    else:
        show_fil = fil_t.rename(columns={"form": "Form", "filing_date": "Filed",
                                         "period_date": "Period"})
        cols = [c for c in ("Form", "Filed", "Period", "url") if c in show_fil.columns]
        st.dataframe(
            show_fil[cols], use_container_width=True, hide_index=True,
            column_config={"url": st.column_config.LinkColumn("Filing",
                                                              display_text="Open ↗")})
        st.caption("Filing date = when it was submitted to the SEC · "
                   "Period = the fiscal period the filing covers.")
    st.markdown(
        f"🔗 [Browse all filings for {ticker} on SEC EDGAR]"
        f"(https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
        f"&ticker={ticker}&type=10-K&dateb=&owner=include&count=40)")


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

    tab_val, tab_fin, tab_growth, tab_qual = st.tabs(
        ["📉 Valuation", "📊 Financials", "📈 Growth (5y)", "🔍 Data quality"])
    with tab_val:
        render_valuation_tab(ticker, row)
    if row.empty:
        for tab in (tab_fin, tab_growth, tab_qual):
            with tab:
                st.info("No summary row for this ticker in fundamentals.csv.")
    else:
        r_series = row.iloc[0]
        with tab_fin:
            render_financials_tab(ticker, r_series)
        with tab_growth:
            render_growth_tab(ticker, r_series)
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
            "Every ticker is classified into one of Peter Lynch's six company "
            "types (from *One Up on Wall Street*), which determines the "
            "**fair P/E** used for the per-category model. Checks run in this "
            "order — the first match wins:"
        )
        st.markdown(
            "**1. 🔧 Turnaround** — losses in the past 5 years. Earnings aren't "
            "a reliable valuation base yet, so **no multiple** is assigned; "
            "checked first regardless of growth.\n\n"
            "**2. 🏗️ Asset Play** — price-to-book ratio below 1. The value is "
            "in the assets, not the earnings — **no multiple** (use P/B instead).\n\n"
            "**3. 🔄 Cyclical** — a structurally cyclical sector (Energy, Basic "
            "Materials, Industrials, Consumer Cyclical, Real Estate) **and** "
            "erratic earnings (year-over-year volatility above 40). Fair P/E "
            "fixed at **12**, applied to normalized (median) earnings, since "
            "raw current earnings can be misleadingly high or low at the "
            "extremes of the cycle.\n\n"
            "**4. 🚀 Fast Grower** — 5-year earnings CAGR above 20%. Fair P/E "
            "= growth rate, **capped at 25** (very few companies sustain "
            "faster growth for long).\n\n"
            "**5. 🏛️ Stalwart** — growth between 10% and 20%. Fair P/E = "
            "growth rate. Split into **Stalwart** (market cap over $50B) and "
            "**Stalwart (mid cap)** below that threshold.\n\n"
            "**6. 🐢 Slow Grower** — growth between 0% and 10%. Fair P/E = "
            "growth + dividend yield, **floored at 6 and capped at 12**. If "
            "growth is negative, the category becomes **Slow Grower "
            "(declining earnings)** with no multiple assigned — a "
            "growth-linked P/E is meaningless once earnings are shrinking.\n\n"
            "**❓ Unclassified** — not enough earnings history to estimate "
            "5-year growth."
        )
        st.caption(
            "**PEG = 1 principle:** for growth-based categories, the fair P/E "
            "tracks the company's own growth rate, so it **varies stock by "
            "stock even within the same category**. Turnaround, Asset Play and "
            "Slow Grower (declining earnings) get no multiple at all: for them "
            "a P/E-based fair value isn't a meaningful signal."
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
        only_profit = st.checkbox("Positive EPS only", value=True, key="screen_profit")
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
                ALL_CATS = ["Fast Grower", "Stalwart", "Stalwart (mid cap)",
                            "Slow Grower", "Cyclical", "Asset Play", "Turnaround",
                            "Unclassified"]
                present = set(f["lynch_category"].dropna().unique())
                opts = [c for c in ALL_CATS if c in present]
                missing = [c for c in ALL_CATS if c not in present
                           and c not in ("Stalwart (mid cap)", "Unclassified")]
                cat_sel = _multi_filter("Lynch category", opts, "screen_cat",
                                        fmt=lambda c: f"{lynch_icon(c)} {c}")
                if missing:
                    st.caption(f"No tickers in: {', '.join(missing)}")
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
                 "current_price", "eps_ttm", "lynch_fair_pe", fv_col_s, disc_col,
                 "pe_ratio", "pe_vs_industry_pct", "growth_5y_cagr",
                 "cagr_revenue_5y", "peg_ratio", "lynch_ratio", "dividend_yield",
                 "roe_pct", "fcf_yield_pct", val_col, "_rel"]
    cols_show = [c for c in cols_show if c in f.columns]
    show = f[cols_show].copy()
    names = {"ticker": "Ticker", "company": "Company", "sector": "Sector",
             "industry": "Industry", "lynch_category": "Category",
             "current_price": "Price", "eps_ttm": "EPS used",
             fv_col_s: "Fair Value", disc_col: "Discount %",
             "pe_ratio": "Cur. P/E", "pe_vs_industry_pct": "Δ P/E vs ind.",
             "lynch_fair_pe": "× Fair P/E", "growth_5y_cagr": "EPS CAGR 5y",
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
    st.caption("💡 **Click on a row** to open that ticker's Details page.")
    sel = st.dataframe(styled, use_container_width=True, height=480, hide_index=True,
                       on_select="rerun", selection_mode="single-row",
                       key="screen_table",
                       column_config={
                           "Δ P/E vs ind.": st.column_config.Column(
                               help="Trailing P/E versus the MEDIAN P/E of the "
                                    "same industry in this dataset. Negative = "
                                    "cheaper than its peers. An asterisk means "
                                    "the benchmark is the whole SECTOR, used "
                                    f"when the industry has fewer than {MIN_PEERS} "
                                    "companies here — a wider, less demanding "
                                    "comparison. Blank when neither group "
                                    "reaches that size."),
                       })

    # clicking a row -> go to that ticker's Details view. We leave the request
    # in "_goto_nav": it gets applied at the start of the next run, before the
    # navigation radio is created.
    rows_sel = (sel.get("selection", {}) or {}).get("rows", []) if sel else []
    if rows_sel:
        idx = rows_sel[0]
        if 0 <= idx < len(f):
            chosen = f.iloc[idx]["ticker"]
            if st.session_state.get("goto_ticker") != chosen or nav != "Details":
                st.session_state["goto_ticker"] = chosen
                # the Details selectbox isn't instantiated in this run (we're in
                # the Screener view), so setting its key is allowed
                st.session_state["hist_ticker"] = chosen
                st.session_state["_goto_nav"] = "Details"
                st.rerun()

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
        "lynch_category": "Lynch category", "current_price": "Price",
        "market_cap": "Market cap",
        "eps_ttm": "EPS used", "eps_source": "EPS source",
        "eps_ttm_yf": "EPS yfinance", "eps_ttm_edgar": "EPS EDGAR",
        "eps_divergence_pct": "EPS divergence %", "eps_flag": "EPS quality",
        "eps_basis": "EPS basis", "eps_basis_note": "EPS basis note",
        "eps_series_age_days": "SEC data age (days)",
        "missing_inputs": "Unavailable inputs",
        "eps_normalized_3y": "3y normalized EPS",
        "lynch_fair_pe": "Fair P/E", "fair_value_peg": "Category fair value",
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
