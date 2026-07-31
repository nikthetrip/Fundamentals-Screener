"""Test offline della classificazione Lynch, dei multipli e delle ancore.

Copre le tre correzioni che hanno cambiato la classificazione:
  1. volatilita' degli utili robusta (MAD ritagliato, denominatore con pavimento)
  2. turnaround definito da QUANDO e QUANTO si e' perso, non da un si'/no
  3. ogni categoria con un'ancora di valutazione dichiarata
"""
from datetime import date, timedelta
from edgar_logic import (classify_lynch, lynch_ratio, eps_cagr,
                         earnings_volatility, had_recent_losses, loss_profile,
                         eps_growth_trend, growth_estimate,
                         CYCLICAL_VOL_THRESHOLD)


def series(vals, start_year=2019):
    """Costruisce una serie TTM trimestrale dai valori dati."""
    out, d = [], date(start_year, 3, 31)
    for v in vals:
        out.append((d, v))
        d += timedelta(days=91)
    return out


def check(label, got, want):
    mark = "✓" if got == want else "✗"
    print(f"  {mark} {label:52s} {got}")
    assert got == want, f"{label}: atteso {want}, ottenuto {got}"


print("=" * 72)
print("TEST CLASSIFICAZIONE NELLE SEI CATEGORIE DI LYNCH")
print("=" * 72)

NO_LOSS = {"any": False, "current": False, "periods": 0, "share": 0.0,
           "quarters_since": None, "episodes": 0}

cases = [
    ("NVDA-like (crescita esplosiva)",
     dict(growth_pct=55.0, eps_now=6.5, volatility=40, sector="Technology",
          dividend_yield=0.05, price_to_book=45, market_cap=3e12, losses=NO_LOSS),
     "Fast Grower", 25.0, "earnings"),

    ("MSFT-like (crescita solida)",
     dict(growth_pct=15.0, eps_now=16.8, volatility=20, sector="Technology",
          dividend_yield=0.95, price_to_book=12, market_cap=3e12, losses=NO_LOSS),
     # 15% di crescita + 0,95% di dividendo: la somma vale in TUTTE le fasce,
     # ed e' cio' che rende il multiplo continuo sui confini (vedi il test
     # dedicato piu' sotto).
     "Stalwart", 15.95, "earnings"),

    ("KO-like (crescita lenta, dividendo)",
     dict(growth_pct=5.0, eps_now=2.5, volatility=15, sector="Consumer Defensive",
          dividend_yield=3.0, price_to_book=10, market_cap=2.5e11, losses=NO_LOSS),
     "Slow Grower", 8.0, "earnings"),

    ("Petrolifera (settore ciclico, utili erratici)",
     dict(growth_pct=12.0, eps_now=8.0, volatility=85, sector="Energy",
          dividend_yield=4.0, price_to_book=1.8, market_cap=3e11,
          losses=NO_LOSS, eps_normalized=6.0),
     "Cyclical", 12.0, "earnings"),

    ("Banca sotto il patrimonio (asset play)",
     dict(growth_pct=6.0, eps_now=3.0, volatility=30, sector="Financial Services",
          dividend_yield=5.0, price_to_book=0.72, market_cap=2e10, losses=NO_LOSS),
     "Asset Play", None, "book"),

    ("Slow grower quasi ferma (pavimento a 6)",
     dict(growth_pct=1.0, eps_now=3.0, volatility=12, sector="Utilities",
          dividend_yield=1.5, price_to_book=2, market_cap=3e10, losses=NO_LOSS),
     "Slow Grower", 6.0, "earnings"),

    ("Storico troppo corto",
     dict(growth_pct=None, eps_now=1.0, volatility=None, sector="Technology",
          dividend_yield=0, price_to_book=3, market_cap=1e9, losses=NO_LOSS),
     "Unclassified", None, "none"),
]

for label, kw, exp_cat, exp_pe, exp_anchor in cases:
    r = classify_lynch(**kw)
    ok_cat = r["category"].startswith(exp_cat)
    ok_pe = (r["fair_pe"] is None and exp_pe is None) or (
        r["fair_pe"] is not None and exp_pe is not None
        and abs(r["fair_pe"] - exp_pe) < 0.01)
    ok_anchor = r["anchor"] == exp_anchor
    mark = "✓" if (ok_cat and ok_pe and ok_anchor) else "✗"
    pe_txt = f"P/E {r['fair_pe']:.1f}" if r["fair_pe"] else "nessun multiplo"
    print(f"\n{mark} {label}")
    print(f"    → {r['category']:24s} {pe_txt:16s} ancora: {r['anchor']}"
          f" · fiducia: {r['confidence']}")
    print(f"      base: {r['basis']}")
    assert ok_cat, f"categoria attesa {exp_cat}, ottenuta {r['category']}"
    assert ok_pe, f"P/E atteso {exp_pe}, ottenuto {r['fair_pe']}"
    assert ok_anchor, f"ancora attesa {exp_anchor}, ottenuta {r['anchor']}"

print("\n" + "=" * 72)
print("TEST TURNAROUND — un anno storto non e' un dissesto")
print("=" * 72)
# Il caso che rompeva tutto: Amazon 2022. Una perdita, chiusa da anni, mentre la
# societa' guadagna. Con la vecchia regola finiva fra le societa' in ripresa dal
# dissesto — e perdeva ogni fair value — insieme ad altre 358.
healed = {"any": True, "current": False, "periods": 4, "share": 0.20,
          "quarters_since": 3.5, "episodes": 1}
r = classify_lynch(growth_pct=18.0, eps_now=5.0, volatility=30,
                   sector="Technology", dividend_yield=None, price_to_book=8,
                   market_cap=1.5e12, losses=healed)
check("perdita isolata chiusa da 3,5 anni → non e' un turnaround",
      r["category"], "Stalwart")
check("  ...ma la fiducia scende", r["confidence"] in ("medium", "low"), True)

current = {"any": True, "current": True, "periods": 6, "share": 0.30,
           "quarters_since": 0.0, "episodes": 1}
r = classify_lynch(growth_pct=40.0, eps_now=-1.0, volatility=90,
                   sector="Technology", dividend_yield=0, price_to_book=5,
                   market_cap=1e10, losses=current, eps_normalized=1.2)
check("in perdita ORA → Turnaround anche con crescita 40%",
      r["category"], "Turnaround")
check("  ...con multiplo di ripresa sugli utili normalizzati",
      (r["fair_pe"], r["eps_base"]), (10.0, "normalized"))

fresh = {"any": True, "current": False, "periods": 5, "share": 0.25,
         "quarters_since": 0.5, "episodes": 1}
r = classify_lynch(growth_pct=12.0, eps_now=2.0, volatility=30,
                   sector="Technology", dividend_yield=0, price_to_book=3,
                   market_cap=1e10, losses=fresh, eps_normalized=1.5)
check("tornata all'utile da 6 mesi → Turnaround", r["category"], "Turnaround")

recurring = {"any": True, "current": False, "periods": 8, "share": 0.40,
             "quarters_since": 1.5, "episodes": 3}
r = classify_lynch(growth_pct=8.0, eps_now=2.0, volatility=30,
                   sector="Healthcare", dividend_yield=0, price_to_book=3,
                   market_cap=1e10, losses=recurring, eps_normalized=1.0)
check("tre episodi di perdita, l'ultimo 1,5 anni fa → Turnaround",
      r["category"], "Turnaround")

old_recurring = dict(recurring, quarters_since=4.0)
r = classify_lynch(growth_pct=8.0, eps_now=2.0, volatility=30,
                   sector="Healthcare", dividend_yield=0, price_to_book=3,
                   market_cap=1e10, losses=old_recurring, eps_normalized=1.0)
check("stessi episodi ma l'ultimo 4 anni fa → si e' stabilizzata",
      r["category"], "Slow Grower")

print("\n" + "=" * 72)
print("TEST RIMBALZO — la ripresa da una perdita non e' crescita")
print("=" * 72)
r = classify_lynch(growth_pct=90.0, eps_now=3.0, volatility=30,
                   sector="Technology", dividend_yield=0, price_to_book=4,
                   market_cap=2e10, losses=healed, rebound_risk=True)
check("crescita 90% da base in perdita → non e' una Fast Grower",
      r["category"], "Stalwart (recovering)")
check("  ...multiplo fermato a 15, non 25", r["fair_pe"], 15.0)
r = classify_lynch(growth_pct=90.0, eps_now=3.0, volatility=30,
                   sector="Technology", dividend_yield=0, price_to_book=4,
                   market_cap=2e10, losses=NO_LOSS, rebound_risk=False)
check("stessa crescita da base sana → Fast Grower al cap", r["fair_pe"], 25.0)

print("\n" + "=" * 72)
print("TEST CICLICHE — il settore da solo non basta")
print("=" * 72)
cyc = dict(growth_pct=8.0, eps_now=3.0, volatility=90, dividend_yield=1.0,
           price_to_book=3, market_cap=5e10, losses=NO_LOSS, eps_normalized=2.5)

r = classify_lynch(sector="Energy", industry="Oil & Gas E&P", **cyc)
check("petrolifera con utili erratici → Ciclica", r["category"], "Cyclical")

# Il caso Amazon: "Consumer Cyclical" e' il SETTORE, ma il commercio elettronico
# non oscilla con il ciclo delle materie prime. Con il solo settore prendeva un
# P/E fisso di 12 su utili normalizzati.
r = classify_lynch(sector="Consumer Cyclical", industry="Internet Retail", **cyc)
check("commercio online in Consumer Cyclical → NON ciclica",
      r["category"], "Slow Grower")

r = classify_lynch(sector="Industrials", industry="Aerospace & Defense", **cyc)
check("difesa (contratti pluriennali) → NON ciclica", r["category"], "Slow Grower")

r = classify_lynch(sector="Real Estate", industry="REIT - Healthcare Facilities", **cyc)
check("REIT sanitario → NON ciclico", r["category"], "Slow Grower")

# Il caso Fiserv: settore indovinato dal codice SIC, non accertato.
r = classify_lynch(sector="Industrials", industry="Specialty Industrial Machinery",
                   classification_source="sic", **cyc)
check("settore indovinato dal SIC → la regola delle cicliche non scatta",
      r["category"], "Slow Grower")
check("  ...e la supposizione e' dichiarata",
      "SIC code" in r["confidence_note"], True)

r = classify_lynch(sector="Industrials", industry="Specialty Industrial Machinery",
                   classification_source="yfinance", **cyc)
check("stesso titolo con settore accertato → Ciclica", r["category"], "Cyclical")

print("\n" + "=" * 72)
print("TEST ANCORE — ogni categoria dichiara su cosa poggia")
print("=" * 72)
# La lacuna che lasciava 494 righe su 989 senza alcun fair value.
r = classify_lynch(growth_pct=-8.0, eps_now=4.0, volatility=20,
                   sector="Consumer Defensive", dividend_yield=4.4,
                   price_to_book=5, market_cap=1.8e10, losses=NO_LOSS,
                   eps_normalized=4.8)
check("utili in calo → non piu' senza multiplo",
      r["category"], "Slow Grower (declining earnings)")
check("  ...pavimento 6, nessun credito alla crescita negativa",
      r["fair_pe"], 6.0)
# La base NON e' piu' quella normalizzata. Lo era, e produceva un gradino del
# 33% esattamente sul confine dello zero: a -0,1% di crescita il fair value
# nasceva dalla mediana a tre anni, a +0,1% dall'ultimo TTM. La prudenza verso
# chi ha utili in calo passa ora dal MULTIPLO (zero credito alla crescita
# negativa), non dalla base — vedi il test di continuita' in fondo.
check("  ...su una base uguale a quella delle altre fasce di crescita",
      r["eps_base"], "current")

r = classify_lynch(growth_pct=None, eps_now=-2.0, volatility=None,
                   sector="Healthcare", dividend_yield=0, price_to_book=0.6,
                   market_cap=5e8, losses=current, eps_normalized=-1.0)
check("in perdita e sotto il patrimonio → Asset Play sul patrimonio",
      (r["category"], r["anchor"], r["fair_pb"]), ("Asset Play", "book", 1.0))

r = classify_lynch(growth_pct=None, eps_now=-2.0, volatility=None,
                   sector="Healthcare", dividend_yield=0, price_to_book=6.0,
                   market_cap=5e8, losses=current, eps_normalized=-1.0)
check("in perdita e senza patrimonio a sconto → nessuna ancora, dichiarata",
      (r["category"], r["anchor"]), ("Turnaround", "none"))

print("\n" + "=" * 72)
print("TEST CAP DEL FAST GROWER")
print("=" * 72)
for g in (22, 25, 45, 90):
    r = classify_lynch(growth_pct=g, eps_now=1, volatility=10, sector="Technology",
                       dividend_yield=0, price_to_book=5, market_cap=1e11,
                       losses=NO_LOSS)
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
print("TEST CRESCITA — tendenza contro CAGR fra due estremi")
print("=" * 72)
# Crescita costante: le due misure devono coincidere.
steady = series([1.0 * (1.15 ** (i / 4)) for i in range(24)], 2019)
t, c = eps_growth_trend(steady, 5), eps_cagr(steady, 5)
print(f"  serie a +15%/anno → tendenza {t:.1f}%  ·  CAGR {c:.1f}%")
assert 13 < t < 17 and 13 < c < 17

# Un solo trimestre di partenza depresso: il CAGR lo prende per buono, la
# tendenza no. E' il caso che produceva i multipli equi senza senso.
# La finestra e' esattamente di cinque anni (21 punti trimestrali), cosi' che il
# punto di partenza scelto dal CAGR sia proprio quello anomalo.
dip = [1.0 * (1.05 ** (i / 4)) for i in range(21)]
dip[0] = 0.25                       # trimestre iniziale anomalo
t, c = eps_growth_trend(series(dip, 2021), 5), eps_cagr(series(dip, 2021), 5)
print(f"  stessa serie con la partenza depressa → tendenza {t:.1f}%  ·  CAGR {c:.1f}%")
assert t < c, "la tendenza deve resistere a un estremo anomalo meglio del CAGR"
print("  ✓ La tendenza non consegna il multiplo al trimestre di partenza")

ge = growth_estimate(steady)
check("scala della crescita: preferisce la tendenza a 5 anni",
      (ge["basis"].startswith("5-year trend"), ge["rebound_risk"]), (True, False))
# Perdite IN MEZZO alla finestra: la regressione non e' possibile (il logaritmo
# di un numero negativo non esiste), quindi si ripiega sul CAGR — e siccome quel
# tasso attraversa la perdita, e' in buona parte rimbalzo. Va segnalato.
mid_loss = series([0.30, 0.32, 0.28, 0.20, -0.4, -0.6, -0.3, 0.2,
                   0.8, 1.2, 1.6, 2.0, 2.2, 2.4, 2.6, 2.8,
                   2.9, 3.0, 3.1, 3.2, 3.3], 2021)
ge = growth_estimate(mid_loss)
check("perdite in mezzo alla finestra: CAGR, con il rimbalzo segnalato",
      (ge["basis"].startswith("5-year CAGR"), ge["rebound_risk"]), (True, True))

# Perdite solo all'INIZIO, poi cinque anni... no: tre anni puliti. Qui il
# ripiego a tre anni misura una tendenza vera, su utili tutti positivi, e NON va
# marcato come rimbalzo — altrimenti si penalizzerebbe una societa' risanata
# due volte, una in classificazione e una nel multiplo.
early_loss = series([-0.5] * 4 + [0.5 * (1.2 ** (i / 4)) for i in range(17)], 2021)
ge = growth_estimate(early_loss)
check("perdite solo all'inizio: finestra a 3 anni pulita, nessun rimbalzo",
      (ge["basis"].startswith("3-year trend"), ge["rebound_risk"]), (True, False))

print("\n" + "=" * 72)
print("TEST VOLATILITA' — robusta alla base vicina a zero e all'episodio unico")
print("=" * 72)
v_stable = earnings_volatility(steady)
print(f"  serie stabile a +15%/anno         : {v_stable:6.1f}")

import random
random.seed(1)
vals_c = [max(0.2, 3 + 2.5 * random.uniform(-1, 1)) for _ in range(24)]
v_cyc = earnings_volatility(series(vals_c, 2019))
print(f"  serie erratica (ciclica)          : {v_cyc:6.1f}")
assert v_cyc > v_stable
check("  distingue utili stabili da erratici", v_cyc > CYCLICAL_VOL_THRESHOLD, True)

# Il caso Covid: una societa' regolare con UN solo anno anomalo. Con la
# deviazione standard bastava a farla sembrare ciclica; con la MAD no.
shock = [2.0 + 0.05 * i for i in range(24)]
shock[8:12] = [0.1, 0.1, 0.1, 0.1]          # un anno di crollo, poi ripresa
v_shock = earnings_volatility(series(shock, 2019))
print(f"  serie regolare con UN anno di crollo: {v_shock:6.1f}")
check("  un episodio isolato non la rende ciclica",
      v_shock < CYCLICAL_VOL_THRESHOLD, True)

# Base vicina a zero: prima produceva percentuali a sei cifre.
near_zero = series([0.01, 0.02, 0.01, 0.5, 1.0, 1.2, 1.1, 1.3,
                    1.4, 1.5, 1.6, 1.5, 1.7, 1.8, 1.9, 2.0], 2019)
v_nz = earnings_volatility(near_zero)
print(f"  serie che parte da 0,01$          : {v_nz:6.1f}")
check("  resta in una scala leggibile (<= 200)", v_nz <= 200.0, True)

print("\n" + "=" * 72)
print("TEST loss_profile — anatomia delle perdite")
print("=" * 72)
two_ep = series([1.0, -0.5, -0.4, 1.0, 1.2, 1.3, 1.4, 1.5,
                 1.6, -0.2, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5,
                 1.6, 1.7, 1.8, 1.9], 2019)
lp = loss_profile(two_ep, years=5)
check("due episodi separati da oltre un anno", lp["episodes"], 2)
check("non e' in perdita adesso", lp["current"], False)
check("had_recent_losses resta il si'/no grezzo",
      had_recent_losses(two_ep, years=5), lp["any"])

print("\n" + "=" * 72)
print("TEST CONTINUITA' — il fair value non deve avere gradini")
print("=" * 72)
# E' il test piu' severo del file, ed e' quello che ha trovato i due difetti
# peggiori. Si spazza la crescita a passi di un decimo di punto attraverso tutti
# i confini fra categorie tenendo fermo tutto il resto: se il fair value salta,
# due societa' che differiscono solo per il rumore di misura ricevono
# valutazioni molto diverse.
#
# Cosa ha trovato:
#  - un'INVERSIONE sul confine del 10%: a 9,9% il multiplo era min(9,9+div, 12)
#    = 11,9, a 10,0% era 10,0 secco. Passare nella categoria migliore faceva
#    SCENDERE il fair value del 16%. Corretto sommando il dividendo in tutte
#    le fasce, non solo nelle slow grower.
#  - un GRADINO del 33% dove cambiava la base di utili fra una categoria e
#    l'altra. Corretto usando la stessa base in tutta la scala di crescita.
prev, jumps = None, []
for i in range(-150, 401):
    g = i / 10
    r = classify_lynch(growth_pct=g, eps_now=4.0, volatility=20,
                       sector="Technology", dividend_yield=2.0, price_to_book=3,
                       market_cap=1e11, losses=NO_LOSS, eps_normalized=3.0)
    base = 3.0 if r["eps_base"] == "normalized" else 4.0
    fv = base * r["fair_pe"] if r["fair_pe"] else None
    if prev and fv and prev[1]:
        jump = abs(fv / prev[1] - 1) * 100
        if jump > 3.0:
            jumps.append((prev[0], g, prev[1], fv, jump))
    prev = (g, fv)
for a, b, fa, fb, j in jumps:
    print(f"  ✗ salto {a:.1f}% → {b:.1f}%: {fa:.2f} → {fb:.2f} ({j:+.0f}%)")
check("nessun gradino oltre il 3% da -15% a +40% di crescita", len(jumps), 0)

# E la monotonia: piu' cresci, piu' vali. Un modello in cui crescere di piu'
# abbassa il fair value e' rotto, per quanto continuo sia.
pes = []
for i in range(0, 401):
    r = classify_lynch(growth_pct=i / 10, eps_now=4.0, volatility=20,
                       sector="Technology", dividend_yield=2.0, price_to_book=3,
                       market_cap=1e11, losses=NO_LOSS, eps_normalized=3.0)
    pes.append(r["fair_pe"])
drops = [(i / 10, pes[i - 1], pes[i]) for i in range(1, len(pes))
         if pes[i] < pes[i - 1] - 1e-9]
for g, a, b in drops[:5]:
    print(f"  ✗ a crescita {g:.1f}% il multiplo scende da {a:.2f} a {b:.2f}")
check("il multiplo non scende mai al crescere della crescita", len(drops), 0)

print("\n" + "=" * 72)
print("✅ TUTTI I TEST DI CLASSIFICAZIONE PASSATI")
print("=" * 72)
