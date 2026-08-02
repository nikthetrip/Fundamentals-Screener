#!/usr/bin/env python3
"""
fix_dividends.py — Ricalcola il rendimento da dividendo del dataset esistente.

PERCHE' ESISTE. Il rendimento veniva dal campo `dividendRate` di yfinance, che
cambia significato proprio dove la risposta conta: dopo un taglio resta la somma
dei quattro stacchi vecchi, e con una cedola straordinaria la ingloba come se
tornasse ogni anno (vedi `data_sources.dividend_profile`). La correzione e' nel
build, ma rifare l'intera build significa riscaricare i companyfacts di mille
societa' — decine di gigabyte e un'ora buona — per sistemare una colonna che
dipende solo da yfinance.

Questo script scarica il SOLO registro dei dividendi e riscrive in place le
colonne che ne dipendono, lasciando intatto tutto il resto del file. E' un
rattoppo dichiarato: alla prossima build completa il risultato e' identico,
perche' il conto e' esattamente lo stesso.

Uso:
    python fix_dividends.py                 # scrive data/fundamentals.csv
    python fix_dividends.py --dry-run       # mostra soltanto cosa cambierebbe
    python fix_dividends.py --workers 8
"""

from __future__ import annotations

import argparse
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from data_sources import dividend_profile, yf_call

DATA_DIR = Path(os.environ.get("LYNCH_DATA_DIR", "data"))

# Le colonne che questo script possiede. Tutte le altre non si toccano.
OWNED = ["dividend_yield", "dividend_rate", "dividend_frequency",
         "dividend_ttm", "dividend_ttm_yield_pct", "dividend_has_special"]


def _round(v, n=2):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, n)


def fetch_dividends(ticker: str) -> list[tuple] | None:
    """Solo la serie dei dividendi. None se la chiamata non riesce: in quel caso
    la riga resta com'e', che e' meglio di svuotarla per un errore di rete."""
    import yfinance as yf

    try:
        s = yf_call(lambda: yf.Ticker(ticker).dividends)
    except Exception:                                            # noqa: BLE001
        return None
    if s is None:
        return None
    out = []
    for idx, amount in s.items():
        try:
            out.append((idx.date(), float(amount)))
        except (AttributeError, TypeError, ValueError):
            continue
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="solo i primi N ticker")
    args = ap.parse_args()

    path = DATA_DIR / "fundamentals.csv"
    df = pd.read_csv(path)
    tickers = df["ticker"].tolist()
    if args.limit:
        tickers = tickers[:args.limit]
    print(f"→ {len(tickers)} ticker da controllare")

    prices = df.set_index("ticker")["current_price"].to_dict()
    done = {"n": 0}

    def work(t: str):
        divs = fetch_dividends(t)
        done["n"] += 1
        if done["n"] % 100 == 0:
            print(f"   {done['n']}/{len(tickers)}")
        if divs is None:
            return t, None
        return t, dividend_profile(divs, prices.get(t))

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        results = dict(ex.map(work, tickers))

    for col in OWNED:
        if col not in df.columns:
            df[col] = None

    changes = []
    for i, t in enumerate(df["ticker"]):
        prof = results.get(t)
        if prof is None:                       # rete fallita: si lascia com'e'
            continue
        old = df.at[i, "dividend_yield"]
        new = _round(prof["yield_pct"])
        df.at[i, "dividend_yield"] = new
        df.at[i, "dividend_rate"] = _round(prof["rate"], 4)
        df.at[i, "dividend_frequency"] = prof["frequency"]
        df.at[i, "dividend_ttm"] = _round(prof["ttm"], 4)
        df.at[i, "dividend_ttm_yield_pct"] = _round(prof["ttm_yield_pct"])
        df.at[i, "dividend_has_special"] = bool(prof["has_special"])
        o = None if pd.isna(old) else float(old)
        if (o is None) != (new is None) or (
                o is not None and new is not None and abs(o - new) > 0.05):
            changes.append({"ticker": t, "prima": o, "dopo": new,
                            "scarto_pp": None if (o is None or new is None)
                            else round(new - o, 2)})

    ch = pd.DataFrame(changes)
    print(f"\n→ {len(ch)} ticker con rendimento corretto oltre 0.05 pp")
    if not ch.empty:
        ch["abs"] = ch["scarto_pp"].abs()
        print(ch.sort_values("abs", ascending=False, na_position="first")
                .drop(columns="abs").head(40).to_string(index=False))

    if args.dry_run:
        print("\n(dry run: nessun file scritto)")
        return 0
    df.to_csv(path, index=False)
    print(f"\n✅ scritto {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
