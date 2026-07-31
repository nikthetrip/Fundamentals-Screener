#!/usr/bin/env python3
"""
audit_dataset.py — Verifica che la classificazione Lynch regga su TUTTI i
ticker del dataset, non su quelli che si è pensato di controllare a mano.

PERCHE' ESISTE. Su mille società nessuno può ispezionare i singoli casi, e un
difetto della meccanica non si manifesta come un errore: si manifesta come un
numero plausibile e sbagliato. Questo script cerca CLASSI di difetto, in tre
modi che non richiedono di conoscere la risposta giusta per ogni società:

  1. INVARIANTI — l'esito rispetta la condizione del ramo da cui esce?
     Una "Fast Grower" con crescita del 4%, una "Ciclica" con P/E diverso da 12,
     un multiplo su utili negativi: sono contraddizioni interne, verificabili
     senza sapere quanto valga davvero il titolo.

  2. COPERTURA E PLAUSIBILITA' — quanti titoli ricevono un fair value, e quelli
     che non lo ricevono hanno una ragione dichiarata? Un fair value soppresso e
     uno mai calcolato lasciano la stessa cella vuota ma sono cose diverse.

  3. CONTINUITA' E SENSIBILITA' — è la prova più severa. Il fair value deve
     essere una funzione continua della crescita: se spostare la crescita di due
     decimi di punto sposta la valutazione del 30%, il modello sta rispondendo
     al rumore della misura e non ai dati. Ha già trovato due difetti reali —
     un'inversione sul confine del 10% (passare nella categoria migliore faceva
     SCENDERE il fair value) e un gradino del 33% dove cambiava la base.

Uso:
    python audit_dataset.py                # audita data/
    python audit_dataset.py --data altro/  # audita un'altra cartella

Esce con codice 1 se un invariante è violato: è pensato per girare anche in CI,
dopo la build e prima del commit dei dati.
"""

from __future__ import annotations
import argparse
import sys
from pathlib import Path

import pandas as pd

from edgar_logic import (
    classify_lynch, growth_estimate, loss_profile, earnings_volatility,
    normalized_eps, fair_value_check,
    CYCLICAL_SECTORS, NON_CYCLICAL_INDUSTRIES, CYCLICAL_VOL_THRESHOLD,
    FAST_GROWER_PE_CAP, SLOW_GROWER_PE_FLOOR, SLOW_GROWER_PE_CAP,
    CYCLICAL_PE, TURNAROUND_RECOVERY_PE, STALWART_MIN_GROWTH,
    FAST_GROWER_MIN_GROWTH,
)

NO_LOSS = {"any": False, "current": False, "periods": 0, "share": 0.0,
           "quarters_since": None, "episodes": 0}


def _num(v):
    return None if v is None or pd.isna(v) else float(v)


def rebuild(data_dir: Path) -> pd.DataFrame:
    """
    Riapplica la classificazione a ogni ticker partendo dalle serie EPS sul
    disco. Non tocca la rete: la serie TTM è già in history.csv.gz, quindi
    l'audit gira in pochi secondi su qualunque dataset già costruito.
    """
    hist_path = next((data_dir / n for n in ("history.csv.gz", "history.csv")
                      if (data_dir / n).exists()), None)
    if hist_path is None:
        sys.exit(f"Nessuno storico in {data_dir}: lancia prima build_dataset.py")
    h = pd.read_csv(hist_path, parse_dates=["eps_date"]).dropna(
        subset=["eps_date", "eps"])
    u = (h.drop_duplicates(subset=["ticker", "eps_date"])
           .sort_values(["ticker", "eps_date"]))
    fund = pd.read_csv(data_dir / "fundamentals.csv").set_index("ticker")

    rows = []
    for t, g in u.groupby("ticker"):
        if t not in fund.index:
            continue
        r = fund.loc[t]
        s = list(zip(g.eps_date.dt.date, g.eps.astype(float)))
        ge, lp = growth_estimate(s), loss_profile(s, 5)
        nrm, vol = normalized_eps(s, 3), earnings_volatility(s)
        c = classify_lynch(
            growth_pct=ge["value"], eps_now=_num(r.get("eps_ttm")), volatility=vol,
            sector=r.get("sector"), dividend_yield=_num(r.get("dividend_yield")),
            price_to_book=_num(r.get("price_to_book")),
            market_cap=_num(r.get("market_cap")), losses=lp, eps_normalized=nrm,
            growth_basis=ge["basis"], growth_confidence=ge["confidence"],
            rebound_risk=ge["rebound_risk"], industry=r.get("industry"),
            classification_source=r.get("classification_source", "yfinance"))

        base = fv = None
        if c["anchor"] == "book":
            bv = _num(r.get("book_value_per_share"))
            if bv and bv > 0 and c["fair_pb"]:
                fv = bv * c["fair_pb"]
        elif c["anchor"] == "earnings" and c["fair_pe"]:
            base = nrm if c["eps_base"] == "normalized" else _num(r.get("eps_ttm"))
            if base and base > 0:
                fv = base * c["fair_pe"]
        price = _num(r.get("current_price"))
        ok, why = fair_value_check(fv, price, base, c["anchor"])

        rows.append(dict(
            ticker=t, company=r.get("company"), sector=r.get("sector"),
            industry=r.get("industry"), clsrc=r.get("classification_source"),
            category=c["category"], fair_pe=c["fair_pe"], fair_pb=c["fair_pb"],
            anchor=c["anchor"], eps_base=c["eps_base"], confidence=c["confidence"],
            growth=ge["value"], rebound=ge["rebound_risk"], vol=vol,
            eps=_num(r.get("eps_ttm")), nrm=nrm, ptb=_num(r.get("price_to_book")),
            price=price, base=base, fv=fv, fv_ok=ok, fv_why=why,
            loss_any=lp["any"], loss_cur=lp["current"], loss_ep=lp["episodes"],
            since=lp["quarters_since"]))
    return pd.DataFrame(rows)


def check_invariants(d: pd.DataFrame) -> int:
    """Ogni esito rispetta la condizione del ramo che l'ha prodotto?"""
    print("=" * 74)
    print("1. INVARIANTI DI RAMO")
    print("=" * 74)
    isc = d.category.str.startswith
    checks = [
        ("Fast Grower con crescita sotto la soglia",
         (d.category == "Fast Grower") & (d.growth.fillna(-9) <= FAST_GROWER_MIN_GROWTH)),
        (f"Fast Grower con P/E oltre il cap ({FAST_GROWER_PE_CAP:.0f})",
         (d.category == "Fast Grower") & (d.fair_pe.fillna(0) > FAST_GROWER_PE_CAP)),
        ("Fast Grower nata da un rimbalzo",
         (d.category == "Fast Grower") & d.rebound),
        ("Stalwart fuori dalla fascia di crescita",
         isc("Stalwart", na=False) & ~d.category.str.contains("recovering", na=False)
         & ((d.growth.fillna(0) < STALWART_MIN_GROWTH)
            | (d.growth.fillna(0) > FAST_GROWER_MIN_GROWTH))),
        (f"Slow Grower con P/E fuori da [{SLOW_GROWER_PE_FLOOR:.0f},{SLOW_GROWER_PE_CAP:.0f}]",
         isc("Slow Grower", na=False)
         & ((d.fair_pe < SLOW_GROWER_PE_FLOOR) | (d.fair_pe > SLOW_GROWER_PE_CAP))),
        ("Slow Grower non-declining con crescita negativa",
         (d.category == "Slow Grower") & (d.growth.fillna(0) < 0)),
        ("Slow Grower declining con crescita non negativa",
         (d.category == "Slow Grower (declining earnings)") & (d.growth.fillna(-1) >= 0)),
        (f"Cyclical con P/E diverso da {CYCLICAL_PE:.0f}",
         (d.category == "Cyclical") & (d.fair_pe != CYCLICAL_PE)),
        ("Cyclical fuori da un settore ciclico",
         (d.category == "Cyclical") & ~d.sector.isin(CYCLICAL_SECTORS)),
        ("Cyclical in un'industria esclusa",
         (d.category == "Cyclical") & d.industry.isin(NON_CYCLICAL_INDUSTRIES)),
        ("Cyclical su un settore indovinato dal SIC",
         (d.category == "Cyclical")
         & d.clsrc.astype(str).str.startswith(("sic", "none"))),
        ("Cyclical con volatilita' sotto soglia",
         (d.category == "Cyclical") & (d.vol.fillna(0) <= CYCLICAL_VOL_THRESHOLD)),
        ("Asset Play con price/book >= 1",
         (d.category == "Asset Play") & (d.ptb.fillna(9) >= 1)),
        ("Asset Play senza ancora sul patrimonio",
         (d.category == "Asset Play") & (d.anchor != "book")),
        ("Turnaround senza alcuna perdita nella finestra",
         (d.category == "Turnaround") & ~d.loss_any),
        (f"Turnaround con P/E diverso da {TURNAROUND_RECOVERY_PE:.0f}",
         (d.category == "Turnaround") & d.fair_pe.notna()
         & (d.fair_pe != TURNAROUND_RECOVERY_PE)),
        ("Unclassified con una crescita invece calcolabile",
         (d.category == "Unclassified") & d.growth.notna()),
        ("multiplo applicato a utili correnti non positivi",
         (d.anchor == "earnings") & (d.eps_base == "current") & (d.eps.fillna(1) <= 0)),
        ("multiplo applicato a utili normalizzati non positivi",
         (d.anchor == "earnings") & (d.eps_base == "normalized") & (d.nrm.fillna(1) <= 0)),
        ("fair P/E negativo o nullo", d.fair_pe.notna() & (d.fair_pe <= 0)),
        ("fair value mostrato senza ancora", d.fv_ok & (d.anchor == "none")),
    ]
    failed = 0
    for label, mask in checks:
        n = int(mask.sum())
        failed += n
        print(f"  {'FAIL' if n else ' ok '}  {label:58s} {n:4d}")
        if n:
            for _, x in d[mask].head(3).iterrows():
                print(f"          {x.ticker:6s} {str(x.company)[:24]:26s} "
                      f"{x.category:30s} P/E={x.fair_pe} g={x.growth}")
    print(f"\n  violazioni totali: {failed}")
    return failed


def report_coverage(d: pd.DataFrame) -> None:
    print("\n" + "=" * 74)
    print("2. COPERTURA E PLAUSIBILITA' DEL FAIR VALUE")
    print("=" * 74)
    n = len(d)
    shown = int(d.fv_ok.sum())
    print(f"  fair value di categoria mostrato : {shown:4d} / {n}  "
          f"({shown / n * 100:.0f}%)")
    print(f"  assente                          : {n - shown:4d}")
    print("\n  ragione, quando manca:")
    why = (d.loc[~d.fv_ok, "fv_why"]
           .str.replace(r"[\d,.]+", "N", regex=True).str.slice(0, 56))
    for k, v in why.value_counts().items():
        print(f"    {v:4d}  {k}")
    r = d[d.fv_ok].assign(ratio=lambda x: x.fv / x.price)
    print("\n  fair value / prezzo di cio' che resta:")
    for q in (0.01, 0.25, 0.5, 0.75, 0.99):
        print(f"    {q:>5.0%}  {r.ratio.quantile(q):.2f}")
    print("\n  quota con fair value, per categoria:")
    g = d.groupby("category").fv_ok.agg(["size", "sum"])
    for cat, row in g.iterrows():
        print(f"    {cat:34s} {int(row['sum']):4d}/{int(row['size']):4d}  "
              f"{row['sum'] / row['size'] * 100:3.0f}%")
    print("\n  confidenza dichiarata:")
    for k, v in d.confidence.value_counts().items():
        print(f"    {k:8s} {v:4d}  ({v / n * 100:.0f}%)")


def check_continuity() -> int:
    """
    Il fair value deve essere una funzione CONTINUA della crescita.

    Si spazza la crescita a passi di un decimo di punto attraverso tutti i
    confini fra categorie, tenendo fisso tutto il resto, e si misura il salto.
    Un gradino qui significa che due societa' identiche a meno del rumore di
    misura ricevono valutazioni molto diverse.
    """
    print("\n" + "=" * 74)
    print("3. CONTINUITA' DEL FAIR VALUE RISPETTO ALLA CRESCITA")
    print("=" * 74)
    worst, prev = [], None
    for i in range(-150, 401):
        g = i / 10
        c = classify_lynch(growth_pct=g, eps_now=4.0, volatility=20,
                           sector="Technology", dividend_yield=2.0,
                           price_to_book=3, market_cap=1e11, losses=NO_LOSS,
                           eps_normalized=3.0)
        base = 3.0 if c["eps_base"] == "normalized" else 4.0
        fv = base * c["fair_pe"] if c["fair_pe"] else None
        if prev and fv and prev[1]:
            jump = abs(fv / prev[1] - 1) * 100
            if jump > 3.0:
                worst.append((prev[0], g, prev[1], fv, jump))
        prev = (g, fv)
    if worst:
        print("  FAIL  salti oltre il 3% per 0,1 punti di crescita:")
        for a, b, fa, fb, j in worst:
            print(f"        {a:.1f}% -> {b:.1f}%: {fa:.2f} -> {fb:.2f}  ({j:+.0f}%)")
    else:
        print("   ok   nessun salto oltre il 3% su tutto l'intervallo -15% .. +40%")
    return len(worst)


def report_sensitivity(data_dir: Path, d: pd.DataFrame) -> None:
    """
    Quanti titoli cambiano categoria per una perturbazione minima degli input?

    Non è un test che si supera o si fallisce — i confini esistono, quindi
    qualcuno ci sta sempre sopra — ma la quota va conosciuta e sorvegliata:
    se cresce, vuol dire che il modello sta diventando sensibile al rumore.
    """
    print("\n" + "=" * 74)
    print("4. SENSIBILITA' DELLA CATEGORIA A PICCOLE PERTURBAZIONI")
    print("=" * 74)
    fund = pd.read_csv(data_dir / "fundamentals.csv").set_index("ticker")
    base_cat = d.set_index("ticker").category

    def run(dg=0.0, dv=0.0):
        out = {}
        for x in d.itertuples():
            r = fund.loc[x.ticker]
            c = classify_lynch(
                growth_pct=None if x.growth is None or pd.isna(x.growth) else x.growth + dg,
                eps_now=x.eps,
                volatility=None if x.vol is None or pd.isna(x.vol) else x.vol * (1 + dv),
                sector=x.sector, dividend_yield=_num(r.get("dividend_yield")),
                price_to_book=x.ptb, market_cap=_num(r.get("market_cap")),
                losses={"any": x.loss_any, "current": x.loss_cur, "periods": 0,
                        "share": 0.0, "quarters_since": x.since,
                        "episodes": x.loss_ep},
                eps_normalized=x.nrm, rebound_risk=x.rebound,
                industry=x.industry, classification_source=x.clsrc)
            out[x.ticker] = c["category"]
        return pd.Series(out)

    for label, kw in (("crescita +1 punto", dict(dg=1)),
                      ("crescita -1 punto", dict(dg=-1)),
                      ("volatilita' +10%", dict(dv=0.10)),
                      ("volatilita' -10%", dict(dv=-0.10))):
        changed = int((run(**kw) != base_cat).sum())
        print(f"  {label:24s} {changed:4d} titoli cambiano categoria "
              f"({changed / len(d) * 100:4.1f}%)")
    print("\n  NB: un titolo che cambia ETICHETTA non cambia necessariamente "
          "valutazione:\n      il multiplo è continuo attraverso i confini "
          "(vedi il punto 3).")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data", help="cartella dei CSV (default: data)")
    ap.add_argument("--csv", help="salva l'audit completo in questo file")
    args = ap.parse_args()

    data_dir = Path(args.data)
    print(f"Audit del dataset in {data_dir.resolve()}\n")
    d = rebuild(data_dir)
    print(f"{len(d)} ticker riclassificati dalle serie EPS sul disco.\n")

    failed = check_invariants(d)
    report_coverage(d)
    failed += check_continuity()
    report_sensitivity(data_dir, d)

    if args.csv:
        d.to_csv(args.csv, index=False)
        print(f"\nAudit completo scritto in {args.csv}")

    print("\n" + "=" * 74)
    if failed:
        print(f"❌ AUDIT FALLITO — {failed} problemi strutturali")
    else:
        print("✅ AUDIT SUPERATO — nessuna violazione di invariante, "
              "nessuna discontinuità")
    print("=" * 74)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
