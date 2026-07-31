"""
Test dei guardrail sui dati: arbitraggio tra fonti EPS discordi e
cancello di plausibilita' sul fair value.
Casi reali che hanno motivato queste protezioni: WM e HAL.
"""
from datetime import date, timedelta
from edgar_logic import (choose_eps, fair_value_is_plausible, fair_value_check, normalized_eps,
                         extract_eps_facts, MAX_PLAUSIBLE_EPS)

print("=" * 72)
print("TEST 1 — caso WM: yfinance sbagliato, EDGAR corretto")
print("=" * 72)
# Waste Management reale: prezzo 238.75, EPS vero ~6.91.
# yfinance riportava 0.86 -> P/E implicito 277 (implausibile).
val, src, flag = choose_eps(eps_edgar=6.91, eps_yf=0.86,
                            eps_norm=6.41, price=238.75, days_stale=30)
print(f"  EDGAR 6.91 · yfinance 0.86 · normalizzato 6.41 · prezzo 238.75")
print(f"  → scelto {val} (fonte: {src}, flag: {flag})")
print(f"    P/E implicito con EDGAR:    {238.75/6.91:.1f}  ✓ plausibile")
print(f"    P/E implicito con yfinance: {238.75/0.86:.1f}  ✗ implausibile")
assert val == 6.91 and src == "edgar", f"ha scelto {val} da {src}"
print("  ✓ Sceglie EDGAR: coerente con lo storico e con un P/E sensato")

print("\n" + "=" * 72)
print("TEST 2 — caso opposto: EDGAR stantio, yfinance corretto")
print("=" * 72)
# Se la serie EDGAR e' vecchia e incoerente con lo storico recente,
# deve vincere yfinance.
val, src, flag = choose_eps(eps_edgar=2.00, eps_yf=8.50,
                            eps_norm=8.20, price=170.0, days_stale=400)
print(f"  EDGAR 2.00 (stantio) · yfinance 8.50 · normalizzato 8.20 · prezzo 170")
print(f"  → scelto {val} (fonte: {src})")
assert src == "yfinance", f"ha scelto {src}"
print("  ✓ Sceglie yfinance quando EDGAR e' incoerente")

print("\n" + "=" * 72)
print("TEST 3 — fonti concordi: nessun arbitraggio necessario")
print("=" * 72)
val, src, flag = choose_eps(16.79, 16.61, 15.0, 381.70, 20)
print(f"  EDGAR 16.79 · yfinance 16.61 (divergenza 1.1%) → {val} ({src}, {flag})")
assert flag == "ok"
print("  ✓ Sotto il 15% di divergenza usa yfinance senza segnalazioni")

print("\n" + "=" * 72)
print("TEST 4 — caso HAL: fair value assurdo bloccato")
print("=" * 72)
# Il cancello e' ASIMMETRICO, e la ragione e' che i due errori non sono
# simmetrici. Verso l'alto bastano 5 volte il prezzo: che il mercato sbagli del
# 400% e' molto meno probabile che sbagli la nostra base di utili. Verso il
# basso il fondo e' molto piu' largo (1/20), perche' `fair value / prezzo` e'
# identicamente `P/E equo / P/E effettivo`: un cancello stretto punirebbe il
# multiplo equo BASSO, che e' spesso il giudizio corretto. Misurato sui dati,
# un cancello a un quinto del prezzo avrebbe soppresso 3M e Fortive — societa'
# a P/E 30 giudicate care contro un P/E equo di 6. Quello e' segnale.
cases = [
    ("Halliburton (bug reale)", 10_476_080.11, 33.36, False),
    ("Fair value ragionevole", 40.00, 33.36, True),
    ("Fair value molto basso ma sensato", 5.00, 33.36, True),
    ("Titolo giudicato molto caro (legittimo)", 3.50, 33.36, True),
    ("Fair value irrisorio (errore)", 0.50, 33.36, False),
    ("Fair value 4x il prezzo (limite alto)", 130.0, 33.36, True),
    ("Fair value 20x il prezzo", 660.0, 33.36, False),
]
for label, fv, price, expected in cases:
    ok = fair_value_is_plausible(fv, price)
    mark = "✓" if ok == expected else "✗"
    verdict = "accettato" if ok else "SCARTATO"
    print(f"  {mark} {label:40s} fv=${fv:>14,.2f} vs ${price} → {verdict}")
    assert ok == expected
print("  ✓ Il fair value da 10 milioni non arriva piu' a schermo")

# Il cancello sul P/E della base: utili quasi a zero rendono ogni multiplo
# arbitrario, ed e' il caso di Estee Lauder (fair value allo 0,3% del prezzo).
print("\n  Cancello sul P/E della base usata:")
for label, fv, price, base, expected in [
        ("utili normali (P/E 25)", 60.0, 100.0, 4.00, True),
        ("utili quasi azzerati (P/E 400)", 1.50, 100.0, 0.25, False),
        ("asset play: il P/E non e' il criterio", 90.0, 100.0, 0.25, True)]:
    anchor = "book" if "asset play" in label else "earnings"
    ok, why = fair_value_check(fv, price, base, anchor)
    mark = "✓" if ok == expected else "✗"
    print(f"  {mark} {label:40s} → {'accettato' if ok else 'SCARTATO'}")
    assert ok == expected, why

print("\n" + "=" * 72)
print("TEST 5 — mediana robusta agli outlier (causa del caso HAL)")
print("=" * 72)
base = date(2026, 3, 31)
# serie normale con UN valore corrotto
serie = [(base - timedelta(days=90 * i), v) for i, v in
         enumerate([2.1, 2.0, 1.9, 2.2, 873_000.0, 2.0, 1.8, 2.1, 2.0, 1.9])]
med = normalized_eps(serie, years=3)
media = sum(v for _, v in serie[:12]) / len(serie[:12])
print(f"  Serie con un valore corrotto (873.000 tra valori intorno a 2)")
print(f"    media   → {media:>12,.2f}   ✗ trascinata dall'outlier")
print(f"    mediana → {med:>12,.2f}   ✓ ignora l'outlier")
assert med < 5, f"mediana non robusta: {med}"
print("  ✓ La normalizzazione usa la mediana e resiste ai dati corrotti")

print("\n" + "=" * 72)
print("TEST 6 — valori EPS impossibili scartati all'origine")
print("=" * 72)
rows = [
    {"start": "2025-01-01", "end": "2025-03-31", "val": 2.10, "fy": 2025,
     "fp": "Q1", "form": "10-Q", "filed": "2025-04-30"},
    {"start": "2025-04-01", "end": "2025-06-30", "val": 873000.0, "fy": 2025,
     "fp": "Q2", "form": "10-Q", "filed": "2025-07-30"},   # corrotto
    {"start": "2025-07-01", "end": "2025-09-30", "val": 2.30, "fy": 2025,
     "fp": "Q3", "form": "10-Q", "filed": "2025-10-30"},
]
cf = {"facts": {"us-gaap": {"EarningsPerShareDiluted": {
    "units": {"USD/shares": rows}}}}}
facts = extract_eps_facts(cf)
vals = [f["val"] for f in facts]
print(f"  Fatti in input: 3 (di cui uno da 873.000)")
print(f"  Fatti accettati: {len(facts)} → {vals}")
assert 873000.0 not in vals and len(facts) == 2
print(f"  ✓ Scartati i valori oltre {MAX_PLAUSIBLE_EPS:,.0f}$ per azione")

print("\n" + "=" * 72)
print("✅ TUTTI I TEST SUI GUARDRAIL PASSATI")
print("=" * 72)
