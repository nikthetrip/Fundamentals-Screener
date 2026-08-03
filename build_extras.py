#!/usr/bin/env python3
"""
build_extras.py — I dati che la dashboard scarica a runtime, portati nel dataset.

PERCHE' ESISTE. overview.py chiama Yahoo al momento in cui si apre una scheda
(descrizione dell'attivita', obiettivi degli analisti) e segments.py legge
l'XBRL del 10-K per la ripartizione dei ricavi. Sul portatile funziona: la
richiesta parte, la risposta arriva, la cache di Streamlit la tiene un'ora.

Da un'applicazione iOS no. Non per pigrizia: Yahoo protegge quelle API con un
cookie piu' un "crumb" che cambia senza preavviso, e yfinance esiste proprio
perche' quel meccanismo va inseguito di continuo. Reimplementarlo in Swift
significa avere due implementazioni da tenere allineate — e quella sul telefono
si aggiorna solo quando l'utente aggiorna l'app. Meglio farlo una volta qui,
dove la libreria che se ne occupa e' gia' installata e gira ogni notte.

DUE COSTI MOLTO DIVERSI, quindi due comportamenti diversi:

  profili e analisti   una chiamata leggera per titolo. Su mille societa' sono
                       pochi minuti. Attivo per impostazione predefinita.

  segmenti             l'XBRL completo dell'ultimo bilancio: fra 1 e 15 MB a
                       societa'. Su mille societa' sono alcuni gigabyte e ore
                       di lavoro, ogni notte, per un dato che cambia UNA VOLTA
                       L'ANNO quando esce il bilancio. Quindi: opzionale
                       (--segments) e con una cache per numero di deposito, che
                       rende gratuite tutte le esecuzioni successive alla prima
                       finche' quel bilancio non cambia.

Output in data/:
  - profile.csv    descrizione, sede, dipendenti, sito, target degli analisti
  - segments.csv   ricavi per divisione / area / prodotto (solo con --segments)

Uso:
  python build_extras.py
  python build_extras.py --universe sp500 --workers 6
  python build_extras.py --segments            # anche i segmenti (lento)
"""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

# Le colonne del profilo, nell'ordine in cui finiscono nel CSV. Sono quelle che
# overview.py legge da `info`: se se ne aggiunge una qui, la scheda iOS puo'
# mostrarla senza altre modifiche alla pipeline.
PROFILE_FIELDS = [
    "long_name", "summary", "sector", "industry", "country", "city", "state",
    "website", "employees", "currency", "quote_type",
    "target_low", "target_mean", "target_median", "target_high",
    "n_analysts", "recommendation", "recommendation_mean",
]


def fetch_profile(ticker: str) -> dict | None:
    """Descrizione dell'attivita' e consenso degli analisti, in una riga."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}
    except Exception:                                        # noqa: BLE001
        return None
    if not info.get("longBusinessSummary") and not info.get("longName"):
        return None

    targets = {}
    try:
        targets = t.analyst_price_targets or {}
    except Exception:                                        # noqa: BLE001
        pass

    def num(*keys):
        for k in keys:
            v = targets.get(k) if k in targets else info.get(k)
            try:
                f = float(v)
                if f == f and f > 0:
                    return f
            except (TypeError, ValueError):
                continue
        return None

    return {
        "ticker": ticker,
        "long_name": info.get("longName"),
        # I riassunti di Yahoo arrivano fino a 3.000 caratteri. Sul telefono
        # nessuno li legge interi, ma tagliarli qui vorrebbe dire troncare a
        # meta' frase: si tengono per intero e li impagina la scheda.
        "summary": info.get("longBusinessSummary"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "country": info.get("country"),
        "city": info.get("city"),
        "state": info.get("state"),
        "website": info.get("website"),
        "employees": info.get("fullTimeEmployees"),
        "currency": info.get("currency"),
        "quote_type": info.get("quoteType"),
        "target_low": num("low", "targetLowPrice"),
        "target_mean": num("mean", "targetMeanPrice"),
        "target_median": num("median", "targetMedianPrice"),
        "target_high": num("high", "targetHighPrice"),
        "n_analysts": info.get("numberOfAnalystOpinions"),
        "recommendation": info.get("recommendationKey"),
        "recommendation_mean": info.get("recommendationMean"),
    }


def fetch_segments_rows(ticker: str, cik: str, cache: dict) -> list[dict]:
    """
    La ripartizione dei ricavi, appiattita in righe.

    LA CACHE E' PER NUMERO DI DEPOSITO, non per data. Un 10-K depositato non
    cambia mai: se l'accession registrato e' ancora l'ultimo, il documento
    scaricato l'anno scorso e' ancora quello giusto e non c'e' niente da
    riscaricare. Con una scadenza a tempo, invece, ogni notte si riscaricherebbe
    lo stesso file per scoprire che non e' cambiato.
    """
    import segments as segments_mod

    hit = cache.get(ticker)
    if hit is not None:
        return hit.get("rows", [])

    try:
        data = segments_mod.fetch_segments(ticker, cik)
    except Exception:                                        # noqa: BLE001
        data = None
    if not data:
        cache[ticker] = {"rows": []}
        return []

    meta = data.get("_meta", {})
    rows = []
    for axis, block in data.items():
        if axis == "_meta":
            continue
        for period_end, members in (block.get("periods") or {}).items():
            total = (block.get("total") or {}).get(period_end)
            for member, value in members.items():
                rows.append({
                    "ticker": ticker,
                    "axis": axis,
                    "title": block.get("title"),
                    "note": block.get("note"),
                    "word": block.get("word"),
                    "period_end": period_end,
                    "member": member,
                    "value": value,
                    "total": total,
                    "reliable": bool(block.get("reliable")),
                    "accession": meta.get("accession"),
                    "filed": meta.get("filed"),
                    "source_url": meta.get("filing_index"),
                })
    cache[ticker] = {"accession": meta.get("accession"), "rows": rows}
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data", help="cartella dei CSV")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--segments", action="store_true",
                    help="scarica anche la ripartizione dei ricavi (lento)")
    ap.add_argument("--cache", default="data/.segments_cache.json",
                    help="cache dei segmenti, per numero di deposito")
    args = ap.parse_args()

    data_dir = Path(args.data)
    fundamentals = data_dir / "fundamentals.csv"
    if not fundamentals.exists():
        raise SystemExit(f"Manca {fundamentals}: genera prima il dataset.")

    df = pd.read_csv(fundamentals, dtype={"cik": str}, low_memory=False)
    tickers = df["ticker"].dropna().tolist()
    ciks = dict(zip(df["ticker"], df["cik"].fillna("")))
    print(f"{len(tickers)} titoli da arricchire")

    # --- profili -----------------------------------------------------------
    #
    # DUE PASSAGGI, NON UNO. Yahoo non risponde con un errore quando si chiede
    # troppo in fretta: risponde con un oggetto vuoto, che e' indistinguibile
    # da "questa societa' non ha un profilo". Al primo giro con sei thread ne
    # tornavano 653 su 990 — un terzo mancante che non sembrava un problema di
    # ritmo, sembrava un dataset incompleto. Il secondo giro ripassa solo i
    # falliti, piano, e recupera la maggior parte.
    def collect(symbols: list[str], workers: int, label: str) -> list[dict]:
        got = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(fetch_profile, t): t for t in symbols}
            for i, fut in enumerate(as_completed(futures), 1):
                row = fut.result()
                if row:
                    got.append(row)
                if i % 100 == 0:
                    print(f"  {label} {i}/{len(symbols)}")
        return got

    profiles = collect(tickers, args.workers, "profili")

    missing = sorted(set(tickers) - {p["ticker"] for p in profiles})
    if missing:
        print(f"  {len(missing)} senza profilo: secondo passaggio piu' lento")
        profiles += collect(missing, max(1, args.workers // 3), "ripasso")

    # --- si aggiunge a quello che c'e', non lo si sostituisce ---------------
    #
    # PERCHE' UNA FUSIONE E NON UNA RISCRITTURA. Yahoo non risponde con un
    # errore quando ha deciso che sono troppe richieste: risponde 401 "Invalid
    # Crumb" e yfinance restituisce un oggetto vuoto. Una serata storta produce
    # zero profili, e riscrivendo il file si cancellerebbero descrizioni
    # raccolte e valide — e' successo: 653 profili buoni sostituiti da un file
    # vuoto. Un profilo non e' un prezzo: la descrizione di un'attivita' di ieri
    # e' ancora giusta oggi. Quindi le righe nuove vincono sulle vecchie, e le
    # vecchie restano dove non sono arrivate righe nuove.
    out = data_dir / "profile.csv"
    fresh = pd.DataFrame(profiles, columns=["ticker"] + PROFILE_FIELDS)
    kept = 0
    if out.exists():
        try:
            old = pd.read_csv(out)
            if not old.empty:
                merged = pd.concat([fresh, old]).drop_duplicates(
                    subset="ticker", keep="first")
                kept = len(merged) - len(fresh)
                fresh = merged
        except Exception:                                    # noqa: BLE001
            pass

    fresh = fresh.sort_values("ticker")
    fresh[["ticker"] + PROFILE_FIELDS].to_csv(out, index=False)
    note = f" ({len(profiles)} nuovi, {kept} conservati)" if kept else ""
    print(f"  · profile.csv   {len(fresh)} righe{note}")
    if not profiles:
        print("    ATTENZIONE: nessun profilo nuovo — il provider ha rifiutato "
              "le richieste. Il file precedente non e' stato toccato.")

    # --- segmenti ----------------------------------------------------------
    if not args.segments:
        print("  · segments.csv  saltato (usa --segments)")
        return

    if not os.environ.get("SEC_USER_AGENT"):
        raise SystemExit("I segmenti vengono dalla SEC: serve SEC_USER_AGENT.")

    cache_path = Path(args.cache)
    cache = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
        except Exception:                                    # noqa: BLE001
            cache = {}

    rows: list[dict] = []
    # Un thread solo: la SEC chiede di restare sotto le dieci richieste al
    # secondo e ogni ticker qui ne fa tre, una delle quali scarica un documento
    # da diversi megabyte. Il limitatore sta in data_sources, ma parallelizzare
    # significherebbe accodarsi da soli.
    for i, ticker in enumerate(tickers, 1):
        rows.extend(fetch_segments_rows(ticker, str(ciks.get(ticker) or ""), cache))
        if i % 25 == 0:
            print(f"  segmenti {i}/{len(tickers)}")
            cache_path.write_text(json.dumps(cache))

    cache_path.write_text(json.dumps(cache))
    seg_out = data_dir / "segments.csv"
    columns = ["ticker", "axis", "title", "note", "word", "period_end",
               "member", "value", "total", "reliable", "accession", "filed",
               "source_url"]
    pd.DataFrame(rows or [], columns=columns).to_csv(seg_out, index=False)
    print(f"  · segments.csv  {len(rows)} righe")


if __name__ == "__main__":
    main()
