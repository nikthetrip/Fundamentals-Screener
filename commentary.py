#!/usr/bin/env python3
"""
commentary.py — Il commento ai numeri, sotto i KPI di ogni sezione.

CHE COSA E', E CHE COSA NON E'. Non c'e' nessun modello linguistico che gira
quando apri la pagina: sarebbe una chiamata a pagamento a ogni visita a ogni
scheda, e questa applicazione deve restare a costo zero. Qui dentro ci sono due
strati distinti:

  1. UN MOTORE DI RILIEVI. Guarda i numeri della societa', li confronta con la
     mediana dei pari, con le soglie del suo settore e con la direzione degli
     ultimi esercizi, e produce un elenco di FATTI notevoli. Solo aritmetica:
     ogni frase e' ricalcolabile a mano dai numeri che stanno nei riquadri
     sopra il commento.
  2. LA PROSA, scritta una volta sola qui dentro. Il testo non "descrive" i
     numeri, li INTERPRETA — dice perche' un margine in calo per tre anni conta
     piu' del suo livello, perche' un ROE alto con poco capitale proprio non e'
     la stessa cosa di un ROE alto senza debito.

Il vantaggio di questa divisione non e' il costo, e' che UN NUMERO NEL TESTO
NON PUO' ESSERE SBAGLIATO: viene dalla stessa variabile del riquadro che sta
sopra, formattata dalla stessa funzione. Un modello che riscrive una cifra puo'
sbagliarla, e un commento con un numero diverso da quello accanto vale meno di
nessun commento.

IL CONTESTO DI SETTORE E' STATICO, e va saputo. Le soglie qui sotto — un
patrimonio netto basso e' normale in banca, un payout alto e' normale in un
REIT, il capex alto e' normale in una utility — sono conoscenza scritta nel
codice, non lettura del mercato di oggi. Il "ciclo" che il commento riconosce e'
quello che i DATI mostrano (utili erratici, perdite recenti, utile corrente
lontano dalla mediana triennale), non il momento macroeconomico: di quello
questo file non sa nulla, e non lo pretende.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import streamlit as st

# Costo del capitale di riferimento per giudicare il ROIC. E' un ordine di
# grandezza, non un numero preciso: sopra questa soglia la crescita crea
# valore, sotto lo distrugge, ed e' l'unico modo di leggere un ROIC senza
# confrontarlo con nient'altro.
COST_OF_CAPITAL = 9.0


@dataclass
class Finding:
    """
    Un rilievo: il testo, il verso e quanto pesa nel giudizio finale.

    `tone` — "bull" a favore, "bear" contro, "flag" un avvertimento sul DATO
    piu' che sull'azienda, "neutral" un fatto che va detto e non pende.
    `weight` — da 1 (dettaglio) a 3 (fatto strutturale). Serve solo al giudizio
    di sintesi: nel testo tutti i rilievi hanno la stessa dignita'.
    """
    tone: str
    text: str
    weight: int = 1


@dataclass
class SectorContext:
    """Cosa e' normale in questo settore, e cosa non va letto alla lettera."""
    cyclical: bool = False
    capital_heavy: bool = False
    # Voci che in questo settore non si interpretano con le soglie generali.
    ignore: tuple[str, ...] = ()
    note: str = ""
    # Soglie sostituite rispetto a quelle generali.
    overrides: dict = field(default_factory=dict)


# Le norme di settore. Sono il motivo per cui lo stesso numero riceve due
# commenti diversi: un equity ratio del 12% e' un allarme in una societa'
# industriale e la normalita' in una banca, il cui mestiere e' impiegare
# denaro di terzi.
SECTOR_CONTEXT: dict[str, SectorContext] = {
    "Financial Services": SectorContext(
        ignore=("equity_ratio_pct", "debt_to_equity", "net_debt_to_ebit",
                "fcf_margin_pct", "fcf_conversion_pct", "capex_to_revenue_pct",
                "asset_turnover"),
        note="Banks and insurers fund themselves with other people's money by "
             "design, and their cash flow statement has a different shape: "
             "leverage and cash-flow ratios are not read here the way they are "
             "elsewhere.",
        overrides={"roe_good": 12.0}),
    "Real Estate": SectorContext(
        capital_heavy=True,
        ignore=("fcf_conversion_pct", "payout_ratio_pct", "net_margin_pct"),
        note="REITs must distribute most of their income by law and carry "
             "property debt as a matter of course, so a payout above 100% of "
             "accounting profit and heavy leverage are structural, not "
             "warnings."),
    "Utilities": SectorContext(
        capital_heavy=True,
        note="Regulated utilities invest heavily and carry high debt against "
             "predictable, rate-set revenue: capex intensity and leverage that "
             "would be alarming elsewhere are the business model here.",
        overrides={"net_debt_ebit_high": 6.0, "capex_high": 25.0}),
    "Energy": SectorContext(
        cyclical=True,
        note="Earnings follow the commodity cycle: margins at a peak are the "
             "least repeatable part of the record, and a low P/E on peak "
             "earnings is the classic cyclical trap."),
    "Basic Materials": SectorContext(
        cyclical=True, capital_heavy=True,
        note="Commodity-driven earnings: the level of this year's margin says "
             "less than where it sits in the cycle."),
    "Industrials": SectorContext(
        capital_heavy=True,
        note="Order books follow the investment cycle, so single-year growth "
             "figures move more than the underlying business does."),
    "Consumer Cyclical": SectorContext(
        cyclical=True,
        note="Demand tracks the consumer cycle: a weak year is not "
             "automatically a broken business, and a strong one is not "
             "automatically a trend."),
    "Technology": SectorContext(
        note="Asset-light economics: high margins and low capital intensity "
             "are the norm, which is also why the market pays higher multiples "
             "here than almost anywhere else."),
    "Communication Services": SectorContext(
        note="The sector holds both asset-light platforms and capital-heavy "
             "telecoms: compare with the industry rather than the sector "
             "wherever possible."),
    "Healthcare": SectorContext(
        note="Research spending runs through the income statement, so margins "
             "understate the cash economics of a business that is investing "
             "in its pipeline."),
    "Consumer Defensive": SectorContext(
        note="Slow, predictable demand: the interesting questions here are "
             "margin direction and the dividend, not growth."),
}

DEFAULT_CONTEXT = SectorContext()

# Soglie generali. Vengono sostituite da quelle di settore dove esistono.
T = {
    "margin_good": 15.0, "margin_thin": 5.0,
    "fcf_margin_good": 15.0, "fcf_margin_thin": 3.0,
    "conversion_good": 90.0, "conversion_poor": 60.0,
    "roe_good": 15.0, "roe_poor": 8.0,
    "roic_good": 12.0,
    "equity_low": 30.0, "equity_good": 55.0,
    "net_debt_ebit_high": 3.5, "net_debt_ebit_safe": 1.0,
    "payout_high": 80.0,
    "capex_high": 12.0,
    "growth_fast": 15.0, "growth_slow": 3.0,
}


def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _ctx(sector) -> SectorContext:
    if not isinstance(sector, str):
        return DEFAULT_CONTEXT
    return SECTOR_CONTEXT.get(sector, DEFAULT_CONTEXT)


def _t(ctx: SectorContext, name: str) -> float:
    return ctx.overrides.get(name, T[name])


def _trend(a: pd.DataFrame | None, col: str, years: int = 4
           ) -> tuple[str, float | None]:
    """
    Dove sta andando una voce: ("up" | "down" | "flat", variazione %).

    Confronta la MEDIA della prima meta' della finestra con quella della
    seconda, invece dei due estremi: due punti scelti male raccontano una
    direzione che non c'e', ed e' esattamente l'errore che questo commento non
    deve fare, visto che afferma tendenze in italiano corrente.
    """
    if a is None or col not in a.columns:
        return "flat", None
    s = pd.to_numeric(a[col], errors="coerce").dropna().tail(years)
    if len(s) < 3:
        return "flat", None
    half = len(s) // 2
    first, second = s.iloc[:half].mean(), s.iloc[-half:].mean()
    if not first or first <= 0:
        return "flat", None
    chg = (second / first - 1) * 100
    return ("up" if chg > 8 else "down" if chg < -8 else "flat"), chg


def _margin_trend(a: pd.DataFrame | None, num: str, den: str = "revenue",
                  years: int = 5) -> tuple[str, float | None]:
    """Direzione di un margine, in PUNTI percentuali fra prima e seconda meta'
    della finestra. Un margine si confronta in punti, non in percentuale di se
    stesso: "il margine e' sceso del 20%" non dice se da 50 a 40 o da 5 a 4."""
    if a is None or num not in a.columns or den not in a.columns:
        return "flat", None
    d = a[[num, den]].apply(pd.to_numeric, errors="coerce").dropna().tail(years)
    d = d[d[den] > 0]
    if len(d) < 3:
        return "flat", None
    m = d[num] / d[den] * 100
    half = len(m) // 2
    gap = m.iloc[-half:].mean() - m.iloc[:half].mean()
    return ("up" if gap > 1.5 else "down" if gap < -1.5 else "flat"), gap


def _peer_gap(r: pd.Series, peers: pd.Series | None, key: str) -> float | None:
    """Scarto contro i pari: punti percentuali per le percentuali, variazione
    relativa per i rapporti. Restituisce None quando il confronto non esiste."""
    if peers is None or key not in peers.index:
        return None
    v, p = _num(r.get(key)), _num(peers.get(key))
    if v is None or p is None:
        return None
    if key.endswith("_pct") or key in ("dividend_yield",):
        return v - p
    return (v / p - 1) * 100 if p else None


# =========================================================================== #
# I RILIEVI, UNA FUNZIONE PER PROSPETTO                                       #
# =========================================================================== #

def income_findings(r, peers, a, ctx: SectorContext, f) -> list[Finding]:
    out: list[Finding] = []
    rev_g = _num(r.get("revenue_growth_yoy_pct"))
    eps_g = _num(r.get("eps_growth_yoy"))
    om, nm = _num(r.get("operating_margin_pct")), _num(r.get("net_margin_pct"))

    if rev_g is not None:
        if rev_g >= _t(ctx, "growth_fast"):
            out.append(Finding("bull", f"Revenue is up **{f('revenue_growth_yoy_pct', rev_g)}** "
                               "over the last twelve months — the top line is "
                               "still expanding, which is what every margin "
                               "below is a fraction of.", 2))
        elif rev_g < 0:
            out.append(Finding("bear", f"Revenue is **{f('revenue_growth_yoy_pct', rev_g)}** "
                               "over the last twelve months: the business is "
                               "selling less than a year ago, and margins have "
                               "to do the work that volume is not doing.", 3))
        elif rev_g < _t(ctx, "growth_slow"):
            out.append(Finding("neutral", f"Revenue is essentially flat "
                               f"(**{f('revenue_growth_yoy_pct', rev_g)}** year "
                               "on year): whatever happens to profit from here "
                               "has to come from costs or from prices.", 1))

    if om is not None and "operating_margin_pct" not in ctx.ignore:
        gap = _peer_gap(r, peers, "operating_margin_pct")
        if om >= _t(ctx, "margin_good"):
            txt = (f"An operating margin of **{f('operating_margin_pct', om)}** "
                   "means the company keeps a wide slice of every sale after "
                   "the cost of running the business — usually the fingerprint "
                   "of pricing power.")
            if gap is not None and gap > 3:
                txt += f" It is **{gap:+.1f} pp** above the peer median."
            out.append(Finding("bull", txt, 2))
        elif om < _t(ctx, "margin_thin"):
            out.append(Finding("bear", f"The operating margin is thin at "
                               f"**{f('operating_margin_pct', om)}**: small "
                               "moves in costs or prices swing the profit a "
                               "long way, in both directions.", 2))

    direction, pp = _margin_trend(a, "ebit")
    if direction == "down" and pp is not None:
        out.append(Finding("bear", f"The operating margin has been **narrowing** "
                           f"across the filed years ({pp:+.1f} pp between the "
                           "first half of the window and the second). Direction "
                           "matters more than level: a margin sliding for "
                           "several years is usually a business losing pricing "
                           "power, not a bad quarter.", 3))
    elif direction == "up" and pp is not None:
        out.append(Finding("bull", f"The operating margin has been **widening** "
                           f"across the filed years ({pp:+.1f} pp): the company "
                           "is keeping more of each sale than it used to.", 2))

    if rev_g is not None and eps_g is not None and abs(rev_g) > 1:
        if eps_g > rev_g + 8:
            out.append(Finding("bull", f"Earnings per share are growing faster "
                               f"than revenue (**{f('eps_growth_yoy', eps_g)}** "
                               f"against **{f('revenue_growth_yoy_pct', rev_g)}**): "
                               "margins are widening, the share count is "
                               "shrinking, or both.", 2))
        elif eps_g < rev_g - 8:
            out.append(Finding("bear", f"Earnings per share are growing more "
                               f"slowly than revenue (**{f('eps_growth_yoy', eps_g)}** "
                               f"against **{f('revenue_growth_yoy_pct', rev_g)}**): "
                               "the extra sales are not reaching the individual "
                               "shareholder — costs, or a rising share count.", 2))

    if nm is not None and om is not None and om - nm > 12:
        out.append(Finding("flag", f"There is a wide gap between the operating "
                           f"margin (**{f('operating_margin_pct', om)}**) and "
                           f"the net margin (**{f('net_margin_pct', nm)}**): "
                           "interest and tax are eating a large part of what "
                           "the business itself earns.", 2))
    return out


def balance_findings(r, peers, a, ctx: SectorContext, f) -> list[Finding]:
    out: list[Finding] = []
    eq = _num(r.get("equity_ratio_pct"))
    nde = _num(r.get("net_debt_to_ebit"))
    net_debt = _num(r.get("net_debt"))
    roe, roic = _num(r.get("roe_pct")), _num(r.get("roic_pct"))

    if eq is not None and "equity_ratio_pct" not in ctx.ignore:
        if eq >= _t(ctx, "equity_good"):
            out.append(Finding("bull", f"**{f('equity_ratio_pct', eq)}** of the "
                               "balance sheet is owned rather than borrowed — a "
                               "company with this much equity gets to decide "
                               "for itself how to handle a bad year.", 2))
        elif eq < _t(ctx, "equity_low"):
            out.append(Finding("bear", f"Only **{f('equity_ratio_pct', eq)}** of "
                               "the assets is equity: the rest belongs to "
                               "creditors, and it is the creditors who set the "
                               "terms when results disappoint.", 3))

    if net_debt is not None and net_debt < 0:
        out.append(Finding("bull", f"The company holds **more cash than debt** "
                           f"(net debt {f('net_debt', net_debt)}). A net cash "
                           "position removes the refinancing question entirely "
                           "and pays for its own opportunities.", 3))
    elif nde is not None and "net_debt_to_ebit" not in ctx.ignore:
        if nde > _t(ctx, "net_debt_ebit_high"):
            out.append(Finding("bear", f"Net debt is **{f('net_debt_to_ebit', nde)}× "
                               "operating profit**: at this level the balance "
                               "sheet starts dictating strategy, because "
                               "refinancing has to be arranged whatever the "
                               "business is doing.", 3))
        elif nde < _t(ctx, "net_debt_ebit_safe"):
            out.append(Finding("bull", f"Net debt is only **{f('net_debt_to_ebit', nde)}× "
                               "operating profit** — barely a year of earnings "
                               "would clear it.", 2))

    direction, chg = _trend(a, "total_debt")
    eq_dir, eq_chg = _trend(a, "equity")
    if direction == "up" and chg is not None and (eq_dir != "up" or (eq_chg or 0) < chg - 10):
        out.append(Finding("bear", f"Debt has grown **{chg:+.0f}%** across the "
                           "filed years, faster than equity: leverage is "
                           "building up rather than being paid down.", 2))
    elif direction == "down" and chg is not None:
        out.append(Finding("bull", f"Debt is down **{chg:+.0f}%** across the "
                           "filed years — the balance sheet is being repaired "
                           "rather than stretched.", 2))

    if roe is not None and roic is not None:
        if roe - roic > 12:
            out.append(Finding("flag", f"ROE (**{f('roe_pct', roe)}**) is far "
                               f"above ROIC (**{f('roic_pct', roic)}**). The "
                               "difference is leverage: the return to "
                               "shareholders is being amplified by debt, and "
                               "leverage amplifies losses on the same terms.", 2))
        elif roic >= _t(ctx, "roic_good"):
            out.append(Finding("bull", f"ROIC of **{f('roic_pct', roic)}** is "
                               f"comfortably above what capital costs (~{COST_OF_CAPITAL:.0f}%): "
                               "growth here creates value rather than consuming "
                               "it.", 3))
    if roic is not None and roic < COST_OF_CAPITAL:
        out.append(Finding("bear", f"ROIC of **{f('roic_pct', roic)}** is below "
                           f"the rough cost of capital (~{COST_OF_CAPITAL:.0f}%): "
                           "on these returns, growing the business destroys "
                           "value instead of adding it.", 3))
    return out


def cash_findings(r, peers, a, ctx: SectorContext, f) -> list[Finding]:
    out: list[Finding] = []
    fcf = _num(r.get("fcf_ttm"))
    fcfm = _num(r.get("fcf_margin_pct"))
    conv = _num(r.get("fcf_conversion_pct"))
    capex = _num(r.get("capex_to_revenue_pct"))
    payout = _num(r.get("payout_ratio_pct"))
    dy = _num(r.get("dividend_yield"))

    if fcf is None:
        out.append(Finding("flag", "No free cash flow is available for this "
                           "company: it does not tag capital expenditure in a "
                           "comparable way — normal for banks and insurers. "
                           "Everything built on FCF is blank throughout the "
                           "app, and the cash question has to be answered from "
                           "the filings themselves.", 1))
        return out
    if fcf < 0:
        out.append(Finding("bear", f"Free cash flow is **negative** "
                           f"({f('fcf_ttm', fcf)}) over the last twelve months: "
                           "the business is consuming cash, and the difference "
                           "has to come from the balance sheet or from new "
                           "investors.", 3))
    elif fcfm is not None and "fcf_margin_pct" not in ctx.ignore:
        if fcfm >= _t(ctx, "fcf_margin_good"):
            out.append(Finding("bull", f"An FCF margin of **{f('fcf_margin_pct', fcfm)}** "
                               "means the business finances itself: growth, "
                               "dividends and buybacks come out of what it "
                               "produces, not out of borrowing.", 3))
        elif fcfm < _t(ctx, "fcf_margin_thin"):
            out.append(Finding("bear", f"An FCF margin of **{f('fcf_margin_pct', fcfm)}** "
                               "leaves almost nothing after the cost of staying "
                               "in business: expansion has to be paid for with "
                               "debt or with new shares.", 2))

    if conv is not None and "fcf_conversion_pct" not in ctx.ignore:
        if conv < _t(ctx, "conversion_poor"):
            out.append(Finding("bear", f"Only **{f('fcf_conversion_pct', conv)}** "
                               "of the accounting profit is turning into cash. "
                               "A lasting gap between the two is where "
                               "receivables, inventory and capitalised costs "
                               "hide — it is the single most useful warning on "
                               "this page.", 3))
        elif conv >= _t(ctx, "conversion_good"):
            out.append(Finding("bull", f"**{f('fcf_conversion_pct', conv)}** of "
                               "the reported profit arrives as cash: the "
                               "earnings are real, not an accounting timing "
                               "effect.", 2))

    if capex is not None and "capex_to_revenue_pct" not in ctx.ignore:
        if capex >= _t(ctx, "capex_high"):
            out.append(Finding("neutral", f"Capital spending absorbs "
                               f"**{f('capex_to_revenue_pct', capex)}** of "
                               "revenue — a capital-hungry business, where "
                               "growth costs cash before it produces any.", 1))
        elif capex < 4:
            out.append(Finding("bull", f"Capital spending takes only "
                               f"**{f('capex_to_revenue_pct', capex)}** of "
                               "revenue: an asset-light business can grow "
                               "without swallowing its own cash.", 2))

    direction, chg = _trend(a, "fcf")
    if direction == "down" and chg is not None:
        out.append(Finding("bear", f"Free cash flow has fallen **{chg:+.0f}%** "
                           "across the filed years. One heavy investment year "
                           "can do that innocently — several in a row usually "
                           "cannot.", 2))
    elif direction == "up" and chg is not None:
        out.append(Finding("bull", f"Free cash flow is up **{chg:+.0f}%** across "
                           "the filed years: the cash engine is getting "
                           "stronger, not just the reported profit.", 2))

    if payout is not None and dy and "payout_ratio_pct" not in ctx.ignore:
        if payout > 100:
            out.append(Finding("bear", f"The dividend costs **{f('payout_ratio_pct', payout)}** "
                               "of profit — more than the company earns. That "
                               "is funded from cash or debt, and it is the "
                               "state a business is in shortly before a cut.", 3))
        elif payout > _t(ctx, "payout_high"):
            out.append(Finding("flag", f"The payout ratio is **{f('payout_ratio_pct', payout)}**: "
                               "most of the profit leaves the company, which "
                               "leaves little room for both reinvestment and a "
                               "bad year.", 2))
        elif payout < 50:
            out.append(Finding("bull", f"The dividend costs only "
                               f"**{f('payout_ratio_pct', payout)}** of profit — "
                               "comfortable cover, and room to raise it.", 1))
    return out


def ratio_findings(r, peers, a, ctx: SectorContext, f) -> list[Finding]:
    out: list[Finding] = []
    pe = _num(r.get("pe_ratio"))
    peg = _num(r.get("peg_ratio"))
    fcfy = _num(r.get("fcf_yield_pct"))
    ey = _num(r.get("earnings_yield_pct"))
    pe_gap = _peer_gap(r, peers, "pe_ratio")

    if pe is not None and pe_gap is not None:
        if pe_gap <= -20:
            out.append(Finding("bull", f"At a P/E of **{f('pe_ratio', pe)}** the "
                               f"stock trades **{pe_gap:+.0f}%** against the "
                               "median of its own peer group — cheaper than the "
                               "companies it should be compared with, which is "
                               "the only comparison a P/E supports.", 2))
        elif pe_gap >= 25:
            out.append(Finding("bear", f"At a P/E of **{f('pe_ratio', pe)}** the "
                               f"stock trades **{pe_gap:+.0f}%** against its "
                               "peer group: the market is already paying for "
                               "something better than average here.", 2))

    if peg is not None:
        if peg < 1:
            out.append(Finding("bull", f"A PEG of **{f('peg_ratio', peg)}** says "
                               "the multiple is lower than the growth rate "
                               "behind it — Lynch's own definition of cheap for "
                               "what you get.", 2))
        elif peg > 2:
            out.append(Finding("bear", f"A PEG of **{f('peg_ratio', peg)}** says "
                               "you are paying more than two years of growth "
                               "for each point of multiple.", 2))

    if fcfy is not None and ey is not None:
        if fcfy > ey + 2:
            out.append(Finding("bull", f"The cash yield (**{f('fcf_yield_pct', fcfy)}**) "
                               f"is above the earnings yield (**{f('earnings_yield_pct', ey)}**): "
                               "the business produces more cash than its "
                               "reported profit suggests.", 2))
        elif ey > fcfy + 4:
            out.append(Finding("flag", f"The earnings yield (**{f('earnings_yield_pct', ey)}**) "
                               f"is well above the cash yield (**{f('fcf_yield_pct', fcfy)}**): "
                               "the profit looks better than the cash, and it "
                               "is the cash that pays dividends and debt.", 2))
    return out


def growth_findings(r, peers, a, ctx: SectorContext, f) -> list[Finding]:
    out: list[Finding] = []
    g_rev = _num(r.get("cagr_revenue_5y"))
    g_eps = _num(r.get("cagr_eps_5y"))
    g_fcf = _num(r.get("cagr_fcf_5y"))
    trend = _num(r.get("growth_5y_trend"))
    cagr = _num(r.get("growth_5y_cagr_raw"))
    vs_norm = _num(r.get("eps_vs_normalized_pct"))
    losses = _num(r.get("loss_episodes_5y"))

    if g_rev is not None:
        if g_rev >= _t(ctx, "growth_fast"):
            out.append(Finding("bull", f"Revenue has compounded at "
                               f"**{f('cagr_revenue_5y', g_rev)} a year** over "
                               "five years — the hardest number on the page to "
                               "massage, and the most reliable evidence that "
                               "the business is genuinely getting bigger.", 3))
        elif g_rev < 0:
            out.append(Finding("bear", f"Revenue has **shrunk** over five years "
                               f"(**{f('cagr_revenue_5y', g_rev)}** a year). "
                               "Every improvement in profit from here has to "
                               "come from costs, and cost cuts run out.", 3))
        elif g_rev < _t(ctx, "growth_slow"):
            out.append(Finding("neutral", f"Revenue has compounded at only "
                               f"**{f('cagr_revenue_5y', g_rev)} a year**: this "
                               "is a mature business, to be judged on cash and "
                               "dividends rather than on growth.", 1))

    if g_eps is not None and g_rev is not None:
        if g_eps > g_rev + 5:
            out.append(Finding("bull", f"Earnings per share have compounded "
                               f"faster than revenue (**{f('cagr_eps_5y', g_eps)}** "
                               f"against **{f('cagr_revenue_5y', g_rev)}**) — "
                               "margins widening, share count falling, or both, "
                               "sustained over five years.", 2))
        elif g_eps < g_rev - 5:
            out.append(Finding("bear", f"Earnings per share have grown more "
                               f"slowly than revenue over five years "
                               f"(**{f('cagr_eps_5y', g_eps)}** against "
                               f"**{f('cagr_revenue_5y', g_rev)}**): growth is "
                               "not reaching the individual share.", 2))

    if g_fcf is not None and g_eps is not None and g_eps - g_fcf > 10:
        out.append(Finding("flag", f"Profit has compounded much faster than "
                           f"cash (**{f('cagr_eps_5y', g_eps)}** against "
                           f"**{f('cagr_fcf_5y', g_fcf)}** for free cash flow). "
                           "Over five years that gap is the classic early "
                           "warning: it is cash, not profit, that eventually "
                           "has to show up.", 3))
    elif g_fcf is not None and g_fcf > 10:
        out.append(Finding("bull", f"Free cash flow has compounded at "
                           f"**{f('cagr_fcf_5y', g_fcf)} a year** — the growth "
                           "is arriving as money, not only as accounting "
                           "profit.", 3))

    if trend is not None and cagr is not None and abs(trend - cagr) > 15:
        out.append(Finding("flag", f"The five-year trend (**{trend:+.0f}%**) and "
                           f"the endpoint CAGR (**{cagr:+.0f}%**) disagree by "
                           f"{abs(trend - cagr):.0f} points, which means one of "
                           "the two endpoints is not representative — usually a "
                           "depressed starting quarter that flatters the CAGR.", 2))

    if ctx.cyclical or str(r.get("lynch_category", "")).startswith("Cyclical"):
        out.append(Finding("flag", "This is a **cyclical** business: a five-year "
                           "compound rate measured from a trough to a peak "
                           "describes the cycle, not the company. Read the "
                           "growth above against where the sector is in its "
                           "cycle, and prefer normalized earnings for the "
                           "valuation.", 2))
    if vs_norm is not None and abs(vs_norm) > 40:
        direction = "above" if vs_norm > 0 else "below"
        tone = "bear" if vs_norm > 0 else "bull"
        out.append(Finding(tone, f"Current earnings sit **{vs_norm:+.0f}%** "
                           f"{direction} their own three-year median. Growth "
                           "measured from an unusual year is growth measured "
                           "against an accident — the normalized fair value on "
                           "the Valuation tab is the more sober reading.", 2))
    if losses and losses >= 2:
        out.append(Finding("bear", f"There have been **{int(losses)} distinct "
                           "loss episodes** in the last five years. A growth "
                           "rate computed across losses describes a recovery, "
                           "not a trend.", 2))
    return out


SECTIONS = {
    "income": income_findings,
    "balance": balance_findings,
    "cash": cash_findings,
    "ratios": ratio_findings,
    "growth": growth_findings,
}

TONE_ICON = {"bull": "🟢", "bear": "🔴", "flag": "🟡", "neutral": "⚪"}


def verdict(findings: list[Finding]) -> tuple[str, str, str]:
    """
    Il saldo dei rilievi: (etichetta, icona, spiegazione della scala).

    E' una SOMMA PESATA, e va detto: non e' un giudizio, e' il conteggio di
    cio' che le cifre di questa pagina sostengono a favore e contro. Un modello
    che non sa nulla del prodotto, dei clienti, della concorrenza o del prezzo
    pagato non puo' produrre altro, e pretendere di piu' sarebbe la parte
    disonesta.
    """
    bull = sum(f.weight for f in findings if f.tone == "bull")
    bear = sum(f.weight for f in findings if f.tone == "bear")
    total = bull + bear
    if not total:
        return "Inconclusive", "⚪", "not enough comparable figures"
    score = (bull - bear) / total
    if score >= 0.5:
        return "Bullish", "🟢", f"{bull} weighted points for, {bear} against"
    if score >= 0.15:
        return "Leaning bullish", "🟢", f"{bull} for, {bear} against"
    if score > -0.15:
        return "Mixed", "🟡", f"{bull} for, {bear} against — evenly balanced"
    if score > -0.5:
        return "Leaning bearish", "🔴", f"{bull} for, {bear} against"
    return "Bearish", "🔴", f"{bull} for, {bear} against"


def render(section: str, r: pd.Series, peers: pd.Series | None,
           a: pd.DataFrame | None, fmt) -> list[Finding]:
    """
    Il riquadro di commento. `fmt(key, value)` e' la stessa funzione di
    formattazione che disegna i riquadri sopra: e' quello che garantisce che un
    numero nel testo e lo stesso numero nella scheda non possano divergere.
    """
    ctx = _ctx(r.get("sector"))
    findings = SECTIONS[section](r, peers, a, ctx, fmt)
    if not findings:
        st.info("Not enough comparable figures for this company to say "
                "anything worth reading here.", icon="🤍")
        return findings

    with st.container(border=True):
        st.markdown("**What these numbers say**")
        for fnd in findings:
            # Lo spazio unificatore dopo l'icona: senza, il markdown accosta il
            # pallino alla prima parola e la riga si legge come un refuso.
            st.markdown(f"{TONE_ICON[fnd.tone]}&nbsp; {fnd.text}")
        if ctx.note:
            st.caption(f"**Sector context** — {ctx.note}")
        st.caption(
            "Every figure quoted above is the same variable shown in the cards "
            "at the top of this section, formatted by the same function: the "
            "commentary is arithmetic on this page's own numbers, not an "
            "opinion about the company.")
    return findings


def render_assessment(r: pd.Series, peers: pd.Series | None,
                      a: pd.DataFrame | None, fmt) -> None:
    """
    Il saldo finale, in fondo alla scheda della crescita: cosa sostengono i
    numeri di TUTTA la pagina, messi insieme.

    Raccoglie i rilievi delle cinque sezioni invece dei soli rilievi sulla
    crescita: un giudizio costruito su una sola sezione direbbe quanto e'
    cresciuta l'azienda, non se i conti la sostengono.
    """
    ctx = _ctx(r.get("sector"))
    all_findings: list[Finding] = []
    for fn in SECTIONS.values():
        try:
            all_findings.extend(fn(r, peers, a, ctx, fmt))
        except Exception:                                    # noqa: BLE001
            continue

    label, icon, why = verdict(all_findings)
    bulls = [f for f in all_findings if f.tone == "bull"]
    bears = [f for f in all_findings if f.tone == "bear"]
    flags = [f for f in all_findings if f.tone == "flag"]

    with st.container(border=True):
        st.markdown(f"### {icon} {label}")
        st.caption(f"Mechanical balance of everything on this ticker's pages · {why}")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**What argues for it**")
            if bulls:
                for fnd in sorted(bulls, key=lambda x: -x.weight):
                    st.markdown(f"- {fnd.text}")
            else:
                st.markdown("_Nothing in the figures argues in favour._")
        with c2:
            st.markdown("**What argues against it**")
            if bears:
                for fnd in sorted(bears, key=lambda x: -x.weight):
                    st.markdown(f"- {fnd.text}")
            else:
                st.markdown("_Nothing in the figures argues against._")

        if flags:
            st.markdown("**Read with care**")
            for fnd in flags:
                st.markdown(f"- {fnd.text}")

        st.warning(
            "**This is a weighted count, not a recommendation.** It adds up "
            "what this company's own filed figures support and contradict, and "
            "it knows nothing about the product, the customers, the "
            "competition, the management or anything that happened after the "
            "last filing. It is not investment advice, and a screener cannot "
            "give any.", icon="⚠️")
