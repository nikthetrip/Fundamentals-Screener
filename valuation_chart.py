#!/usr/bin/env python3
"""
valuation_chart.py — Prezzo contro fair value disegnato con lightweight-charts.

E' la libreria con cui TradingView disegna i propri grafici. Rispetto a Plotly
cambiano tre cose che si sentono subito con vent'anni di storia sullo schermo:

  1. ZOOM E SCORRIMENTO VERI. Rotella per stringere l'asse dei tempi, trascina
     per scorrere, doppio click per tornare indietro — senza far ripartire lo
     script Python. Su Plotly ogni interazione utile passa da un rerun.
  2. IL MIRINO. La riga che segue il puntatore con le etichette sui due assi,
     agganciata al punto dati piu' vicino.
  3. La resa di una serie lunga: e' scritta per centinaia di migliaia di punti,
     e la differenza si vede scorrendo.

QUELLO CHE NON FA, e che va detto perche' e' una perdita rispetto a Plotly:
non ha un riquadro di suggerimento che elenchi PIU' serie insieme. Plotly, al
passaggio del mouse, mostra prezzo, fair value, differenza in dollari, scarto
percentuale e l'EPS usato in un colpo solo. Qui il mirino da' un valore per
asse: le altre grandezze si leggono nella riga di intestazione che questo
modulo disegna sopra il grafico.

LE BANDE DI PERDITA sono un istogramma sovrapposto, non un rettangolo:
lightweight-charts non ha rettangoli. Una serie su una scala propria che vale 1
dove gli utili sono negativi e nulla altrove riempie l'altezza della finestra e
produce lo stesso effetto visivo, restando ancorata all'asse dei tempi.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from streamlit_lightweight_charts import renderLightweightCharts

import charts

# Modalita' della scala dei prezzi in lightweight-charts.
PRICE_SCALE_NORMAL, PRICE_SCALE_LOG = 0, 1


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha})"


def _line_data(dates: pd.Series, values: pd.Series) -> list[dict]:
    """
    Punti per una serie, con i BUCHI dichiarati invece che saltati.

    Un punto senza `value` e' "whitespace": lightweight-charts tiene la data
    sull'asse ma interrompe la linea. E' quello che serve per il fair value nei
    periodi di perdita — saltare la data invece congiungerebbe i due estremi
    con un segmento dritto che afferma un valore mai esistito.
    """
    out = []
    for t, v in zip(dates, values):
        stamp = t.strftime("%Y-%m-%d")
        out.append({"time": stamp} if pd.isna(v)
                   else {"time": stamp, "value": round(float(v), 4)})
    return out


def _loss_band_data(d: pd.DataFrame) -> list[dict]:
    """
    Aree a piena altezza sui periodi di utili negativi.

    UN'AREA, NON UN ISTOGRAMMA. L'istogramma disegna una barra per punto dato,
    e con una serie settimanale su vent'anni ogni barra e' larga due pixel: la
    banda esce a picchetti invece che piena, e a colpo d'occhio sembra un
    disturbo del grafico piu' che un'informazione. L'area riempie con
    continuita' fra un punto e il successivo, e i punti "whitespace" fuori dai
    periodi di perdita la interrompono dove deve interrompersi.
    """
    rows = []
    for t, eps in zip(d["date"], d["eps"]):
        stamp = t.strftime("%Y-%m-%d")
        rows.append({"time": stamp, "value": 1}
                    if (pd.notna(eps) and eps <= 0) else {"time": stamp})
    return rows


def _event_markers(events: pd.DataFrame | None, ticker: str,
                   d: pd.DataFrame, color: str) -> list[dict]:
    """Split e altri eventi societari, come segni sull'asse dei tempi."""
    if events is None or events.empty:
        return []
    ev = events[(events["ticker"] == ticker)
                & (events["date"] >= d["date"].min())
                & (events["date"] <= d["date"].max())]
    return [{
        "time": e["date"].strftime("%Y-%m-%d"),
        "position": "aboveBar",
        "color": color,
        "shape": "arrowDown",
        "text": f"{str(e['type']).capitalize()} {e['detail']}",
    } for _, e in ev.iterrows()]


def render(d: pd.DataFrame, ticker: str, events: pd.DataFrame | None,
           pe: int, use_norm_eps: bool, scale: str, height: int = 460) -> None:
    """
    Disegna il grafico. `d` e' lo stesso frame che usa la versione Plotly:
    date, price, fv, eps, eps_used, premium_pct.
    """
    p = charts.palette()
    price_color, fv_color = p["series"][0], p["series"][1]

    # --- riga di intestazione: cio' che il mirino da solo non puo' dire ---
    last = d.iloc[-1]
    fv_now, price_now = last.get("fv"), last.get("price")
    gap = ((price_now / fv_now - 1) * 100
           if (pd.notna(fv_now) and fv_now and pd.notna(price_now)) else None)
    eps_label = "Normalized EPS (3y)" if use_norm_eps else "EPS (TTM)"
    head = st.columns([1, 1, 1, 1])
    head[0].markdown(
        f"<span style='color:{price_color};font-weight:600'>● Price</span><br>"
        f"<span style='font-size:1.3rem'>${price_now:,.2f}</span>",
        unsafe_allow_html=True)
    head[1].markdown(
        f"<span style='color:{fv_color};font-weight:600'>● Fair value "
        f"(P/E {pe})</span><br><span style='font-size:1.3rem'>"
        + (f"${fv_now:,.2f}" if pd.notna(fv_now) else "n/a") + "</span>",
        unsafe_allow_html=True)
    head[2].markdown(
        f"<span style='color:{p['muted']}'>Price vs fair value</span><br>"
        f"<span style='font-size:1.3rem'>"
        + (f"{gap:+.0f}%" if gap is not None else "—") + "</span>",
        unsafe_allow_html=True)
    head[3].markdown(
        f"<span style='color:{p['muted']}'>{eps_label}</span><br>"
        f"<span style='font-size:1.3rem'>"
        + (f"${last['eps_used']:,.2f}" if pd.notna(last.get("eps_used")) else "—")
        + "</span>", unsafe_allow_html=True)

    chart_options = {
        "height": height,
        "layout": {
            "background": {"type": "solid", "color": "rgba(0,0,0,0)"},
            "textColor": p["muted"],
            "attributionLogo": False,
        },
        "grid": {
            "vertLines": {"color": p["grid"]},
            "horzLines": {"color": p["grid"]},
        },
        "rightPriceScale": {
            "borderColor": p["grid"],
            "mode": PRICE_SCALE_LOG if scale == "Log" else PRICE_SCALE_NORMAL,
            "scaleMargins": {"top": 0.12, "bottom": 0.08},
        },
        "timeScale": {
            "borderColor": p["grid"],
            "timeVisible": False,
            # Con vent'anni di storia le etichette del secondo livello
            # (i mesi) si accavallano: si tengono solo gli anni.
            "secondsVisible": False,
        },
        # "magnet": il mirino si aggancia al punto dati piu' vicino invece di
        # restare dov'e' il puntatore. Su una serie settimanale e' la
        # differenza fra leggere il valore giusto e leggerne uno interpolato.
        "crosshair": {"mode": 1},
        "handleScroll": True,
        "handleScale": True,
    }

    series = []
    # La banda va PER PRIMA, cosi' le linee le finiscono sopra.
    if (d["eps"] <= 0).any():
        band = _hex_to_rgba(p["bad"], 0.14)
        series.append({
            "type": "Area",
            "data": _loss_band_data(d),
            "options": {
                "priceScaleId": "loss_band",   # scala propria, non quella dei $
                "topColor": band, "bottomColor": band,
                "lineColor": "rgba(0,0,0,0)", "lineWidth": 0,
                "priceLineVisible": False,
                "lastValueVisible": False,
                "crosshairMarkerVisible": False,
            },
            "priceScale": {"scaleMargins": {"top": 0, "bottom": 0},
                           "visible": False},
        })
    series.append({
        "type": "Line",
        "data": _line_data(d["date"], d["price"]),
        "options": {
            "color": price_color, "lineWidth": 2,
            "priceLineVisible": False,
            "priceFormat": {"type": "price", "precision": 2, "minMove": 0.01},
        },
        "markers": _event_markers(events, ticker, d, p["muted"]),
    })
    series.append({
        "type": "Line",
        "data": _line_data(d["date"], d["fv"]),
        "options": {
            "color": fv_color, "lineWidth": 2,
            "priceLineVisible": False,
            "lastValueVisible": True,
            "priceFormat": {"type": "price", "precision": 2, "minMove": 0.01},
        },
    })

    renderLightweightCharts(
        [{"chart": chart_options, "series": series}],
        key=f"lwc_{ticker}_{pe}_{scale}_{int(use_norm_eps)}")

    st.caption(
        "**Wheel** to zoom the time axis · **drag** to pan · **double-click** "
        "to fit everything back in. The crosshair snaps to the nearest data "
        "point. Shaded band = negative earnings, where no fair value exists "
        "and the orange line breaks. Arrows = corporate events."
    )
