#!/usr/bin/env python3
"""
screener_grid.py — La tabella dello Screener disegnata con AG Grid.

PERCHE' ESISTE. `st.dataframe` mostra dei numeri; una tabella da screener deve
farci lavorare sopra. Le tre cose che mancavano e che qui ci sono:

  1. FILTRO PER COLONNA. Sotto ogni intestazione c'e' una casella: "P/E < 15",
     "settore contiene Bank", "sconto < -30". Si combinano fra loro, e sono
     l'unico modo di cercare qualcosa che i filtri in cima alla pagina non
     prevedono. Prima l'unica risposta era esportare in CSV e aprire Excel.
  2. COLONNE BLOCCATE. Ticker e societa' restano visibili mentre si scorre a
     destra fra venti colonne: senza, a meta' tabella non si sa piu' di chi si
     stiano leggendo i numeri.
  3. ORDINAMENTO SUI NUMERI VERI. La tabella classica e' costruita di stringhe
     gia' formattate ("$1,234.50"), quindi ordinabile solo alfabeticamente.
     Qui la cella riceve il numero e lo formatta al momento del disegno: il
     valore resta un valore, e ordinamento e filtri numerici funzionano.

LE INTESTAZIONI SONO A DUE LIVELLI — identita', valutazione, multipli,
qualita'. Ventidue colonne in fila sono un muro; raggruppate diventano quattro
domande, e si capisce a colpo d'occhio dove guardare.

I COLORI RESTANO QUELLI DELLA TABELLA CLASSICA, e con la stessa convenzione:
verde = a favore di chi compra. Attenzione al PEG e al Lynch ratio, che sono
l'uno il reciproco dell'altro e quindi hanno la scala ROVESCIATA: PEG basso
buono, Lynch ratio alto buono. Sbagliarne uno dei due e' il modo piu' rapido di
far leggere la tabella al contrario.
"""

from __future__ import annotations

import pandas as pd
from st_aggrid import AgGrid, GridUpdateMode, JsCode

# --- formattatori, eseguiti nel browser al momento di disegnare la cella -----
#
# Restituiscono TUTTI il trattino sul valore mancante. Le celle vuote qui sono
# normalissime — il fair P/E di una Turnaround, il FCF yield di una banca, il
# dividendo di chi non lo paga — e una tabella cosparsa di "null" si legge come
# un guasto del programma invece che come un'informazione che non esiste.
_MISSING = "if (v === null || v === undefined || v === '' || isNaN(v)) return '—';"


def _fmt(body: str) -> JsCode:
    return JsCode(f"function(p) {{ const v = p.value; {_MISSING} {body} }}")


FMT_USD = _fmt("return '$' + v.toLocaleString('en-US', "
               "{minimumFractionDigits: 2, maximumFractionDigits: 2});")
FMT_PCT_SIGNED = _fmt("return (v > 0 ? '+' : '') + v.toFixed(0) + '%';")
FMT_PCT1 = _fmt("return v.toFixed(1) + '%';")
FMT_PCT2 = _fmt("return v.toFixed(2) + '%';")
FMT_RATIO1 = _fmt("return v.toFixed(1);")
FMT_RATIO2 = _fmt("return v.toFixed(2);")
FMT_INT = _fmt("return v.toFixed(0);")

# Lo scarto di P/E misurato contro il SETTORE invece che contro l'industria
# porta un asterisco: e' un gruppo piu' largo, quindi un confronto meno
# stringente, e va dichiarato. La base viaggia in una colonna nascosta della
# riga, cosi' il formattatore la legge senza occupare spazio sullo schermo.
FMT_PE_GAP = JsCode(f"""
function(p) {{
    const v = p.value; {_MISSING}
    const star = (p.data && p.data['_pe_basis'] === 'sector') ? '*' : '';
    return (v > 0 ? '+' : '') + v.toFixed(0) + '%' + star;
}}""")

# --- colori: verde = a favore di chi compra ---------------------------------
GREEN, RED, AMBER, MUTED = "#22c55e", "#f87171", "#fbbf24", "#9ca3af"


def _style(rule: str) -> JsCode:
    return JsCode(f"function(p) {{ const v = p.value; "
                  f"if (v === null || v === undefined || isNaN(v)) return null; "
                  f"{rule} return null; }}")


STYLE_DISCOUNT = _style(
    f"if (v <= -30) return {{color: '{GREEN}', fontWeight: 600}};"
    f"if (v >= 30) return {{color: '{RED}'}};")
STYLE_PE_GAP = _style(
    f"if (v <= -20) return {{color: '{GREEN}', fontWeight: 600}};"
    f"if (v >= 20) return {{color: '{RED}'}};")
# PEG: basso = conveniente
STYLE_PEG = _style(
    f"if (v < 1) return {{color: '{GREEN}', fontWeight: 600}};"
    f"if (v > 2) return {{color: '{RED}'}};")
# Lynch ratio: RECIPROCO del PEG, quindi alto = conveniente
STYLE_LYNCH = _style(
    f"if (v > 1.5) return {{color: '{GREEN}', fontWeight: 600}};"
    f"if (v < 1) return {{color: '{RED}'}};")

STYLE_VALUATION = JsCode(f"""
function(p) {{
    if (p.value === 'Undervalued')
        return {{color: '{GREEN}', fontWeight: 600}};
    if (p.value === 'Overvalued') return {{color: '{RED}'}};
    return {{color: '{MUTED}'}};
}}""")
STYLE_EPS_FLAG = JsCode(f"""
function(p) {{
    if (p.value && p.value.indexOf('check') >= 0)
        return {{color: '{AMBER}', fontWeight: 600}};
    return {{color: '{MUTED}'}};
}}""")
STYLE_CONFIDENCE = JsCode(f"""
function(p) {{
    if (p.value === 'high') return {{color: '{GREEN}'}};
    if (p.value === 'low') return {{color: '{RED}'}};
    return {{color: '{MUTED}'}};
}}""")


def _col(field: str, width: int, *, fmt: JsCode | None = None,
         style: JsCode | None = None, numeric: bool = True,
         pinned: str | None = None, tip: str | None = None) -> dict:
    """Una colonna. Il tipo decide il filtro: numerico o testuale."""
    d: dict = {
        "field": field, "width": width,
        "filter": "agNumberColumnFilter" if numeric else "agTextColumnFilter",
        "type": "rightAligned" if numeric else None,
    }
    if fmt is not None:
        d["valueFormatter"] = fmt
    if style is not None:
        d["cellStyle"] = style
    if pinned:
        d["pinned"] = pinned
    if tip:
        d["headerTooltip"] = tip
    return {k: v for k, v in d.items() if v is not None}


def build_grid_options(min_peers: int) -> dict:
    """Le colonne, raggruppate per domanda invece che in fila."""
    return {
        # IL PUNTO NEL NOME DI UNA COLONNA NON E' UN PERCORSO.
        # Per AG Grid `field: "Cur. P/E"` significa "dentro l'oggetto `Cur `,
        # prendi la chiave ` P/E`", e la colonna esce vuota senza alcun errore:
        # e' cosi' che «Conf.», «Cur. P/E» e «Δ P/E vs ind.» risultavano tutte
        # e tre bianche pur avendo i dati. Le intestazioni sono testo scritto
        # per essere letto, non identificatori: qui si dichiara che vanno prese
        # alla lettera.
        "suppressFieldDotNotation": True,
        "defaultColDef": {
            "sortable": True, "resizable": True, "filter": True,
            "floatingFilter": True, "suppressMenu": False,
        },
        "columnDefs": [
            {"headerName": "", "children": [
                _col("Ticker", 92, numeric=False, pinned="left"),
                _col("Company", 210, numeric=False, pinned="left"),
            ]},
            {"headerName": "Classification", "children": [
                _col("Sector", 150, numeric=False),
                _col("Industry", 190, numeric=False,
                     tip="The peer group every relative figure is measured "
                         "against."),
                _col("Category", 150, numeric=False,
                     tip="The Lynch type, which decides the fair multiple."),
                _col("Conf.", 90, numeric=False, style=STYLE_CONFIDENCE,
                     tip="How much to trust the classification: high · medium "
                         "· low."),
            ]},
            {"headerName": "Price vs fair value", "children": [
                _col("Price", 100, fmt=FMT_USD),
                _col("EPS used", 100, fmt=FMT_USD,
                     tip="The earnings per share the fair value is built on, "
                         "after arbitration between SEC filings and provider."),
                _col("× Fair P/E", 100, fmt=FMT_INT,
                     tip="The multiple the category assigns to this company."),
                _col("Fair Value", 110, fmt=FMT_USD,
                     tip="EPS used × fair P/E."),
                _col("Discount %", 110, fmt=FMT_PCT_SIGNED,
                     style=STYLE_DISCOUNT,
                     tip="Price vs fair value. Negative = trading below it."),
                _col("Valuation", 115, numeric=False, style=STYLE_VALUATION),
            ]},
            {"headerName": "Multiples", "children": [
                _col("Cur. P/E", 95, fmt=FMT_RATIO1),
                _col("Δ P/E vs ind.", 120, fmt=FMT_PE_GAP, style=STYLE_PE_GAP,
                     tip="Trailing P/E versus the median of its own industry. "
                         "Negative = cheaper than its peers. An asterisk means "
                         f"the industry has fewer than {min_peers} companies "
                         "here and the benchmark falls back to the sector."),
                _col("PEG", 85, fmt=FMT_RATIO2, style=STYLE_PEG,
                     tip="P/E ÷ growth. Below 1 cheap for the growth, above 2 "
                         "expensive."),
                _col("Lynch ratio", 110, fmt=FMT_RATIO2, style=STYLE_LYNCH,
                     tip="(growth + dividend) ÷ P/E — the RECIPROCAL of PEG, "
                         "so the reading flips: above 1.5 attractive."),
            ]},
            {"headerName": "Growth and quality", "children": [
                _col("EPS growth 5y", 125, fmt=FMT_PCT1),
                _col("Rev. CAGR 5y", 120, fmt=FMT_PCT1),
                _col("ROE", 90, fmt=FMT_PCT1),
                _col("FCF yield", 105, fmt=FMT_PCT2),
                _col("Div %", 90, fmt=FMT_PCT2),
                _col("EPS data", 105, numeric=False, style=STYLE_EPS_FLAG,
                     tip="✓ the two EPS sources agree · ⚠ worth checking."),
            ]},
            {"field": "_pe_basis", "hide": True},
        ],
        "rowSelection": "single",
        "suppressCellFocus": True,
        "rowHeight": 34,
        "headerHeight": 34,
        "floatingFiltersHeight": 34,
        "groupHeaderHeight": 30,
        "animateRows": True,
    }


def render(show: pd.DataFrame, pe_basis: pd.Series | None,
           min_peers: int, height: int = 560) -> str | None:
    """
    Disegna la tabella e restituisce il ticker selezionato, se ce n'e' uno.

    `show` arriva con i VALORI NUMERICI e le intestazioni gia' tradotte: la
    formattazione avviene nel browser, cosi' ordinamento e filtri lavorano sui
    numeri e non sulle stringhe.
    """
    data = show.copy()
    data["_pe_basis"] = (list(pe_basis) if pe_basis is not None
                         else [""] * len(data))

    grid = AgGrid(
        data,
        gridOptions=build_grid_options(min_peers),
        height=height,
        theme="streamlit",
        allow_unsafe_jscode=True,
        update_mode=GridUpdateMode.SELECTION_CHANGED,
        show_search=True,
        show_download_button=True,
        key="screen_aggrid",
    )

    # A seconda della versione la selezione torna come DataFrame o come lista
    # di dizionari: entrambe le forme vanno accettate, o la tabella smette di
    # navigare al primo aggiornamento della libreria.
    sel = grid.selected_rows
    if sel is None:
        return None
    if isinstance(sel, pd.DataFrame):
        return str(sel.iloc[0]["Ticker"]) if not sel.empty else None
    if isinstance(sel, list) and sel:
        return str(sel[0].get("Ticker")) or None
    return None
