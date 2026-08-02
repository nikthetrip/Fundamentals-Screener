#!/usr/bin/env python3
"""
charts.py — Un solo posto in cui si decide che aspetto ha un grafico.

PERCHE' ESISTE. I grafici erano costruiti uno per uno dentro app.py, ognuno con
i suoi colori scelti sul momento (`#2563eb` qui, `#16a34a` la', verde per il
prezzo in una scheda e verde per l'utile netto in un'altra) e il suo
`template="plotly_white"` fisso. Due conseguenze concrete:

  - lo stesso colore significava cose diverse in schede diverse, che e' il modo
    piu' rapido per far leggere male un grafico a chi passa da una all'altra;
  - con il tema scuro di Streamlit i grafici restavano bianchi.

Qui la tavolozza e' UNA, i ruoli sono nominati (`series[0]` e' sempre la
grandezza principale) e il tema segue quello dell'interfaccia.

LA TAVOLOZZA. E' quella di riferimento della skill dataviz, presa senza
modifiche e nell'ORDINE documentato: l'ordine degli slot non e' estetico, e'
il meccanismo che garantisce la distinguibilita' per chi ha un deficit di
percezione del colore (le coppie adiacenti sono validate a ΔE ≥ 8 in OKLab, in
entrambi i temi). Usare gli slot in ordine, e non piu' di quattro per grafico,
e' la condizione per restare dentro quella garanzia.

DUE REGOLE CHE VALGONO PER TUTTI I GRAFICI DI QUESTO FILE.

1. MAI DUE ASSI Y. Un grafico con due scale verticali fa inventare al lettore
   una correlazione che nei dati non c'e', perche' l'allineamento fra le due
   scale e' arbitrario. Dove servono due grandezze di ordine diverso — ricavi in
   miliardi e margine in percentuale — si fanno DUE grafici impilati che
   condividono l'asse dei tempi, oppure si indicizzano entrambe a 100.
2. Il colore segue l'ENTITA', non la sua posizione. "Ricavi" e' sempre lo slot
   1 in ogni grafico di questa dashboard, anche quando e' l'unica serie
   presente, cosi' che passando da una scheda all'altra il blu voglia sempre
   dire la stessa cosa.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# --- tavolozza di riferimento (skill dataviz), nei due temi -----------------
_LIGHT = {
    "surface": "#fcfcfb",
    "text": "#0b0b0b",
    "muted": "#52514e",
    "grid": "#e7e6e2",
    "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
               "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
    "good": "#008300",
    "bad": "#e34948",
    "band": "rgba(227,73,72,0.07)",
}
_DARK = {
    "surface": "#1a1a19",
    "text": "#ffffff",
    "muted": "#c3c2b7",
    "grid": "#33332f",
    "series": ["#3987e5", "#d95926", "#199e70", "#c98500",
               "#d55181", "#008300", "#9085e9", "#e66767"],
    "good": "#199e70",
    "bad": "#e66767",
    "band": "rgba(230,103,103,0.08)",
}


def is_dark() -> bool:
    """
    Il tema attivo di Streamlit. L'API e' cambiata fra versioni, quindi si
    prova la piu' recente e si ripiega, senza mai far fallire un grafico per
    colpa di un tema non rilevato: nel dubbio, chiaro.
    """
    try:
        theme = getattr(st.context, "theme", None)
        if theme is not None and getattr(theme, "type", None):
            return theme.type == "dark"
    except Exception:
        pass
    try:
        return (st.get_option("theme.base") or "light") == "dark"
    except Exception:
        return False


def palette() -> dict:
    return _DARK if is_dark() else _LIGHT


def style(fig: go.Figure, *, height: int = 340, ylab: str | None = None,
          legend: bool = True, money: bool = False,
          hovermode: str = "x unified") -> go.Figure:
    """
    Cromatura comune: griglia sottile e in secondo piano, nessun bordo, testo
    nei toni dell'inchiostro e mai nel colore della serie, legenda in alto a
    sinistra dove l'occhio la trova prima di leggere i dati.
    """
    p = palette()
    fig.update_layout(
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=p["text"], size=12),
        hovermode=hovermode,
        hoverlabel=dict(bgcolor=p["surface"], font_size=12,
                        bordercolor=p["grid"]),
        margin=dict(t=30 if legend else 12, r=12, b=36, l=64),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0,
                    font=dict(color=p["muted"], size=11),
                    bgcolor="rgba(0,0,0,0)"),
        barcornerradius=4,          # estremita' arrotondate, ancorate alla base
        bargap=0.28, bargroupgap=0.08,
    )
    fig.update_xaxes(showgrid=False, zeroline=False,
                     linecolor=p["grid"], tickfont=dict(color=p["muted"]))
    fig.update_yaxes(showgrid=True, gridcolor=p["grid"], gridwidth=1,
                     zeroline=True, zerolinecolor=p["grid"], zerolinewidth=1,
                     linecolor="rgba(0,0,0,0)",
                     tickfont=dict(color=p["muted"]),
                     title=dict(text=ylab, font=dict(color=p["muted"], size=11))
                     if ylab else None,
                     **(_MONEY_TICKS if money else {}))
    return fig


# Solo il simbolo di valuta. Il suffisso delle grandi cifre lo mette gia'
# Plotly nella forma giusta per la finanza (4B, non 4G): il formato SI, che
# scriverebbe "G", si ottiene solo chiedendolo esplicitamente con
# tickformat="~s" — quindi non lo si chiede.
_MONEY_TICKS = dict(tickprefix="$")


def _money_hover(name: str) -> str:
    return f"{name}: %{{y:$,.3s}}<extra></extra>"


def bars(x, series: list[tuple[str, object]], *, ylab: str = "$",
         height: int = 320, pct: bool = False) -> go.Figure | None:
    """
    Barre raggruppate. `series` e' [(etichetta, valori)] IN ORDINE: la prima
    prende lo slot 1 della tavolozza, la seconda lo slot 2, e cosi' via.
    Restituisce None se non c'e' niente da disegnare, cosi' che il chiamante
    possa saltare il blocco invece di mostrare un riquadro vuoto.
    """
    p = palette()
    usable = [(lbl, v) for lbl, v in series
              if v is not None and pd.Series(v).notna().any()]
    if not usable:
        return None
    fig = go.Figure()
    for i, (lbl, vals) in enumerate(usable):
        fig.add_trace(go.Bar(
            x=x, y=vals, name=lbl, marker_color=p["series"][i % 8],
            hovertemplate=(f"{lbl}: %{{y:.1f}}%<extra></extra>" if pct
                           else _money_hover(lbl)),
        ))
    fig.update_layout(barmode="group")
    return style(fig, height=height, ylab=ylab, legend=len(usable) > 1,
                 money=not pct)


def lines(x, series: list[tuple[str, object]], *, ylab: str = "$",
          height: int = 300, pct: bool = False, markers: bool = True,
          suffix: str = "") -> go.Figure | None:
    """Linee. Stesse regole di ordine e colore delle barre."""
    p = palette()
    usable = [(lbl, v) for lbl, v in series
              if v is not None and pd.Series(v).notna().any()]
    if not usable:
        return None
    fig = go.Figure()
    for i, (lbl, vals) in enumerate(usable):
        fig.add_trace(go.Scatter(
            x=x, y=vals, name=lbl, mode="lines+markers" if markers else "lines",
            line=dict(color=p["series"][i % 8], width=2),
            marker=dict(size=8, line=dict(width=2, color=p["surface"])),
            connectgaps=False,
            hovertemplate=(f"{lbl}: %{{y:,.1f}}{suffix or '%'}<extra></extra>"
                           if pct else f"{lbl}: %{{y:$,.2f}}<extra></extra>"),
        ))
    return style(fig, height=height, ylab=ylab, legend=len(usable) > 1)


def indexed(x, series: list[tuple[str, object]], *, height: int = 320
            ) -> go.Figure | None:
    """
    Piu' grandezze di ordini di grandezza diversi sullo STESSO asse, riportate
    tutte a 100 al primo punto in cui esistono.

    E' la risposta corretta a "voglio vedere ricavi, utile e cassa insieme":
    l'alternativa — due assi verticali — inventerebbe un allineamento fra le
    scale che nei dati non c'e'. Qui la domanda a cui il grafico risponde e'
    "chi e' cresciuto di piu'", e la risposta si legge direttamente.

    DUE ESCLUSIONI, ed entrambe servono.

    1. Base non positiva: rapportare a una base negativa produce un indice che
       sale quando la grandezza scende.
    2. Base TROPPO PICCOLA rispetto alla serie stessa. E' il caso che rovina il
       grafico in silenzio: il free cash flow di Alcoa nel 2020 era una frazione
       del suo livello abituale, e indicizzato su quella base oscillava fra
       +1.400 e -1.050, schiacciando i ricavi in una riga piatta. Un indice
       costruito su una base che non rappresenta la serie non misura la
       crescita, misura quanto era anomalo il primo anno. Si esclude quando la
       base sta sotto il 20% del livello tipico della serie.
    """
    p = palette()
    fig = go.Figure()
    n = 0
    skipped: list[str] = []
    for lbl, vals in series:
        s = pd.Series(vals, dtype="float64")
        valid = s.dropna()
        if valid.empty:
            continue
        base = valid.iloc[0]
        if base <= 0 or base < 0.2 * valid.abs().median():
            skipped.append(lbl)
            continue
        fig.add_trace(go.Scatter(
            x=x, y=s / base * 100, name=lbl, mode="lines+markers",
            line=dict(color=p["series"][n % 8], width=2),
            marker=dict(size=8, line=dict(width=2, color=p["surface"])),
            connectgaps=False,
            hovertemplate=f"{lbl}: %{{y:,.0f}}<extra></extra>",
        ))
        n += 1
    if not n:
        return None
    fig.add_hline(y=100, line=dict(color=p["grid"], width=1))
    fig._skipped = skipped          # il chiamante lo dichiara in didascalia
    return style(fig, height=height, ylab="index (first year = 100)",
                 legend=n > 1)


def log_yaxis(fig: go.Figure, lo: float, hi: float, *, prefix: str = "$"
              ) -> go.Figure:
    """
    Asse logaritmico con etichette che dicono il VALORE, non la mantissa.

    IL DIFETTO CHE RIPARA. Sotto le due decadi Plotly etichetta le tacche
    minori con la sola prima cifra: su un titolo che va da 20 a 400 dollari
    l'asse si legge «2 3 4 5 6 7 8 9 100 2 3 4», dove i primi otto numeri
    valgono 20-90 e gli ultimi tre 200-400. Lo stesso simbolo «2» compare due
    volte a significare venti e duecento. Non e' un dettaglio estetico: chi
    guarda il grafico legge il prezzo sull'asse, e un asse ambiguo fa sbagliare
    di un fattore dieci.

    Le tacche si mettono quindi a mano, sulla scala 1-2-5 di ogni decade — la
    stessa che usano le piattaforme di mercato — e ognuna porta scritto quanto
    vale.
    """
    if not lo or not hi or lo <= 0 or hi <= lo:
        fig.update_yaxes(type="log")
        return fig
    import math
    ticks: list[float] = []
    start = math.floor(math.log10(lo))
    for decade in range(start, math.ceil(math.log10(hi)) + 1):
        for mult in (1, 2, 5):
            value = mult * (10 ** decade)
            if lo * 0.92 <= value <= hi * 1.08:
                ticks.append(value)
    # Su un intervallo stretto (meno di mezza decade) la scala 1-2-5 lascia due
    # sole tacche: si infittisce, altrimenti l'asse non ha riferimenti.
    if len(ticks) < 6:
        ticks = []
        for decade in range(start, math.ceil(math.log10(hi)) + 1):
            for mult in (1, 1.5, 2, 3, 4, 5, 7):
                value = mult * (10 ** decade)
                if lo * 0.92 <= value <= hi * 1.08:
                    ticks.append(value)
    if len(ticks) < 2:
        fig.update_yaxes(type="log")
        return fig
    fig.update_yaxes(
        type="log", tickmode="array", tickvals=ticks,
        ticktext=[f"{prefix}{v:,.0f}" if v >= 1 else f"{prefix}{v:,.2f}"
                  for v in ticks])
    return fig


def donut(labels, values, *, height: int = 300, hole: float = 0.62
          ) -> go.Figure | None:
    """
    Una ripartizione: quanta parte del tutto fa ciascuna voce.

    E' l'UNICO caso in cui questo file disegna una torta, e vale la pena dire
    perche'. Una torta risponde male a quasi ogni domanda — confrontare due
    fette vicine per angolo e' piu' difficile che confrontare due barre — ma
    qui la domanda e' esattamente "che frazione del totale", e la somma delle
    parti E' il tutto per costruzione. Il buco centrale toglie l'area dal
    confronto e lascia solo la lunghezza dell'arco, che si legge meglio.

    LE VOCI SONO ORDINATE, dalla piu' grande alla piu' piccola, e i colori
    seguono quell'ordine: cosi' la fetta principale prende sempre lo slot 1
    della tavolozza, in questo grafico come in tutti gli altri.
    """
    p = palette()
    pairs = [(str(lbl), float(v)) for lbl, v in zip(labels, values)
             if v is not None and not pd.isna(v) and float(v) > 0]
    if len(pairs) < 2:
        return None
    pairs.sort(key=lambda x: -x[1])
    names = [x[0] for x in pairs]
    vals = [x[1] for x in pairs]

    fig = go.Figure(go.Pie(
        labels=names, values=vals, hole=hole, sort=False, direction="clockwise",
        marker=dict(colors=[p["series"][i % 8] for i in range(len(names))],
                    line=dict(color=p["surface"], width=2)),
        textinfo="none",
        hovertemplate="%{label}<br>%{value:$,.3s} · %{percent}<extra></extra>",
    ))
    fig.update_layout(
        height=height,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=p["text"], size=12),
        margin=dict(t=8, r=8, b=8, l=8),
        hoverlabel=dict(bgcolor=p["surface"], font_size=12,
                        bordercolor=p["grid"]),
        showlegend=True,
        legend=dict(orientation="h", yanchor="top", y=-0.02, x=0.5,
                    xanchor="center", font=dict(color=p["muted"], size=11),
                    bgcolor="rgba(0,0,0,0)"),
    )
    return fig


def capital_structure(market_cap, debt, cash, *, height: int = 260
                      ) -> go.Figure | None:
    """
    Da cosa e' fatto il prezzo dell'AZIENDA: capitalizzazione piu' debito meno
    cassa fa l'enterprise value.

    E' una CASCATA, non una barra impilata. Con una pila la cassa — che e' una
    voce in sottrazione — finisce disegnata a sinistra dello zero, e si legge
    come se l'azienda avesse un valore negativo prima di cominciare. La cascata
    e' la forma che dice l'aritmetica: si parte da un valore, due passi lo
    modificano nel verso giusto, l'ultima colonna e' il totale.
    """
    p = palette()
    mc = None if market_cap is None or pd.isna(market_cap) else float(market_cap)
    db = 0.0 if debt is None or pd.isna(debt) else float(debt)
    ch = 0.0 if cash is None or pd.isna(cash) else float(cash)
    if not mc or mc <= 0:
        return None

    fig = go.Figure(go.Waterfall(
        orientation="v",
        measure=["absolute", "relative", "relative", "total"],
        x=["Market cap", "+ Debt", "− Cash", "Enterprise value"],
        y=[mc, db, -ch, None],
        connector=dict(line=dict(color=p["grid"], width=1)),
        increasing=dict(marker=dict(color=p["series"][1])),   # il debito aggiunge
        decreasing=dict(marker=dict(color=p["series"][2])),   # la cassa toglie
        totals=dict(marker=dict(color=p["series"][0])),
        text=[fmt_compact(v) for v in (mc, db, -ch, mc + db - ch)],
        textposition="outside",
        textfont=dict(color=p["muted"], size=11),
        hovertemplate="%{x}: %{y:$,.3s}<extra></extra>",
    ))
    return style(fig, height=height, ylab="$", legend=False,
                 money=True, hovermode="closest")


def fmt_compact(v: float) -> str:
    """12.9 B$ / 491 M$ — le stesse unita' usate nel resto della dashboard."""
    if v is None or pd.isna(v):
        return "—"
    a = abs(v)
    for unit, scale in (("T$", 1e12), ("B$", 1e9), ("M$", 1e6), ("K$", 1e3)):
        if a >= scale:
            s = v / scale
            return f"{s:,.{0 if abs(s) >= 100 else 1}f} {unit}"
    return f"{v:,.0f} $"
