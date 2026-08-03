#!/usr/bin/env python3
"""
build_mobile_db.py — Impacchetta i CSV di data/ in un unico SQLite per l'app iOS.

PERCHE' ESISTE. La dashboard Streamlit gira su una macchina che ha i CSV sul
disco accanto: puo' permettersi di caricare in memoria 658.000 righe di storico
e filtrarle con pandas. Un telefono no. E soprattutto un telefono non ha i file
accanto: deve scaricarli, e sei richieste HTTP su rete mobile sono sei modi
diversi di fallire a meta'.

Quindi un file solo, sempre lo stesso indirizzo, con dentro tutto. SQLite
perche' e' gia' dentro iOS (nessuna dipendenza da aggiungere), perche' con un
indice su `ticker` la scheda di un titolo si apre leggendo le sue duecento
righe invece di scorrere le seicentomila, e perche' resta un file: si scarica,
si mette in cache, e finche' non arriva il successivo l'app funziona offline.

NON RICALCOLA NIENTE. Ogni numero qui dentro viene dai CSV che build_dataset.py
ha gia' prodotto e che audit_dataset.py ha gia' verificato. Se questo script
cominciasse a derivare valori propri, esisterebbero due implementazioni della
stessa logica — quella della dashboard e quella del telefono — e prima o poi
mostrerebbero due fair value diversi per lo stesso titolo.

Output in data/:
  - screener.sqlite      il database (utile per ispezionarlo a mano)
  - screener.sqlite.gz   quello che scarica l'app

Uso:
  python build_mobile_db.py
  python build_mobile_db.py --data data --out data/screener.sqlite
"""

from __future__ import annotations

import argparse
import gzip
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from derived_metrics import derive_ratios

# La versione dello schema viaggia dentro il database. L'app la controlla
# all'apertura: se il file scaricato dichiara uno schema che non conosce,
# preferisce tenersi quello vecchio in cache piuttosto che aprire un database
# le cui colonne non corrispondono piu' a quello che si aspetta di leggere.
SCHEMA_VERSION = 3

# Le tabelle piccole: una riga per ticker, o poche decine. Vanno nel database
# come sono, con un indice su `ticker` perche' e' sempre da li' che si entra.
# Lo storico non e' in questa lista: ha bisogno di un trattamento suo, sotto.
TABLES: list[tuple[str, str, list[str]]] = [
    ("fundamentals",      "fundamentals.csv",      ["ticker"]),
    ("financials_annual", "financials_annual.csv", ["ticker"]),
    ("filings",           "filings.csv",           ["ticker"]),
    ("events",            "events.csv",            ["ticker"]),
    ("cagr_detail",       "cagr_detail.csv",       ["ticker"]),
    ("skipped",           "skipped.csv",           []),
    # Prodotte da build_extras.py. Possono mancare — la scheda iOS mostra le
    # sezioni che ci sono e dichiara quelle che no.
    ("profile",           "profile.csv",           ["ticker"]),
    ("segments",          "segments.csv",          ["ticker"]),
    # Prodotta da build_commentary.py: i rilievi di commentary.py, gia'
    # calcolati. L'indice e' su (ticker, section) perche' la scheda ne chiede
    # una sezione alla volta.
    ("commentary",        "commentary.csv",        ["ticker", "ticker,section"]),
]

# Colonne che nei CSV sono date e che nel database restano testo ISO. SQLite non
# ha un tipo data, e il testo ISO si ordina e si confronta correttamente proprio
# perche' e' ISO: qualunque altra formattazione romperebbe gli ordinamenti.
DATE_COLUMNS = {"date", "eps_date", "fy_end", "filing_date", "period_date",
                "eps_last_date", "next_earnings_date", "start_date", "end_date"}


def _read(path: Path) -> pd.DataFrame:
    """Legge un CSV, compresso o no, senza inventare tipi."""
    return pd.read_csv(path, low_memory=False)


def _normalise_dates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Riporta ogni colonna di date alla forma YYYY-MM-DD.

    I CSV arrivano gia' cosi', ma non tutti e non sempre: `next_earnings_date`
    passa da yfinance e ogni tanto porta con se' un orario. Una colonna in cui
    il 99% dei valori e' lungo dieci caratteri e il resto diciannove si ordina
    ancora bene, ma confrontata con `date('now')` dentro l'app smette di
    corrispondere. Meglio tagliare qui, una volta, che ricordarsene in Swift.
    """
    for col in df.columns:
        if col in DATE_COLUMNS and df[col].dtype == object:
            df[col] = df[col].astype(str).str.slice(0, 10).replace(
                {"nan": None, "NaT": None, "None": None, "": None})
    return df


def _build_history(con: sqlite3.Connection, path: Path) -> int:
    """
    Lo storico, che da solo e' il 90% del database, scritto in forma compatta.

    LE TRE SCELTE E IL LORO PERCHE'. Riversato cosi' com'e' il CSV occupa 55 MB
    sul telefono, e occuparne 55 per 658.000 righe di quattro numeri vuol dire
    che quasi tutto lo spazio non e' dati.

    1) `WITHOUT ROWID` con chiave primaria (ticker, date). La tabella *e'* il
       suo indice, invece di essere una copia piu' un indice che ne duplica le
       colonne: l'indice su (ticker, date) qui pesava quanto la tabella.
    2) Le date come interi YYYYMMDD. Si ordinano e si confrontano come il testo
       ISO — e' proprio per questo che YYYYMMDD funziona — ma stanno in quattro
       byte invece di undici.
    3) `eps_date` come intero per la stessa ragione. Serve a una cosa sola
       (sapere quanto e' vecchia l'ultima perdita) ma sta su ogni riga.

    Il ticker resta testo. Sostituirlo con un intero e una tabella di
    corrispondenza risparmierebbe altri pochi megabyte al prezzo di una join in
    ogni query dell'app: non vale il cambio.
    """
    con.execute("""
        CREATE TABLE history (
            ticker   TEXT    NOT NULL,
            date     INTEGER NOT NULL,   -- YYYYMMDD
            price    REAL,
            eps      REAL,
            eps_date INTEGER,            -- YYYYMMDD
            PRIMARY KEY (ticker, date)
        ) WITHOUT ROWID
    """)

    def _as_int_date(s: pd.Series) -> pd.Series:
        d = pd.to_datetime(s, errors="coerce")
        return (d.dt.year * 10000 + d.dt.month * 100 + d.dt.day).astype("Int64")

    total = 0
    # A pezzi: il CSV compresso sta in memoria senza problemi su un portatile,
    # ma questo script gira anche dentro una Action con due giga di RAM.
    for chunk in pd.read_csv(path, chunksize=200_000, low_memory=False):
        chunk["date"] = _as_int_date(chunk["date"])
        chunk["eps_date"] = _as_int_date(chunk["eps_date"])
        chunk = chunk.dropna(subset=["date"])
        con.executemany(
            "INSERT OR REPLACE INTO history VALUES (?, ?, ?, ?, ?)",
            chunk[["ticker", "date", "price", "eps", "eps_date"]]
            .astype(object).where(pd.notna(chunk[["ticker", "date", "price",
                                                  "eps", "eps_date"]]), None)
            .itertuples(index=False, name=None))
        total += len(chunk)
    return total


def build(data_dir: Path, out: Path) -> Path:
    if out.exists():
        out.unlink()

    con = sqlite3.connect(out)
    # Il database e' di sola lettura sul telefono: non serve un journal, e
    # toglierlo evita di spedire un file -wal accanto a quello principale.
    con.execute("PRAGMA journal_mode = OFF")
    con.execute("PRAGMA synchronous = OFF")

    counts: dict[str, int] = {}
    for table, filename, indexes in TABLES:
        path = data_dir / filename
        if not path.exists():
            print(f"  · {table:18s} assente ({filename}) — tabella vuota")
            continue

        df = _normalise_dates(_read(path))

        # I rapporti che il dataset non porta gia' pronti — ROIC, conversione
        # in cassa, payout, earnings yield — si aggiungono QUI, con la stessa
        # funzione che usa la dashboard. Aggiungerli alla tabella invece di
        # calcolarli sul telefono ha una ragione precisa: le mediane di
        # industria si costruiscono sulle colonne, e un rapporto che arriva
        # dopo sarebbe l'unico senza confronto con i pari.
        if table == "fundamentals":
            df = derive_ratios(df)

        df.to_sql(table, con, index=False, if_exists="replace")
        counts[table] = len(df)

        for spec in indexes:
            name = f"idx_{table}_{spec.replace(',', '_')}"
            con.execute(f"CREATE INDEX {name} ON {table} ({spec})")

        print(f"  · {table:18s} {len(df):>7,} righe")

    history_path = data_dir / "history.csv.gz"
    if history_path.exists():
        counts["history"] = _build_history(con, history_path)
        print(f"  · {'history':18s} {counts['history']:>7,} righe")
    else:
        print("  · history            assente — grafici di valutazione vuoti")

    # --- meta ----------------------------------------------------------------
    # Una tabella chiave/valore invece di colonne fisse: quando servira'
    # aggiungere un dato di servizio, si aggiunge una riga e i lettori vecchi
    # continuano a funzionare. Con una colonna nuova, no.
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    fundamentals_path = data_dir / "fundamentals.csv"
    built_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    data_mtime = (
        datetime.fromtimestamp(fundamentals_path.stat().st_mtime, timezone.utc)
        .replace(microsecond=0).isoformat()
        if fundamentals_path.exists() else built_at)

    meta = {
        "schema_version": str(SCHEMA_VERSION),
        "built_at": built_at,
        "data_generated_at": data_mtime,
        **{f"rows_{k}": str(v) for k, v in counts.items()},
    }
    con.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", meta.items())

    con.commit()
    # VACUUM dopo gli indici: ricompatta il file e toglie le pagine libere
    # lasciate dalle scritture. Su questo dataset vale qualche megabyte, che su
    # rete mobile e' la differenza fra un avvio e un'attesa.
    con.execute("VACUUM")
    con.close()

    gz = out.with_suffix(out.suffix + ".gz")
    with open(out, "rb") as src, gzip.open(gz, "wb", compresslevel=9) as dst:
        shutil.copyfileobj(src, dst)

    print(f"\n  {out.name}     {out.stat().st_size / 1e6:6.1f} MB")
    print(f"  {gz.name}  {gz.stat().st_size / 1e6:6.1f} MB  (scaricato dall'app)")
    return gz


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data", help="cartella dei CSV")
    ap.add_argument("--out", default="data/screener.sqlite",
                    help="percorso del database da scrivere")
    args = ap.parse_args()

    data_dir = Path(args.data)
    if not (data_dir / "fundamentals.csv").exists():
        raise SystemExit(
            f"Manca {data_dir}/fundamentals.csv: genera prima il dataset con\n"
            f"  python build_dataset.py --universe sp500+russell1000")

    print(f"Impacchetto {data_dir}/ per l'app iOS")
    build(data_dir, Path(args.out))


if __name__ == "__main__":
    main()
