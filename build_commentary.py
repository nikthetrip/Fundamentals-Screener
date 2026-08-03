#!/usr/bin/env python3
"""
build_commentary.py — I commenti di valutazione, calcolati dalla build.

CHE COSA SONO. Sotto ogni sezione della scheda finanziaria, la dashboard scrive
in parole cosa dicono quei numeri: il margine e' sceso di tre punti in
quattro anni, il debito netto vale sei anni di cassa libera, il ROIC sta sotto
il costo del capitale. Non sono opinioni sull'azienda — sono aritmetica sulle
cifre della pagina, prodotta da commentary.py, con un verdetto finale che e'
la somma pesata dei rilievi a favore e contro.

PERCHE' SI CALCOLANO QUI E NON SUL TELEFONO. commentary.py sono settecento
righe di regole: quando un margine in calo e' strutturale e quando e' rumore,
cosa e' normale in un settore e cosa non va letto alla lettera, quanto pesa
ogni rilievo nel saldo finale. Riscriverle in Swift significherebbe avere due
implementazioni della stessa cosa, e la seconda comincerebbe a divergere dalla
prima il giorno in cui qualcuno corregge una soglia. Qui gira il codice vero,
una volta per titolo, e nel database finisce il risultato.

Output: data/commentary.csv
  ticker, section, position, tone, weight, text
  piu' le righe di sintesi (section = "_verdict" e "_context").

Uso:
  python build_commentary.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

import commentary
from derived_metrics import derive_ratios
from metrics_catalog import (MIN_PEERS, fmt_metric, peer_medians,
                             peer_reference)


def rows_for(ticker: str, r: pd.Series, peers: pd.Series | None,
             annual: pd.DataFrame | None) -> list[dict]:
    """Tutti i rilievi di un titolo, sezione per sezione, piu' i verdetti."""
    out: list[dict] = []
    context = commentary._ctx(r.get("sector"))

    for section, findings_for in commentary.SECTIONS.items():
        try:
            findings = findings_for(r, peers, annual, context, fmt_metric)
        except Exception:                                    # noqa: BLE001
            # Un titolo con dati mancanti puo' far fallire una singola regola.
            # Perdere una sezione e' meglio che perdere l'intero dataset dei
            # commenti: le altre quattro restano, e la scheda lo dichiara.
            continue

        for i, f in enumerate(findings):
            out.append({"ticker": ticker, "section": section, "position": i,
                        "tone": f.tone, "weight": f.weight, "text": f.text})

        label, _icon, scale = commentary.verdict(findings)
        out.append({"ticker": ticker, "section": f"{section}:verdict",
                    "position": 0, "tone": "neutral", "weight": 0,
                    "text": f"{label} — {scale}"})

    # Il giudizio complessivo: la somma dei rilievi di TUTTE le sezioni, che e'
    # cosa diversa dalla media dei cinque verdetti — un rilievo strutturale
    # nella sezione del debito deve pesare anche quando le altre quattro
    # sezioni sono tranquille.
    everything = []
    for section, findings_for in commentary.SECTIONS.items():
        try:
            everything += findings_for(r, peers, annual, context, fmt_metric)
        except Exception:                                    # noqa: BLE001
            continue
    if everything:
        label, _icon, scale = commentary.verdict(everything)
        out.append({"ticker": ticker, "section": "_assessment", "position": 0,
                    "tone": "neutral", "weight": 0,
                    "text": f"{label} — {scale}"})

    if context.note:
        out.append({"ticker": ticker, "section": "_context", "position": 0,
                    "tone": "neutral", "weight": 0, "text": context.note})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data")
    args = ap.parse_args()

    data = Path(args.data)
    fund = derive_ratios(pd.read_csv(data / "fundamentals.csv", low_memory=False))
    annual_all = (pd.read_csv(data / "financials_annual.csv")
                  if (data / "financials_annual.csv").exists() else None)

    # Le stesse mediane della dashboard, con la stessa scelta del gruppo:
    # l'industria quando e' abbastanza numerosa, altrimenti il settore.
    ind = peer_medians(fund, "industry") if "industry" in fund.columns else pd.DataFrame()
    sec = peer_medians(fund, "sector") if "sector" in fund.columns else pd.DataFrame()

    rows: list[dict] = []
    for _, r in fund.iterrows():
        ticker = r["ticker"]
        peers, _label, _n = peer_reference(r, ind, sec)
        annual = None
        if annual_all is not None:
            sub = annual_all[annual_all["ticker"] == ticker]
            annual = sub if not sub.empty else None
        rows += rows_for(ticker, r, peers, annual)

    out = data / "commentary.csv"
    pd.DataFrame(rows, columns=["ticker", "section", "position", "tone",
                                "weight", "text"]).to_csv(out, index=False)

    findings = sum(1 for x in rows if not x["section"].startswith("_")
                   and ":verdict" not in x["section"])
    covered = len({x["ticker"] for x in rows})
    print(f"  · commentary.csv  {len(rows):,} righe · {findings:,} rilievi · "
          f"{covered} titoli su {len(fund)}")


if __name__ == "__main__":
    main()
