"""Test offline della classificazione Lynch e dei multipli per categoria."""
from datetime import date, timedelta
from edgar_logic import (classify_lynch, lynch_ratio, eps_cagr,
                         earnings_volatility, had_recent_losses)


def series(vals, start_year=2019):
    """Costruisce una serie TTM trimestrale dai valori dati."""
    out, d = [], date(start_year, 3, 31)
    for v in vals:
        out.append((d, v))
        d += timedelta(days=91)
    return out


print("=" * 72)
print("TEST CLASSIFICAZIONE NELLE SEI CATEGORIE DI LYNCH")
print("=" * 72)

cases = [
    ("NVDA-like (crescita esplosiva)",
     dict(growth_pct=55.0, eps_now=6.5, volatility=40, sector="Technology",
          dividend_yield=0.05, price_to_book=45, market_cap=3e12, recent_losses=False),
     "Fast Grower", 25.0),

    ("MSFT-like (crescita solida)",
     dict(growth_pct=15.0, eps_now=16.8, volatility=20, sector="Technology",
          dividend_yield=0.95, price_to_book=12, market_cap=3e12, recent_losses=False),
     "Stalwart", 15.0),

    ("KO-like (crescita lenta, dividendo)",
     dict(growth_pct=5.0, eps_now=2.5, volatility=15, sector="Consumer Defensive",
          dividend_yield=3.0, price_to_book=10, market_cap=2.5e11, recent_losses=False),
     "Slow Grower", 8.0),

    ("Petrolifera (settore ciclico, utili erratici)",
     dict(growth_pct=12.0, eps_now=8.0, volatility=85, sector="Energy",
          dividend_yield=4.0, price_to_book=1.8, market_cap=3e11, recent_losses=False),
     "Cyclical", 12.0),

    ("Banca sotto il patrimonio (asset play)",
     dict(growth_pct=6.0, eps_now=3.0, volatility=30, sector="Financial Services",
          dividend_yield=5.0, price_to_book=0.72, market_cap=2e10, recent_losses=False),
     "Asset Play", None),

    ("AMZN-like 2023 (perdite recenti)",
     dict(growth_pct=18.0, eps_now=2.9, volatility=120, sector="Consumer Cyclical",
          dividend_yield=None, price_to_book=8, market_cap=1.5e12, recent_losses=True),
     "Turnaround", None),

    ("BBY-like (utili in calo, -11%)",
     dict(growth_pct=-11.2, eps_now=5.41, volatility=25, sector="Consumer Defensive",
          dividend_yield=4.4, price_to_book=5, market_cap=1.8e10, recent_losses=False),
     "Slow Grower (declining earnings)", None),

    ("Slow grower quasi ferma (pavimento a 6)",
     dict(growth_pct=1.0, eps_now=3.0, volatility=12, sector="Utilities",
          dividend_yield=1.5, price_to_book=2, market_cap=3e10, recent_losses=False),
     "Slow Grower", 6.0),

    ("Storico troppo corto",
     dict(growth_pct=None, eps_now=1.0, volatility=None, sector="Technology",
          dividend_yield=0, price_to_book=3, market_cap=1e9, recent_losses=False),
     "Unclassified", None),
]

for label, kw, exp_cat, exp_pe in cases:
    r = classify_lynch(**kw)
    ok_cat = r["category"].startswith(exp_cat)
    ok_pe = (r["fair_pe"] is None and exp_pe is None) or (
        r["fair_pe"] is not None and exp_pe is not None and abs(r["fair_pe"] - exp_pe) < 0.01)
    mark = "✓" if (ok_cat and ok_pe) else "✗"
    pe_txt = f"P/E {r['fair_pe']:.1f}" if r["fair_pe"] else "nessun multiplo"
    print(f"\n{mark} {label}")
    print(f"    → {r['category']:22s} {pe_txt}")
    print(f"      base: {r['basis']}")
    assert ok_cat, f"categoria attesa {exp_cat}, ottenuta {r['category']}"
    assert ok_pe, f"P/E atteso {exp_pe}, ottenuto {r['fair_pe']}"

print("\n" + "=" * 72)
print("TEST PRIORITA' — turnaround e asset play prevalgono sulla crescita")
print("=" * 72)
# una fast grower con perdite recenti deve risultare Turnaround, non Fast Grower
r = classify_lynch(growth_pct=40, eps_now=2.0, volatility=90, sector="Technology",
                   dividend_yield=0, price_to_book=5, market_cap=1e11,
                   recent_losses=True)
assert r["category"] == "Turnaround", r
print(f"  ✓ Crescita 40% + perdite recenti → {r['category']} (non Fast Grower)")

r = classify_lynch(growth_pct=30, eps_now=2.0, volatility=20, sector="Industrials",
                   dividend_yield=1, price_to_book=0.8, market_cap=1e10,
                   recent_losses=False)
assert r["category"] == "Asset Play", r
print(f"  ✓ Crescita 30% + P/B 0.8 → {r['category']} (non Fast Grower)")

print("\n" + "=" * 72)
print("TEST CAP DEL FAST GROWER")
print("=" * 72)
for g in (22, 25, 45, 90):
    r = classify_lynch(growth_pct=g, eps_now=1, volatility=10, sector="Technology",
                       dividend_yield=0, price_to_book=5, market_cap=1e11)
    print(f"  crescita {g:>2}% → P/E equo {r['fair_pe']:.0f}")
    assert r["fair_pe"] <= 25.0
print("  ✓ Il multiplo non supera mai 25")

print("\n" + "=" * 72)
print("TEST LYNCH RATIO")
print("=" * 72)
for lbl, g, dv, pe in [("Titolo a sconto", 20, 2.0, 10),
                       ("Titolo equo", 15, 0.0, 15),
                       ("Titolo caro", 8, 1.0, 30)]:
    lr = lynch_ratio(g, dv, pe)
    verdict = "interessante" if lr > 1.5 else ("equo" if lr >= 1 else "caro")
    print(f"  {lbl:18s} (cresc {g}% + div {dv}%) / P/E {pe} = {lr:.2f} → {verdict}")
assert lynch_ratio(20, 2, 10) == 2.2
print("  ✓ Formula corretta")

print("\n" + "=" * 72)
print("TEST CAGR e VOLATILITA'")
print("=" * 72)
# crescita costante ~15% annuo su 5 anni (20 trimestri)
vals = [1.0 * (1.15 ** (i / 4)) for i in range(24)]
s = series(vals, 2019)
c = eps_cagr(s, years=5)
print(f"  CAGR calcolato su serie a +15%/anno: {c:.1f}%")
assert 13 < c < 17, c
v_stable = earnings_volatility(s)
print(f"  Volatilità serie stabile: {v_stable:.1f}")

# serie erratica (ciclica)
import random
random.seed(1)
vals_c = [max(0.2, 3 + 2.5 * random.uniform(-1, 1)) for _ in range(24)]
v_cyc = earnings_volatility(series(vals_c, 2019))
print(f"  Volatilità serie erratica: {v_cyc:.1f}")
assert v_cyc > v_stable
print("  ✓ La volatilità distingue utili stabili da erratici")

print("\n" + "=" * 72)
print("✅ TUTTI I TEST DI CLASSIFICAZIONE PASSATI")
print("=" * 72)
