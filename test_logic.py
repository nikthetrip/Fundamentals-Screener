"""
Test offline della logica in edgar_logic.py con dati sintetici.
Non tocca la rete: verifica solo la matematica (TTM, as-of merge, fair value).
"""
from datetime import date
from edgar_logic import (
    extract_eps_facts, build_quarterly_eps, build_ttm_eps,
    build_fair_value_rows, asof_eps, ttm_growth_yoy,
)


def make_fact(start, end, val, fp, form, frame=None):
    return {"start": start, "end": end, "val": val, "fy": int(end[:4]),
            "fp": fp, "form": form, "frame": frame}


# Azienda sintetica: EPS trimestrale che cresce ~1.0 -> 1.6 su 2 anni.
# Simuliamo anche il caso "Q4 mancante" (solo FY riportato) per il 2021.
synthetic = {
    "cik": 999999,
    "entityName": "TestCo",
    "facts": {"us-gaap": {"EarningsPerShareDiluted": {"units": {"USD/shares": [
        # 2021: Q1,Q2,Q3 discreti + FY (Q4 assente, va ricostruito)
        make_fact("2021-01-01", "2021-03-31", 1.00, "Q1", "10-Q", "CY2021Q1"),
        make_fact("2021-04-01", "2021-06-30", 1.10, "Q2", "10-Q", "CY2021Q2"),
        make_fact("2021-07-01", "2021-09-30", 1.20, "Q3", "10-Q", "CY2021Q3"),
        make_fact("2021-01-01", "2021-12-31", 4.60, "FY", "10-K", "CY2021"),  # FY -> Q4=1.30
        # 2022: tutti e 4 i trimestri discreti
        make_fact("2022-01-01", "2022-03-31", 1.30, "Q1", "10-Q", "CY2022Q1"),
        make_fact("2022-04-01", "2022-06-30", 1.40, "Q2", "10-Q", "CY2022Q2"),
        make_fact("2022-07-01", "2022-09-30", 1.50, "Q3", "10-Q", "CY2022Q3"),
        make_fact("2022-10-01", "2022-12-31", 1.60, "Q4", "10-Q", "CY2022Q4"),
        # rumore: un ri-statement dello stesso Q2 2022 senza frame (deve essere ignorato)
        make_fact("2022-04-01", "2022-06-30", 1.42, "Q2", "10-Q/A", None),
    ]}}}},
}

print("=" * 60)
print("TEST 1 — extract + ricostruzione trimestri (incl. Q4 da FY)")
print("=" * 60)
facts = extract_eps_facts(synthetic)
print(f"Fatti estratti: {len(facts)}")
quarters = build_quarterly_eps(facts)
for d, v in quarters:
    print(f"  {d}  EPS_Q = {v:.2f}")

# Verifica: Q4 2021 ricostruito = 4.60 - (1.00+1.10+1.20) = 1.30
q4_2021 = dict(quarters).get(date(2021, 12, 31))
assert q4_2021 is not None, "Q4 2021 non ricostruito!"
assert abs(q4_2021 - 1.30) < 1e-9, f"Q4 2021 sbagliato: {q4_2021}"
print(f"\n  ✓ Q4 2021 ricostruito da FY: {q4_2021:.2f} (atteso 1.30)")

# Verifica: il ri-statement 1.42 senza frame NON deve sostituire 1.40
q2_2022 = dict(quarters).get(date(2022, 6, 30))
assert abs(q2_2022 - 1.40) < 1e-9, f"dedupe frame fallito: {q2_2022}"
print(f"  ✓ Dedupe: Q2 2022 = {q2_2022:.2f} (tenuto quello con frame, ignorato 1.42)")

print("\n" + "=" * 60)
print("TEST 2 — TTM rolling di 4 trimestri")
print("=" * 60)
ttm, method = build_ttm_eps(facts)
print(f"Metodo: {method}")
for d, v in ttm:
    print(f"  {d}  TTM_EPS = {v:.2f}")

# Primo TTM completo = Q1..Q4 2021 = 1.00+1.10+1.20+1.30 = 4.60
first_ttm = ttm[0]
assert abs(first_ttm[1] - 4.60) < 1e-9, f"primo TTM sbagliato: {first_ttm}"
print(f"\n  ✓ Primo TTM (fine 2021) = {first_ttm[1]:.2f} (atteso 4.60 = FY2021)")
# Ultimo TTM = Q1..Q4 2022 = 1.30+1.40+1.50+1.60 = 5.80
last_ttm = ttm[-1]
assert abs(last_ttm[1] - 5.80) < 1e-9, f"ultimo TTM sbagliato: {last_ttm}"
print(f"  ✓ Ultimo TTM (fine 2022) = {last_ttm[1]:.2f} (atteso 5.80)")

print("\n" + "=" * 60)
print("TEST 3 — merge as-of con prezzi + fair value (unita' PER AZIONE)")
print("=" * 60)
# Prezzi sintetici: uno prima di ogni EPS, uno dopo
prices = [
    (date(2021, 6, 15), 60.0),   # prima del primo TTM completo -> deve essere saltato
    (date(2022, 1, 15), 90.0),   # usa TTM fine-2021 = 4.60
    (date(2023, 2, 1), 70.0),    # usa TTM fine-2022 = 5.80
]
rows = build_fair_value_rows(prices, ttm, target_pes=[15, 20])
for r in rows:
    print(f"  {r['date']}  price={r['price']:.1f}  eps_ttm={r['eps']:.2f}  "
          f"FV@15={r['fair_value_pe15']:.2f}  FV@20={r['fair_value_pe20']:.2f}")

# La riga di giugno 2021 deve essere saltata (nessun TTM completo prima)
assert all(r["date"] != date(2021, 6, 15) for r in rows), "riga pre-EPS non saltata!"
# Gennaio 2022: EPS as-of = 4.60 (fine 2021), FV@15 = 69.0
jan = next(r for r in rows if r["date"] == date(2022, 1, 15))
assert abs(jan["fair_value_pe15"] - 69.0) < 1e-9, f"FV sbagliato: {jan}"
print(f"\n  ✓ 2022-01-15: FV@15 = {jan['fair_value_pe15']:.1f} (= 4.60 EPS x 15)")
print(f"    prezzo 90 > FV 69  => OVERVALUED a P/E 15 ✓ (coerente)")
# Feb 2023: EPS as-of = 5.80, FV@15 = 87.0, prezzo 70 < 87 -> undervalued
feb = next(r for r in rows if r["date"] == date(2023, 2, 1))
assert abs(feb["fair_value_pe15"] - 87.0) < 1e-9
print(f"  ✓ 2023-02-01: FV@15 = {feb['fair_value_pe15']:.1f}, prezzo 70 < 87 => UNDERVALUED ✓")

print("\n" + "=" * 60)
print("TEST 4 — crescita YoY corretta (no anno parziale)")
print("=" * 60)
g = ttm_growth_yoy(ttm)
# TTM fine-2022 (5.80) vs TTM fine-2021 (4.60) = +26.1%
print(f"  Growth YoY (TTM) = {g:.1f}%  (atteso ~+26.1%)")
assert 25 < g < 27, f"growth sbagliato: {g}"
print("  ✓ Confronta anni COMPLETI, niente distorsione da trimestre parziale")

print("\n" + "=" * 60)
print("TEST 5 — fallback annuale quando i trimestri sono < 4")
print("=" * 60)
only_annual = {"facts": {"us-gaap": {"EarningsPerShareDiluted": {"units": {"USD/shares": [
    make_fact("2019-01-01", "2019-12-31", 3.0, "FY", "10-K"),
    make_fact("2020-01-01", "2020-12-31", 3.5, "FY", "10-K"),
    make_fact("2021-01-01", "2021-12-31", 4.2, "FY", "10-K"),
]}}}}}
f2 = extract_eps_facts(only_annual)
series2, method2 = build_ttm_eps(f2)
print(f"  Metodo: {method2} (atteso 'annual')")
assert method2 == "annual"
print(f"  Serie: {[(str(d), v) for d, v in series2]}")
print("  ✓ Fallback annuale attivo quando mancano i trimestri")

print("\n" + "=" * 60)
print("TEST 6 — dedup split-aware (tiene il filing piu' recente)")
print("=" * 60)
# Stesso trimestre riportato due volte: pre-split (valore grande, depositato prima)
# e post-split (valore piccolo, depositato dopo). Deve vincere il post-split.
def mk_filed(start, end, val, fp, form, filed, frame=None):
    d = make_fact(start, end, val, fp, form, frame)
    d["filed"] = filed
    return d

split_case = {"facts": {"us-gaap": {"EarningsPerShareDiluted": {"units": {"USD/shares": [
    # Q1 2022 pre-split: 30.0 (deposito 2022-04, con frame vecchio)
    mk_filed("2022-01-01", "2022-03-31", 30.0, "Q1", "10-Q", "2022-04-26", "CY2022Q1"),
    # stesso Q1 2022 post-split (20:1): 1.50 (ridepositato 2023-04 come comparativo)
    mk_filed("2022-01-01", "2022-03-31", 1.50, "Q1", "10-Q", "2023-04-25", None),
]}}}}}
f6 = extract_eps_facts(split_case)
q6 = dict(build_quarterly_eps(f6))
val = q6[date(2022, 3, 31)]
print(f"  Valore tenuto per Q1 2022: {val}  (atteso 1.50, post-split)")
assert abs(val - 1.50) < 1e-9, f"dedup split fallita: ha tenuto {val}"
print("  ✓ Tiene il post-split (filing piu' recente), scarta il pre-split 30.0")

print("\n" + "=" * 60)
print("TEST 7 — crescita YoY basata su date (robusta)")
print("=" * 60)
from edgar_logic import ttm_growth_yoy_dated
# serie TTM: un anno fa 10.0, oggi 13.0 -> +30%
dated_series = [
    (date(2024, 3, 31), 9.0),
    (date(2024, 6, 30), 9.5),
    (date(2025, 3, 31), 10.0),   # ~1 anno prima dell'ultimo
    (date(2025, 6, 30), 11.0),
    (date(2026, 3, 31), 13.0),   # ultimo; confronto con 2025-03-31 = 10.0
]
g7 = ttm_growth_yoy_dated(dated_series)
print(f"  Growth YoY (dated) = {g7:.1f}%  (atteso +30.0%)")
assert abs(g7 - 30.0) < 1e-6, f"growth dated sbagliato: {g7}"
print("  ✓ Confronta con il punto piu' vicino a 365 giorni prima, non per indice")

print("\n" + "=" * 60)
print("✅ TUTTI I TEST PASSATI — la logica di trasformazione e' corretta")
print("=" * 60)
