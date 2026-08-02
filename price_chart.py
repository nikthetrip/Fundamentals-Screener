#!/usr/bin/env python3
"""
price_chart.py — Il grafico del PREZZO: candele, volumi, orizzonti scegliibili.

DUE GRAFICI, DUE DOMANDE DIVERSE. Questo risponde a "come si e' mosso il
titolo": candele, volumi, medie mobili, la lettura che si fa su una piattaforma
di mercato. Il grafico di VALUTAZIONE — quello con la linea del fair value,
disegnato con Plotly — risponde a "quanto vale rispetto a quello che guadagna",
ed e' un'altra cosa. Prima erano lo stesso grafico con un interruttore che ne
sceglieva il motore, e l'interruttore chiedeva al lettore una decisione tecnica
("Plotly o TradingView?") al posto di una decisione di contenuto.

I DATI ARRIVANO AL MOMENTO, non dalla build. Il dataset porta un prezzo di
chiusura a settimana: con quello le candele non si disegnano, perche' una
candela ha bisogno di apertura, massimo, minimo e chiusura. Le serie
giornaliere complete di novecento societa' sarebbero decine di milioni di righe
in un repository, quindi si scaricano su richiesta e si tengono in cache per
un'ora. E' l'unica parte dell'applicazione che parla con la rete mentre e'
in uso: quando la rete non risponde, il grafico lo dice e ripiega sulla
chiusura settimanale che il dataset ha gia'.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from streamlit_lightweight_charts import renderLightweightCharts

import charts

PRICE_SCALE_NORMAL, PRICE_SCALE_LOG = 0, 1

# Ogni orizzonte con quanti giorni di storia mostra. "Max" prende tutto.
RANGES: dict[str, int | None] = {
    "6M": 182, "1Y": 365, "2Y": 730, "5Y": 1825, "10Y": 3652, "Max": None,
}

# Cadenza della candela. Il valore e' la regola di ricampionamento di pandas.
TIMEFRAMES: dict[str, str] = {"Daily": "D", "Weekly": "W", "Monthly": "ME"}


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_ohlc(ticker: str) -> pd.DataFrame | None:
    """
    Serie giornaliera apertura/massimo/minimo/chiusura e volumi.

    In cache per un'ora: e' un dato di mercato, non cambia entro la sessione di
    chi legge, e senza cache ogni tocco a un comando della pagina rifarebbe la
    chiamata. Rettificata per gli split (`auto_adjust`), come i prezzi del
    dataset: due serie rettificate diversamente non si possono confrontare.
    """
    try:
        import yfinance as yf
        h = yf.Ticker(ticker).history(period="max", interval="1d",
                                      auto_adjust=True)
    except Exception:                                        # noqa: BLE001
        return None
    if h is None or h.empty or "Close" not in h.columns:
        return None
    h = h.reset_index()
    date_col = "Date" if "Date" in h.columns else h.columns[0]
    out = pd.DataFrame({
        "date": pd.to_datetime(h[date_col]).dt.tz_localize(None),
        "open": pd.to_numeric(h["Open"], errors="coerce"),
        "high": pd.to_numeric(h["High"], errors="coerce"),
        "low": pd.to_numeric(h["Low"], errors="coerce"),
        "close": pd.to_numeric(h["Close"], errors="coerce"),
        "volume": pd.to_numeric(h.get("Volume"), errors="coerce"),
    }).dropna(subset=["open", "high", "low", "close"])
    return out if not out.empty else None


def resample_ohlc(d: pd.DataFrame, rule: str) -> pd.DataFrame:
    """
    Da giornaliero a settimanale o mensile, con la regola giusta per ogni voce.

    Non si ricampiona una candela facendo la media: l'apertura del periodo e'
    la PRIMA apertura, il massimo e' il PIU' ALTO dei massimi, il minimo il piu'
    basso, la chiusura l'ULTIMA. Una media produrrebbe candele che non sono mai
    esistite, con corpi piu' corti del vero — cioe' un grafico che fa sembrare
    il titolo piu' tranquillo di quanto sia stato.
    """
    if rule == "D":
        return d
    g = d.set_index("date").resample(rule)
    out = pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(),
        "low": g["low"].min(), "close": g["close"].last(),
        "volume": g["volume"].sum(min_count=1),
    }).dropna(subset=["open", "high", "low", "close"])
    return out.reset_index()


def _sma(close: pd.Series, window: int) -> pd.Series:
    """Media mobile semplice. `min_periods=window`: finche' la finestra non e'
    piena il valore non esiste, e disegnarne uno costruito su meno punti
    significa mostrare una media di 200 giorni che ne ha visti trenta."""
    return close.rolling(window, min_periods=window).mean()


def _candles(d: pd.DataFrame, up: str, down: str) -> list[dict]:
    return [{
        "time": t.strftime("%Y-%m-%d"),
        "open": round(float(o), 4), "high": round(float(h), 4),
        "low": round(float(low), 4), "close": round(float(c), 4),
    } for t, o, h, low, c in zip(d["date"], d["open"], d["high"],
                                 d["low"], d["close"])]


def _volumes(d: pd.DataFrame, up: str, down: str) -> list[dict]:
    """Volumi colorati come la candela del giorno: verde se ha chiuso sopra
    l'apertura. Un volume senza direzione dice quanto si e' scambiato, non da
    che parte stava la pressione."""
    rows = []
    for t, o, c, v in zip(d["date"], d["open"], d["close"], d["volume"]):
        if pd.isna(v):
            continue
        rows.append({"time": t.strftime("%Y-%m-%d"), "value": float(v),
                     "color": up if c >= o else down})
    return rows


def _line(d: pd.DataFrame, values: pd.Series) -> list[dict]:
    return [{"time": t.strftime("%Y-%m-%d"), "value": round(float(v), 4)}
            for t, v in zip(d["date"], values) if pd.notna(v)]


def render(ticker: str, fallback: pd.DataFrame | None = None,
           height: int = 520) -> None:
    """
    Il grafico e i suoi comandi. `fallback` e' la serie settimanale del dataset,
    usata come rete di sicurezza quando il mercato non risponde.
    """
    p = charts.palette()
    up, down = p["good"], p["bad"]

    c1, c2, c3, c4 = st.columns([1.4, 1.2, 1, 1.4])
    with c1:
        rng = st.segmented_control(
            "Range", list(RANGES), key=f"pc_range_{ticker}",
            default="5Y", label_visibility="collapsed")
    with c2:
        tf = st.segmented_control(
            "Timeframe", list(TIMEFRAMES), key=f"pc_tf_{ticker}",
            default="Weekly", label_visibility="collapsed")
    with c3:
        logscale = st.toggle("Log", key=f"pc_log_{ticker}", value=True,
                             help="Scala logaritmica: su vent'anni di storia "
                                  "un raddoppio da 5 a 10 dollari e uno da 50 "
                                  "a 100 occupano la stessa altezza, che e' "
                                  "l'unico modo di confrontare due periodi.")
    with c4:
        mas = st.multiselect("Moving averages", ["SMA 50", "SMA 200"],
                             default=["SMA 200"], key=f"pc_ma_{ticker}",
                             label_visibility="collapsed",
                             placeholder="Moving averages…")

    rng = rng or "5Y"
    tf = tf or "Weekly"

    raw = fetch_ohlc(ticker)
    if raw is None:
        st.warning(
            "**Market data unavailable right now.** Candles need daily "
            "open/high/low/close, which is fetched live rather than stored in "
            "the dataset — the provider did not answer. The weekly closing "
            "line below comes from the dataset and is always available.",
            icon="📡")
        if fallback is not None and not fallback.empty:
            st.line_chart(fallback.set_index("date")["price"], height=300)
        return

    # LE MEDIE MOBILI SI CALCOLANO PRIMA DEL TAGLIO, sulla serie intera: una
    # media a 200 periodi sul solo tratto visibile non esisterebbe per i primi
    # 200 punti, e il grafico si aprirebbe con un buco lungo quanto la media.
    d = resample_ohlc(raw, TIMEFRAMES[tf])
    d["sma50"] = _sma(d["close"], 50)
    d["sma200"] = _sma(d["close"], 200)
    days = RANGES[rng]
    if days:
        d = d[d["date"] >= d["date"].max() - pd.Timedelta(days=days)]
    if d.empty:
        st.info("No price data in this range for this ticker.")
        return

    last, first = d.iloc[-1], d.iloc[0]
    change = (last["close"] / first["close"] - 1) * 100 if first["close"] else None
    hi, lo = d["high"].max(), d["low"].min()
    k = st.columns(4)
    k[0].metric("Last close", f"${last['close']:,.2f}")
    k[1].metric(f"Change over {rng}",
                f"{change:+.1f}%" if change is not None else "—",
                delta=f"{last['close'] - first['close']:+,.2f} $"
                if change is not None else None)
    k[2].metric(f"High / low over {rng}", f"${hi:,.2f}",
                delta=f"low ${lo:,.2f}", delta_color="off")
    k[3].metric("Candles shown", f"{len(d):,}",
                delta=f"{tf.lower()}", delta_color="off")

    chart_options = {
        "height": height,
        "layout": {"background": {"type": "solid", "color": "rgba(0,0,0,0)"},
                   "textColor": p["muted"], "attributionLogo": False},
        "grid": {"vertLines": {"color": p["grid"]},
                 "horzLines": {"color": p["grid"]}},
        "rightPriceScale": {
            "borderColor": p["grid"],
            "mode": PRICE_SCALE_LOG if logscale else PRICE_SCALE_NORMAL,
            # Spazio in basso per i volumi, che stanno sulla loro scala.
            "scaleMargins": {"top": 0.08, "bottom": 0.26},
        },
        "timeScale": {"borderColor": p["grid"], "timeVisible": False,
                      "rightOffset": 6, "barSpacing": 8},
        "crosshair": {"mode": 1},
        "handleScroll": True, "handleScale": True,
    }

    series = [{
        "type": "Candlestick",
        "data": _candles(d, up, down),
        "options": {
            "upColor": up, "downColor": down,
            "borderUpColor": up, "borderDownColor": down,
            "wickUpColor": up, "wickDownColor": down,
        },
    }]
    if d["volume"].notna().any():
        series.append({
            "type": "Histogram",
            "data": _volumes(d, _rgba(up, 0.5), _rgba(down, 0.5)),
            "options": {"priceScaleId": "vol", "priceLineVisible": False,
                        "lastValueVisible": False},
            "priceScale": {"scaleMargins": {"top": 0.8, "bottom": 0},
                           "visible": False},
        })
    for name, col, slot in (("SMA 50", "sma50", 3), ("SMA 200", "sma200", 4)):
        if name in (mas or []) and d[col].notna().any():
            series.append({
                "type": "Line",
                "data": _line(d, d[col]),
                "options": {"color": p["series"][slot], "lineWidth": 1,
                            "priceLineVisible": False,
                            "lastValueVisible": False,
                            "crosshairMarkerVisible": False},
            })

    renderLightweightCharts(
        [{"chart": chart_options, "series": series}],
        key=f"pc_{ticker}_{rng}_{tf}_{int(logscale)}_{'_'.join(sorted(mas or []))}")

    st.caption(
        "**Wheel** to zoom · **drag** to pan · **double-click** to fit. "
        "Green candle = closed above its open. Bars at the bottom are volume, "
        "coloured the same way. Prices are adjusted for splits, like the rest "
        "of the app. Daily data is fetched live and cached for an hour — it is "
        "the only figure here that does not come from the stored dataset.")


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha})"
