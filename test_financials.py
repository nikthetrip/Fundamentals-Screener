#!/usr/bin/env python3
"""
test_financials.py — Test offline dell'estrazione di bilancio da EDGAR.

Copre i punti dove un errore produrrebbe numeri plausibili e sbagliati, cioe'
il tipo di errore che nessuno nota:

  1. I flussi di cassa in EDGAR sono CUMULATI da inizio esercizio. Sommarne
     quattro darebbe un "TTM" di circa 2,5 volte il valore vero.
  2. Il free cash flow va allineato per data fra flusso operativo e capex:
     sottrarre periodi diversi produce un numero che sembra normale.
  3. Il CAGR da una base negativa non esiste e non va inventato.
  4. La volatilita' degli utili va misurata anno su anno anche quando la serie
     e' annuale, non a quattro passi indietro.

Non serve rete: i dati sono sintetici, costruiti con la stessa forma dei
companyfacts della SEC.

Uso:  python test_financials.py
"""

from __future__ import annotations
import sys
from datetime import date

from edgar_logic import (
    build_quarterly_flow, rolling_ttm, combine_series,
    extract_flow_facts, extract_instant_series, extract_financials,
    compute_ratios, compute_cagrs, annual_financial_table,
    series_cagr, yoy_change, earnings_volatility, latest_value, safe_div,
    build_fair_value_rows, asof_eps_dated,
)

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✓' if ok else '✗'} {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        failures.append(f"{name}: {detail}")


def section(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def near(a, b, tol=1e-6) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


# ---------------------------------------------------------------------------
def _duration_fact(start: str, end: str, val: float, filed: str = "2026-01-01") -> dict:
    return {"start": start, "end": end, "val": val, "filed": filed, "form": "10-Q"}


def _cf(concept_rows: dict) -> dict:
    """Costruisce un companyfacts sintetico da {concetto: [righe]}."""
    return {"facts": {"us-gaap": {c: {"units": {"USD": rows}}
                                  for c, rows in concept_rows.items()}}}


section("1 — Flussi di cassa cumulati (year-to-date): il caso che gonfia il TTM")
# Un esercizio solare come lo deposita davvero una societa': Q1 discreto,
# poi semestre, nove mesi e anno, tutti con lo stesso 'start'.
ytd = [
    _duration_fact("2025-01-01", "2025-03-31", 100),
    _duration_fact("2025-01-01", "2025-06-30", 250),
    _duration_fact("2025-01-01", "2025-09-30", 430),
    _duration_fact("2025-01-01", "2025-12-31", 640),
]
facts = extract_flow_facts(_cf({"NetCashProvidedByUsedInOperatingActivities": ytd}),
                           ("NetCashProvidedByUsedInOperatingActivities",))
q = build_quarterly_flow(facts)
print(f"  trimestri discreti ricavati: {[(str(d), v) for d, v in q]}")
check("Q1 preso direttamente", near(dict(q).get(date(2025, 3, 31)), 100))
check("Q2 = semestre - Q1", near(dict(q).get(date(2025, 6, 30)), 150))
check("Q3 = nove mesi - semestre", near(dict(q).get(date(2025, 9, 30)), 180))
check("Q4 = anno - nove mesi", near(dict(q).get(date(2025, 12, 31)), 210))

ttm = rolling_ttm(q)
check("TTM = totale dell'esercizio, non la somma dei cumulati",
      near(ttm[-1][1], 640),
      f"TTM={ttm[-1][1]} (sommare i cumulati darebbe {sum(f['val'] for f in ytd)})")

section("2 — Free cash flow: allineamento per data fra flusso operativo e capex")
ocf = [(date(2025, 3, 31), 100.0), (date(2025, 6, 30), 150.0), (date(2025, 9, 30), 180.0)]
capex = [(date(2025, 3, 31), 30.0), (date(2025, 9, 30), 40.0)]   # manca il Q2
fcf = combine_series(ocf, capex, lambda a, b: a - b)
check("solo le date presenti in entrambe le serie", len(fcf) == 2, str(fcf))
check("nessuna sottrazione fra periodi diversi",
      fcf == [(date(2025, 3, 31), 70.0), (date(2025, 9, 30), 140.0)], str(fcf))

section("3 — Fatti di istante (stato patrimoniale) contro fatti di durata")
cf_mixed = _cf({
    "Assets": [
        {"end": "2025-12-31", "val": 1000, "filed": "2026-02-01"},
        {"end": "2025-12-31", "val": 990, "filed": "2025-12-31"},   # deposito piu' vecchio
        {"end": "2024-12-31", "val": 900, "filed": "2025-02-01"},
    ],
    "Revenues": [_duration_fact("2025-01-01", "2025-12-31", 500)],
})
assets = extract_instant_series(cf_mixed, ("Assets",))
check("serie di istante ordinata", [str(d) for d, _ in assets] ==
      ["2024-12-31", "2025-12-31"], str(assets))
check("a parita' di data vince il deposito piu' recente",
      near(dict(assets)[date(2025, 12, 31)], 1000))
rev_instants = extract_instant_series(cf_mixed, ("Revenues",))
check("un fatto di durata non finisce fra gli istanti", rev_instants == [],
      str(rev_instants))

section("4 — latest_value: un bilancio vecchio non descrive 'adesso'")
old = [(date(2014, 6, 30), 269.9)]
check("scartato oltre la soglia di eta'",
      latest_value(old, 400, today=date(2026, 7, 29)) is None)
check("tenuto quando la soglia e' disattivata",
      latest_value(old, None, today=date(2026, 7, 29))[1] == 269.9)

section("5 — CAGR: nessun tasso composto da una base negativa o nulla")
neg_start = [(date(2021, 12, 31), -5.0), (date(2026, 6, 30), 10.0)]
check("base negativa -> None", series_cagr(neg_start, 5) is None)
zero_start = [(date(2021, 12, 31), 0.0), (date(2026, 6, 30), 10.0)]
check("base nulla -> None", series_cagr(zero_start, 5) is None)
short = [(date(2025, 12, 31), 10.0), (date(2026, 6, 30), 12.0)]
check("storia troppo corta per la finestra -> None", series_cagr(short, 5) is None)
good = [(date(2021, 6, 30), 100.0), (date(2026, 6, 30), 200.0)]
cagr = series_cagr(good, 5)
check("raddoppio in cinque anni ≈ 14,9% l'anno", near(cagr, 14.87, 0.05), f"{cagr:.2f}%")

section("6 — Variazione YoY confrontata per data, non per posizione")
irregular = [
    (date(2024, 3, 31), 100.0),
    (date(2024, 6, 30), 110.0),
    # buco: manca il Q3 2024
    (date(2025, 3, 31), 120.0),
    (date(2025, 6, 30), 132.0),
]
check("confronta con il punto a un anno di distanza",
      near(yoy_change(irregular), 20.0), str(yoy_change(irregular)))
too_far = [(date(2023, 1, 31), 100.0), (date(2026, 6, 30), 200.0)]
check("rifiuta il confronto se il punto piu' vicino e' lontano da un anno",
      yoy_change(too_far) is None)

section("7 — Volatilita' degli utili su una serie ANNUALE")
# Crescita costante del 10% l'anno: e' l'opposto di una societa' ciclica.
steady = [(date(2019 + i, 12, 31), 100 * (1.1 ** i)) for i in range(7)]
vol = earnings_volatility(steady)
check("crescita regolare -> volatilita' quasi nulla", vol is not None and vol < 1.0,
      f"vol={vol:.3f}" if vol is not None else "None")
erratic = [(date(2019, 12, 31), 100), (date(2020, 12, 31), 20),
           (date(2021, 12, 31), 140), (date(2022, 12, 31), 30),
           (date(2023, 12, 31), 160), (date(2024, 12, 31), 25),
           (date(2025, 12, 31), 180)]
vol_e = earnings_volatility(erratic)
check("utili erratici -> volatilita' alta", vol_e is not None and vol_e > 40,
      f"vol={vol_e:.0f}" if vol_e is not None else "None")

section("8 — Quadro completo su un bilancio sintetico coerente")
years = []
for i, y in enumerate(range(2021, 2026)):
    rev = 1000 * (1.1 ** i)
    years.append((y, rev))
cf_full = _cf({
    "Revenues": [_duration_fact(f"{y}-01-01", f"{y}-12-31", rev) for y, rev in years],
    "NetIncomeLoss": [_duration_fact(f"{y}-01-01", f"{y}-12-31", rev * 0.1)
                      for y, rev in years],
    "OperatingIncomeLoss": [_duration_fact(f"{y}-01-01", f"{y}-12-31", rev * 0.15)
                            for y, rev in years],
    "NetCashProvidedByUsedInOperatingActivities": [
        _duration_fact(f"{y}-01-01", f"{y}-12-31", rev * 0.2) for y, rev in years],
    "PaymentsToAcquirePropertyPlantAndEquipment": [
        _duration_fact(f"{y}-01-01", f"{y}-12-31", rev * 0.05) for y, rev in years],
    "Assets": [{"end": f"{y}-12-31", "val": rev * 2, "filed": f"{y + 1}-02-01"}
               for y, rev in years],
    "StockholdersEquity": [{"end": f"{y}-12-31", "val": rev * 1.0,
                            "filed": f"{y + 1}-02-01"} for y, rev in years],
    "LongTermDebtNoncurrent": [{"end": f"{y}-12-31", "val": rev * 0.4,
                                "filed": f"{y + 1}-02-01"} for y, rev in years],
})
fin = extract_financials(cf_full)
check("ricavi annuali estratti", len(fin["revenue_fy"]) == 5, str(len(fin["revenue_fy"])))
check("FCF FY = flusso operativo - capex",
      near(fin["fcf_fy"][-1][1], years[-1][1] * 0.15, 1e-6),
      f"{fin['fcf_fy'][-1][1]:.1f} atteso {years[-1][1] * 0.15:.1f}")

ratios = compute_ratios(fin, market_cap=years[-1][1] * 3, shares=100,
                        today=date(2026, 3, 1))
check("equity ratio = patrimonio / attivo = 50%", near(ratios["equity_ratio_pct"], 50.0, 1e-6),
      f"{ratios['equity_ratio_pct']:.1f}%")
check("earning power = EBIT / attivo = 7,5%", near(ratios["earning_power_pct"], 7.5, 1e-6),
      f"{ratios['earning_power_pct']:.1f}%")
check("net margin = 10%", near(ratios["net_margin_pct"], 10.0, 1e-6))
check("debito / patrimonio = 0,4", near(ratios["debt_to_equity"], 0.4, 1e-9))
# ROE su patrimonio MEDIO: utile 10% dei ricavi, patrimonio medio fra i due anni
expected_roe = (years[-1][1] * 0.1) / ((years[-1][1] + years[-2][1]) / 2) * 100
check("ROE calcolato sul patrimonio medio, non su quello finale",
      near(ratios["roe_pct"], expected_roe, 1e-6),
      f"{ratios['roe_pct']:.2f}% atteso {expected_roe:.2f}%")

cagrs = compute_cagrs(fin, [(date(y, 12, 31), rev / 1000) for y, rev in years])
check("CAGR ricavi a 4 anni di storia ≈ 10%", near(cagrs["cagr_revenue_5y"], 10.0, 0.3),
      f"{cagrs['cagr_revenue_5y']:.2f}%")
check("CAGR EPS ≈ 10%", near(cagrs["cagr_eps_5y"], 10.0, 0.3))

table = annual_financial_table(fin, [(date(y, 12, 31), rev / 1000) for y, rev in years])
check("tabella annuale con tutte le voci agganciate alla chiusura",
      len(table) == 5 and all(r["fcf"] is not None and r["equity"] is not None
                              for r in table))

section("9 — safe_div non esplode e non produce infiniti")
check("divisione per zero -> None", safe_div(1.0, 0.0) is None)
check("numeratore mancante -> None", safe_div(None, 5.0) is None)

section("10 — un buco nella serie EPS non si riempie col dato vecchio")
# Caso GoDaddy: EDGAR non espone EPS fra il 2017 e il 2022. Senza limite, l'EPS
# del 2016 veniva attribuito ai prezzi del 2025.
eps_with_hole = [(date(2016, 12, 31), -0.23), (date(2025, 12, 31), 6.22)]
prices = [(date(2016, 12, 31), 35.0), (date(2017, 6, 30), 42.0),
          (date(2020, 6, 30), 74.0), (date(2025, 12, 31), 190.0),
          (date(2026, 3, 31), 200.0)]
rows = build_fair_value_rows(prices, eps_with_hole, [15])
got = {r["date"]: r["eps"] for r in rows}
check("il prezzo alla data del deposito e' coperto", got.get(date(2016, 12, 31)) == -0.23)
check("sei mesi dopo si porta ancora avanti (deposito annuale)",
      got.get(date(2017, 6, 30)) == -0.23)
check("nel buco di nove anni NON si inventa un utile",
      date(2020, 6, 30) not in got, str(sorted(str(d) for d in got)))
check("alla ripresa dei depositi si usa il dato nuovo",
      got.get(date(2025, 12, 31)) == 6.22)
check("tre mesi dopo l'ultimo deposito si porta avanti",
      got.get(date(2026, 3, 31)) == 6.22)
check("asof_eps_dated riporta anche la data del dato usato",
      asof_eps_dated(eps_with_hole, date(2020, 6, 30)) == (date(2016, 12, 31), -0.23))

section("11 — il rendimento da dividendo si ricostruisce dai pagamenti")
# Il campo dividendRate del provider sbaglia proprio dove conta: dopo un taglio
# resta la somma dei quattro stacchi vecchi, e con una cedola straordinaria la
# ingloba come se tornasse ogni anno. Qui si verifica il conto fatto in casa.
from datetime import timedelta                                      # noqa: E402
from data_sources import dividend_profile                           # noqa: E402


TODAY = date(2026, 8, 1)


def _pays(amounts, last=date(2026, 7, 15), step=91):
    """Pagamenti a ritroso dall'ultimo, cosi' la serie finisce sempre 'oggi'."""
    n = len(amounts)
    return [(last - timedelta(days=(n - 1 - i) * step), a)
            for i, a in enumerate(amounts)]


p = dividend_profile(_pays([0.485, 0.485, 0.51, 0.51, 0.51, 0.51, 0.53, 0.53]),
                     88.49, asof=TODAY)
check("cedola trimestrale stabile: annualizza l'ultimo stacco",
      abs(p["rate"] - 2.12) < 1e-9 and p["frequency"] == 4, str(p))
check("e il rendimento e' quello del passo corrente",
      abs(p["yield_pct"] - 2.396) < 0.01, str(p["yield_pct"]))

cut = dividend_profile(_pays([1.75, 1.75, 1.75, 1.75, 1.75, 0.90, 0.90, 0.90]),
                       38.07, asof=TODAY)
check("DOPO UN TAGLIO usa la cedola nuova, non la somma delle vecchie",
      abs(cut["rate"] - 3.60) < 1e-9, str(cut["rate"]))
check("il rendimento scende di conseguenza (9.5%, non 11.7%)",
      abs(cut["yield_pct"] - 9.456) < 0.01, str(cut["yield_pct"]))

spec = dividend_profile(
    _pays([0.10, 4.60, 0.10, 0.10, 0.10, 13.60, 0.10, 0.10]), 213.28, asof=TODAY)
check("la cedola STRAORDINARIA non entra nel rendimento ordinario",
      abs(spec["rate"] - 0.40) < 1e-9, str(spec["rate"]))
check("ma viene dichiarata, e il totale incassato la comprende",
      spec["has_special"] and abs(spec["ttm"] - 13.90) < 1e-9, str(spec))

# American Financial Group: 0,88 $ a trimestre con straordinari infilati in
# mezzo. Contando anche quelli, la distanza mediana fra pagamenti crolla e la
# cadenza sembra mensile: 0,88 × 12 dava un rendimento del 7,5% invece del 2,5%.
afg = dividend_profile(
    [(date(2025, 1, 14), 0.80), (date(2025, 3, 17), 2.00),
     (date(2025, 4, 15), 0.80), (date(2025, 7, 15), 0.80),
     (date(2025, 10, 15), 0.88), (date(2025, 11, 17), 2.00),
     (date(2026, 1, 15), 0.88), (date(2026, 2, 13), 1.50),
     (date(2026, 4, 15), 0.88), (date(2026, 7, 15), 0.88)], 141.70, asof=TODAY)
check("la cadenza si misura sulle SOLE cedole ordinarie",
      afg["frequency"] == 4 and abs(afg["rate"] - 3.52) < 1e-9, str(afg))
check("gli straordinari fra un trimestre e l'altro non la rendono mensile",
      abs(afg["yield_pct"] - 2.484) < 0.01, str(afg["yield_pct"]))

monthly = dividend_profile(
    [(date(2025, 8, 15) + timedelta(days=i * 30), 0.271) for i in range(12)],
    64.43, asof=TODAY)
check("cadenza mensile riconosciuta (12 stacchi, non 4)",
      monthly["frequency"] == 12 and abs(monthly["rate"] - 3.252) < 1e-9, str(monthly))

yearly = dividend_profile([(date(2024, 6, 1), 2.0), (date(2025, 6, 1), 2.2),
                           (date(2026, 6, 1), 2.4)], 100.0, asof=TODAY)
check("cadenza annuale: nessun raddoppio del totale a dodici mesi",
      yearly["frequency"] == 1 and abs(yearly["ttm"] - 2.4) < 1e-9, str(yearly))

# Arch Capital: un solo stacco straordinario, mai piu' nulla. Annualizzato
# dava il 19,8% a una societa' che non paga dividendi.
once = dividend_profile([(date(2024, 11, 18), 5.00)], 101.20, asof=TODAY)
check("UN SOLO pagamento non fa una cedola: nessun rendimento",
      once["rate"] is None and once["yield_pct"] is None, str(once))

# American Airlines: sospeso a febbraio 2020. Adobe: smesso nel 2005.
aal = dividend_profile(_pays([0.1] * 8, last=date(2020, 2, 4)), 12.0, asof=TODAY)
check("un dividendo SOSPESO non produce un rendimento",
      aal["rate"] is None and aal["discontinued"], str(aal))
check("e nemmeno un totale a dodici mesi",
      not aal["ttm"], str(aal["ttm"]))
adbe = dividend_profile(_pays([0.0065] * 12, last=date(2005, 3, 24)), 247.90,
                        asof=TODAY)
check("vale anche per chi ha smesso vent'anni fa",
      adbe["yield_pct"] is None and adbe["discontinued"], str(adbe))
late = dividend_profile(_pays([0.5] * 6, last=date(2026, 5, 20)), 40.0, asof=TODAY)
check("un trimestre in ritardo di poche settimane NON e' una sospensione",
      late["rate"] == 2.0 and not late["discontinued"], str(late))

# STAG Industrial: da mensile 0,124 $ a trimestrale 0,388 $. Guardando tutta la
# storia il cambio di cadenza sembra una raffica di cedole straordinarie, e la
# societa' risultava aver sospeso il dividendo.
stag = dividend_profile(
    [(date(2025, 9, 30), 0.124), (date(2025, 10, 31), 0.124),
     (date(2025, 11, 28), 0.124), (date(2025, 12, 31), 0.124),
     (date(2026, 3, 31), 0.388), (date(2026, 6, 30), 0.388)], 38.54, asof=TODAY)
check("un CAMBIO DI CADENZA non e' una sospensione",
      stag["frequency"] == 4 and not stag["discontinued"], str(stag))
check("e il rendimento resta quello di prima (l'importo annuo non cambia)",
      abs(stag["yield_pct"] - 4.03) < 0.05, str(stag["yield_pct"]))

# Nvidia: 0,01 $ a trimestre e un pagamento isolato da 0,25 $. Un aumento del
# 2.400% non esiste: e' uno straordinario.
nvda = dividend_profile(
    [(date(2025, 3, 12), 0.01), (date(2025, 6, 11), 0.01),
     (date(2025, 9, 11), 0.01), (date(2025, 12, 4), 0.01),
     (date(2026, 3, 11), 0.01), (date(2026, 6, 4), 0.25)], 195.04, asof=TODAY)
check("un balzo di 25 volte e' uno straordinario, non un aumento",
      abs(nvda["rate"] - 0.04) < 1e-9 and nvda["has_special"], str(nvda))

# W. R. Berkley: cedola ordinaria di 0,09 $ e uno straordinario ogni due
# trimestri. Con la mediana semplice il livello "ordinario" finiva a meta'
# strada e il rendimento saliva a 3,3% invece di 0,5%.
wrb = dividend_profile(
    [(date(2025, 3, 3), 0.08), (date(2025, 6, 23), 0.59),
     (date(2025, 9, 22), 0.09), (date(2025, 12, 15), 1.09),
     (date(2026, 2, 23), 0.09), (date(2026, 6, 23), 0.60)], 73.38, asof=TODAY)
check("straordinari frequenti non alzano il livello ordinario",
      wrb["rate"] is not None and wrb["rate"] < 0.5, str(wrb))

# Crown Holdings: due trimestri distanti 58 giorni. Bastano a far sembrare
# mensile una cedola trimestrale, e a triplicare il rendimento — ma nell'anno
# la societa' ha pagato quattro volte, non dodici.
cck = dividend_profile(
    [(date(2025, 3, 18), 0.26), (date(2025, 5, 15), 0.26),
     (date(2025, 8, 7), 0.26), (date(2025, 11, 6), 0.26),
     (date(2026, 3, 17), 0.35), (date(2026, 5, 14), 0.35)], 126.0, asof=TODAY)
check("la cadenza non puo' promettere piu' stacchi di quanti ne siano avvenuti",
      cck["frequency"] == 4 and abs(cck["rate"] - 1.40) < 1e-9, str(cck))

# Dentsply: cedola trimestrale interrotta a fine 2025. I due stacchi rimasti
# nella finestra non fanno di lei una societa' a cedola semestrale.
xray = dividend_profile(
    [(date(2025, 3, 28), 0.16), (date(2025, 6, 27), 0.16),
     (date(2025, 9, 26), 0.16), (date(2025, 12, 26), 0.16)], 13.74, asof=TODAY)
check("una serie che si e' fermata resta trimestrale, non diventa semestrale",
      xray["frequency"] == 4, str(xray))

# CNH: dividendo annuale sceso da 0,47 a 0,10 $ in tre anni, con un anno
# saltato nel 2020. Filtrando gli importi "alti" sparivano gli anni di mezzo e
# la cadenza annuale non veniva piu' riconosciuta.
cnh = dividend_profile(
    [(date(2019, 4, 23), 0.1767), (date(2021, 4, 19), 0.1149),
     (date(2022, 4, 19), 0.302), (date(2023, 4, 24), 0.396),
     (date(2024, 5, 10), 0.47), (date(2025, 5, 21), 0.25),
     (date(2026, 5, 21), 0.10)], 10.32, asof=TODAY)
check("un dividendo annuale in calo resta un dividendo annuale",
      cnh["frequency"] == 1 and abs(cnh["rate"] - 0.10) < 1e-9, str(cnh))

# Vornado: 0,74 $ ogni dicembre, due volte. Due pagamenti identici a un anno
# esatto di distanza sono una politica; due importi diversi no.
vno = dividend_profile([(date(2023, 12, 14), 0.30), (date(2024, 12, 16), 0.74),
                        (date(2025, 12, 18), 0.74)], 40.15, asof=TODAY)
check("due stacchi annuali IDENTICI bastano a stabilire la cadenza",
      vno["frequency"] == 1 and abs(vno["rate"] - 0.74) < 1e-9, str(vno))

# Rocket Companies: tre elargizioni in cinque anni. Non e' un dividendo annuale.
rkt = dividend_profile([(date(2021, 3, 8), 1.11), (date(2022, 3, 7), 1.01),
                        (date(2025, 3, 20), 0.80)], 13.26, asof=TODAY)
check("pagamenti sporadici non fanno una cadenza",
      rkt["rate"] is None, str(rkt))

# Un aumento vero, invece, si accetta subito.
raise_ = dividend_profile(_pays([0.50, 0.50, 0.50, 0.75]), 40.0, asof=TODAY)
check("un aumento contenuto vale dal primo stacco",
      abs(raise_["rate"] - 3.0) < 1e-9, str(raise_))

check("chi non paga dividendi non ha un rendimento pari a zero, ma nessuno",
      dividend_profile([], 10.0)["yield_pct"] is None)
check("senza prezzo resta il dividendo per azione, senza rendimento",
      dividend_profile(_pays([0.5] * 4), None, asof=TODAY)["rate"] == 2.0
      and dividend_profile(_pays([0.5] * 4), None, asof=TODAY)["yield_pct"] is None)

print("\n" + "=" * 72)
if failures:
    print(f"❌ {len(failures)} TEST FALLITI")
    for f in failures:
        print("   -", f)
    sys.exit(1)
print("✅ TUTTI I TEST PASSATI — estrazione e indicatori di bilancio corretti")
print("=" * 72)
