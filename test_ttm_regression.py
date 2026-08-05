#!/usr/bin/env python3
"""
test_ttm_regression.py — Regressione sulla ricostruzione TTM, su dati SEC reali.

A differenza degli altri test, che usano serie sintetiche, questo lavora su
companyfacts EDGAR veri (ridotti ai soli concetti necessari) congelati in
tests/fixtures/. Serve a impedire il ritorno di due difetti che hanno prodotto
verdetti di valutazione sbagliati su circa un quarto dell'S&P 500:

  1. Il Q4 non ricostruito, per una finestra dell'esercizio troppo larga di
     pochi giorni: il TTM finiva per sommare quattro punti distribuiti su
     CINQUE trimestri. Estee Lauder risultava a +1,25$ per azione mentre il
     TTM reale era -0,71$, e Qualcomm veniva etichettata "Undervalued".
  2. La scelta del concetto XBRL per posizione in lista invece che per data,
     che faceva usare tag abbandonati anni prima.

I valori attesi sono stati verificati contro i 10-K depositati.
Non serve rete: le fixture sono nel repo.

Uso:  python test_ttm_regression.py
"""

from __future__ import annotations
import json
import sys
from datetime import date
from pathlib import Path

from edgar_logic import (
    extract_eps_facts, build_ttm_eps, build_quarterly_eps, build_annual_eps,
    build_derived_eps_facts, extract_net_income_facts, extract_dei_shares,
    series_age_days, series_is_stale, _period_bucket,
    TTM_MIN_SPAN, TTM_MAX_SPAN, MAX_SERIES_AGE_DAYS,
)

FIXTURES = Path(__file__).parent / "tests" / "fixtures"

# Data di riferimento: le fixture sono uno scatto del 29 luglio 2026. I test
# sull'obsolescenza sono ancorati a questa data, altrimenti passerebbero oggi
# e fallirebbero fra un anno per il solo scorrere del tempo.
AS_OF = date(2026, 7, 29)

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✓' if ok else '✗'} {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        failures.append(f"{name}: {detail}")


def load(ticker: str) -> dict:
    return json.loads((FIXTURES / f"{ticker}.json").read_text(encoding="utf-8"))


def section(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


# ---------------------------------------------------------------------------
# 1. TTM corretto sui casi che il difetto sbagliava
# ---------------------------------------------------------------------------
# (ticker, TTM atteso, data attesa, riferimento di verifica)
TTM_CASES = [
    ("EL",   -0.71, "2026-03-31", "FY2025 10-K = -3,15$: la societa' e' in perdita, "
                                  "prima risultava a +1,25$"),
    ("CRL",  -3.71, "2026-03-28", "FY2025 10-K = -2,91$: prima risultava a +2,36$"),
    ("QCOM",  9.32, "2026-03-29", "FY2025 10-K = 5,01$: prima 14,61$ ed etichettata Undervalued"),
    ("FSLR", 15.48, "2026-03-31", "FY2025 10-K = 14,21$: prima 12,59$"),
    ("MOS",   0.14, "2026-03-31", "FY2025 10-K = 1,70$: prima 2,52$ ed etichettata Undervalued"),
    ("PM",    6.95, "2026-06-30", "FY2025 10-K = 7,26$: prima 7,54$ su cinque trimestri"),
    ("AMP",  40.13, "2026-03-31", "gia' corretto prima: non deve regredire"),
    ("WM",    6.91, "2026-03-31", "gia' corretto prima: non deve regredire"),
    ("NKE",   2.10, "2026-05-31", "FY2026 10-K = 2,10$: il TTM deve arrivare alla "
                                  "chiusura d'esercizio, non fermarsi un trimestre prima"),
]


def test_ttm_values() -> None:
    section("TEST 1 — TTM EPS verificato contro i 10-K depositati")
    for ticker, expected, expected_date, why in TTM_CASES:
        series, method = build_ttm_eps(extract_eps_facts(load(ticker)))
        if not series:
            check(ticker, False, "serie vuota")
            continue
        got_date, got = series[-1]
        ok = abs(got - expected) < 0.02 and str(got_date) == expected_date
        check(f"{ticker}: {got:+.2f} @ {got_date} (atteso {expected:+.2f} @ {expected_date})",
              ok, why if not ok else f"metodo {method}")


def test_ttm_windows_are_twelve_months() -> None:
    section("TEST 2 — ogni finestra TTM copre davvero dodici mesi")
    for ticker, *_ in TTM_CASES:
        quarters = build_quarterly_eps(extract_eps_facts(load(ticker)))
        series, method = build_ttm_eps(extract_eps_facts(load(ticker)))
        if method != "ttm":
            check(f"{ticker}: metodo {method}", True, "fallback annuale, nessuna finestra da verificare")
            continue
        qdates = [d for d, _ in quarters]
        bad = []
        for d, _ in series:
            i = qdates.index(d)
            span = (qdates[i] - qdates[i - 3]).days
            if not (TTM_MIN_SPAN <= span <= TTM_MAX_SPAN):
                bad.append((str(d), span))
        check(f"{ticker}: {len(series)} finestre TTM tutte fra {TTM_MIN_SPAN} e {TTM_MAX_SPAN} giorni",
              not bad, f"fuori intervallo: {bad[:3]}")


def test_q4_is_reconstructed() -> None:
    section("TEST 3 — il Q4 viene ricostruito anche quando esiste quello dell'anno prima")
    # E' esattamente la condizione che il difetto rendeva impossibile: la
    # finestra dell'esercizio inglobava la chiusura precedente, il conteggio
    # arrivava a quattro e la ricostruzione non scattava mai.
    for ticker in ("EL", "FSLR", "QCOM", "MOS"):
        facts = extract_eps_facts(load(ticker))
        quarters = dict(build_quarterly_eps(facts))
        fy_ends = [d for d, _ in build_annual_eps(facts)]
        missing = [str(d) for d in fy_ends[-3:] if d not in quarters]
        check(f"{ticker}: chiusure d'esercizio presenti nella serie trimestrale",
              not missing, f"mancanti: {missing}")


def test_no_ttm_across_a_gap() -> None:
    section("TEST 4 — con un buco nella serie non viene prodotto alcun TTM")
    # Serie sintetica: manca il terzo trimestre del 2025, e nessun FY permette
    # di ricostruirlo. Sommare i quattro punti disponibili darebbe un numero
    # plausibile e sbagliato; il comportamento corretto e' non produrlo.
    def q(start: str, end: str, val: float) -> dict:
        s, e = date.fromisoformat(start), date.fromisoformat(end)
        return {"start": s, "end": e, "val": val, "fy": None, "fp": None,
                "form": "10-Q", "frame": None, "filed": e, "dur": (e - s).days,
                "concept": "EarningsPerShareDiluted"}

    facts = [
        q("2025-01-01", "2025-03-31", 1.0),
        q("2025-04-01", "2025-06-30", 1.0),
        # trimestre chiuso al 2025-09-30 assente
        q("2025-10-01", "2025-12-31", 1.0),
        q("2026-01-01", "2026-03-31", 1.0),
    ]
    series, method = build_ttm_eps(facts)
    check("nessun TTM emesso attraverso il buco", not series or method == "annual",
          f"emessi {len(series)} punti col metodo {method}")


# ---------------------------------------------------------------------------
# 2. Selezione del concetto XBRL e obsolescenza
# ---------------------------------------------------------------------------

def test_tag_selection_by_recency() -> None:
    section("TEST 5 — si sceglie il concetto XBRL piu' aggiornato, non il primo in lista")
    # Emerson ha abbandonato EarningsPerShareDiluted in favore di
    # IncomeLossFromContinuingOperationsPerDilutedShare.
    facts = extract_eps_facts(load("EMR"))
    concept = facts[0]["concept"] if facts else ""
    check(f"EMR usa '{concept}'",
          concept == "IncomeLossFromContinuingOperationsPerDilutedShare",
          "ha ripreso il tag abbandonato")

    gaap = load("EMR")["facts"]["us-gaap"]
    abandoned = max(r["end"] for r in gaap["EarningsPerShareDiluted"]["units"]["USD/shares"])
    current = max(r["end"] for r in
                  gaap["IncomeLossFromContinuingOperationsPerDilutedShare"]["units"]["USD/shares"])
    check(f"il tag scelto e' piu' recente ({current} contro {abandoned})", current > abandoned)


def test_stale_series_rejected() -> None:
    section("TEST 6 — le serie obsolete vengono riconosciute come tali")
    # Berkshire dal 2014 tagga l'EPS per classe di azioni: le API XBRL espongono
    # solo la serie che si ferma al 2013, con l'EPS della classe A (11.849$).
    # Usarla come se fosse attuale produceva una fair value line a sei cifre.
    series, _ = build_ttm_eps(extract_eps_facts(load("BRK-B")))
    age = series_age_days(series, AS_OF)
    check(f"BRK-B: serie ferma a {series[-1][0]} ({age} giorni fa)",
          series_is_stale(series, today=AS_OF),
          f"non riconosciuta obsoleta con soglia {MAX_SERIES_AGE_DAYS} giorni")

    # Paramount Skydance: entita' nata da fusione, storico discontinuo.
    series, _ = build_ttm_eps(extract_eps_facts(load("PSKY")))
    check("PSKY: serie discontinua riconosciuta obsoleta",
          series_is_stale(series, today=AS_OF),
          f"eta' {series_age_days(series, AS_OF)} giorni")

    # Controprova: i ticker sani NON devono essere scartati.
    for ticker in ("EL", "QCOM", "WM", "NKE"):
        series, _ = build_ttm_eps(extract_eps_facts(load(ticker)))
        check(f"{ticker}: serie attuale, non scartata",
              not series_is_stale(series, today=AS_OF),
              f"eta' {series_age_days(series, AS_OF)} giorni")


def test_derived_eps_for_multiclass() -> None:
    section("TEST 7 — EPS derivato dall'utile netto per le societa' multiclasse")
    # Ares non espone alcun EPS non dimensionale, ma espone l'utile netto.
    cf = load("ARES")
    check("ARES: nessun EPS esposto dalle API XBRL", not extract_eps_facts(cf))
    # La fixture e' troncata al 2021 per tenerla leggera: su EDGAR i fatti sono
    # molti di piu'. Qui basta verificare che la serie sia sostanziosa.
    check("ARES: utile netto invece disponibile", len(extract_net_income_facts(cf)) > 30,
          f"{len(extract_net_income_facts(cf))} fatti")

    shares = 200_000_000.0
    derived = build_derived_eps_facts(cf, shares)
    check("ARES: EPS derivabile dall'utile netto", len(derived) > 30,
          f"{len(derived)} fatti derivati")

    series, method = build_ttm_eps(derived)
    ok = bool(series) and all(abs(v) < 100 for _, v in series)
    check(f"ARES: serie derivata di {len(series)} punti, ultimo {series[-1][1]:.2f}$ "
          f"@ {series[-1][0]}" if series else "ARES: serie derivata vuota", ok)

    # Il conteggio azioni di copertina va scartato se troppo vecchio: per le
    # multiclasse l'ultimo valore non dimensionale risale a prima del cambio
    # di tagging e non descrive piu' la societa'.
    dei = extract_dei_shares(cf)
    check(f"ARES: conteggio azioni di copertina fermo al {dei[0]}, da scartare",
          dei is not None and (AS_OF - dei[0]).days > 400)


def test_derived_eps_is_proportional() -> None:
    section("TEST 8 — l'EPS derivato scala esattamente col numero di azioni")
    cf = load("ARES")
    a, _ = build_ttm_eps(build_derived_eps_facts(cf, 100_000_000.0))
    b, _ = build_ttm_eps(build_derived_eps_facts(cf, 200_000_000.0))
    ok = a and b and abs(a[-1][1] / b[-1][1] - 2.0) < 1e-9
    check("raddoppiare le azioni dimezza l'EPS derivato", bool(ok),
          f"{a[-1][1]:.4f} contro {b[-1][1]:.4f}" if a and b else "serie vuota")


def test_weekly_calendar_duplicates() -> None:
    section("TEST 9 — chi chiude a settimane non ha ogni trimestre due volte")
    # Waters dichiara il primo trimestre 2024 chiuso il 30 marzo nel 10-Q e il
    # 31 marzo nel 10-K: stesso trimestre, stesso EPS, due date. Tenendole
    # entrambe la serie trimestrale raddoppiava, quattro punti consecutivi
    # coprivano sei mesi invece di dodici e rolling_ttm li scartava tutti: la
    # fair value line passava da diciassette anni a uno e mezzo, e la societa'
    # finiva sul ripiego dell'utile netto diviso per le azioni (6,50$ contro i
    # 10,71$ del 10-K 2024).
    facts = extract_eps_facts(load("WAT"))
    quarters = build_quarterly_eps(facts)
    close = [(str(a), str(b)) for a, b in zip(quarters, quarters[1:])
             if (b[0] - a[0]).days <= 15]
    check("WAT: nessuna coppia di chiusure a meno di 15 giorni", not close,
          f"{len(close)} coppie, es. {close[:2]}")

    series, method = build_ttm_eps(facts)
    check(f"WAT: {len(series)} punti TTM col metodo {method}",
          method == "ttm" and len(series) > 60, "serie ancora frammentata")
    check(f"WAT: la serie parte dal {series[0][0]}",
          series[0][0].year <= 2010, "storia persa in testa")
    got_date, got = series[-1]
    check(f"WAT: ultimo TTM {got:+.2f}$ @ {got_date}",
          abs(got - 7.87) < 0.02 and str(got_date) == "2026-04-04",
          "atteso +7,87$ @ 2026-04-04 (FY2025 10-K = 10,76$)")


def test_concept_gaps_are_filled() -> None:
    section("TEST 10 — i buchi di un concetto XBRL si riempiono con gli altri")
    # Southern Copper espone EarningsPerShareDiluted solo dal 2021: gli undici
    # anni precedenti stanno in EarningsPerShareBasicAndDiluted. Prendendo un
    # concetto solo, la fair value line partiva dal 2021 pur essendoci i dati.
    facts = extract_eps_facts(load("SCCO"))
    series, method = build_ttm_eps(facts)
    check(f"SCCO: {len(series)} punti TTM dal {series[0][0]} al {series[-1][0]}",
          method == "ttm" and series[0][0].year <= 2010 and len(series) > 60,
          "storia ancora troncata")
    got_date, got = series[-1]
    check(f"SCCO: ultimo TTM {got:+.2f}$ @ {got_date}",
          abs(got - 6.85) < 0.02 and str(got_date) == "2026-06-30",
          "atteso +6,85$ @ 2026-06-30")

    # Il riempimento e' SOLO nei buchi: dove il concetto scelto ha un dato, quel
    # dato resta. Se cosi' non fosse, le due misure — utile totale e utile delle
    # attivita' continuative — si mescolerebbero e la serie avrebbe un gradino.
    # Il confronto e' per TIPO di periodo: un concetto secondario che porta il
    # Q4 dove il principale ha solo l'esercizio intero non e' una sovrapposizione,
    # e' proprio il buco che si voleva riempire.
    primary = facts[0]["concept"]
    ends = {(_period_bucket(f["dur"]), f["end"])
            for f in facts if f["concept"] == primary}
    intruders = [f for f in facts if f["concept"] != primary
                 and (_period_bucket(f["dur"]), f["end"]) in ends]
    check("nessun concetto secondario dove il principale ha gia' il periodo",
          not intruders, f"{len(intruders)} periodi contesi")

    # Controprova su una societa' sana: riempire i buchi non deve aggiungere
    # nulla dove non ci sono buchi.
    for ticker, expected in (("WM", 6.91), ("NKE", 2.10), ("QCOM", 9.32)):
        series, _ = build_ttm_eps(extract_eps_facts(load(ticker)))
        check(f"{ticker}: ultimo TTM invariato ({series[-1][1]:+.2f}$)",
              abs(series[-1][1] - expected) < 0.02)


def main() -> None:
    print("=" * 72)
    print("REGRESSIONE TTM — dati SEC reali congelati al 2026-07-29")
    print("=" * 72)
    test_ttm_values()
    test_ttm_windows_are_twelve_months()
    test_q4_is_reconstructed()
    test_no_ttm_across_a_gap()
    test_tag_selection_by_recency()
    test_stale_series_rejected()
    test_derived_eps_for_multiclass()
    test_derived_eps_is_proportional()
    test_weekly_calendar_duplicates()
    test_concept_gaps_are_filled()

    print("\n" + "=" * 72)
    if failures:
        print(f"❌ {len(failures)} VERIFICHE FALLITE")
        for f in failures:
            print(f"   · {f}")
        print("=" * 72)
        sys.exit(1)
    print("✅ TUTTE LE VERIFICHE PASSATE")
    print("=" * 72)


if __name__ == "__main__":
    main()
