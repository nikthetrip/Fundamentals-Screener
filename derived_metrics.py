#!/usr/bin/env python3
"""
derived_metrics.py — I rapporti che il dataset non porta gia' pronti.

PERCHE' STA IN UN MODULO SUO. Questo codice viveva dentro app.py, ed e' andato
bene finche' l'unico lettore del dataset era la dashboard. Con l'app iOS i
lettori sono due, e un rapporto calcolato solo dentro app.py e' un rapporto che
sul telefono non esiste — oppure, peggio, che sul telefono viene ricalcolato in
Swift da un secondo pezzo di codice destinato a divergere da questo alla prima
correzione.

Quindi una funzione sola, importata da app.py per la dashboard e da
build_mobile_db.py per il database dell'app. Il ROIC di Apple e' lo stesso
numero nei due posti perche' e' letteralmente la stessa riga di codice.

MEDIANE. La ragione per cui questi rapporti si aggiungono alla TABELLA e non si
calcolano al momento di disegnare una scheda e' che le mediane di industria si
costruiscono sulle colonne: un rapporto calcolato dopo sarebbe l'unico senza
confronto con i pari.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# Tassazione usata per il NOPAT del ROIC. Non e' l'aliquota effettiva della
# societa': quella richiederebbe le imposte depositate riga per riga, che il
# dataset non porta. E' l'aliquota federale statunitense, la stessa per tutti,
# dichiarata nel tooltip — un ROIC confrontabile fra societa' vale piu' di un
# ROIC "esatto" per una sola e incomparabile con le altre.
ROIC_TAX_RATE = 0.21


def _series(df: pd.DataFrame, col: str) -> pd.Series:
    """La colonna come numeri, o una colonna di NaN se non c'e'."""
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce")


def _ratio(num: pd.Series, den: pd.Series, *, positive_den: bool = True,
           scale: float = 1.0) -> pd.Series:
    """
    Divisione riga per riga che si rifiuta di produrre numeri privi di senso.

    Un denominatore ZERO o NEGATIVO non da' un rapporto interpretabile: un
    margine su ricavi negativi, un ROIC su capitale investito negativo (societa'
    con piu' cassa che capitale) o un payout su utili in perdita si leggerebbero
    come valori normali con il segno sbagliato. Meglio la cella vuota, che il
    resto dell'interfaccia sa gia' rappresentare.
    """
    den = den.where(den > 0) if positive_den else den.where(den != 0)
    return num / den * scale


def derive_ratios(df: pd.DataFrame) -> pd.DataFrame:
    """
    I rapporti che il dataset non porta gia' pronti, calcolati QUI e una volta
    sola.

    Perche' qui e non nella pagina: le mediane di settore (`peer_medians`) si
    costruiscono sulle colonne del DataFrame, quindi un rapporto calcolato al
    momento di disegnare una scheda sarebbe l'unico senza confronto con i pari.
    Aggiungendolo alla tabella, ogni nuovo indicatore eredita gratuitamente il
    delta contro l'industria come tutti gli altri.
    """
    rev = _series(df, "revenue_ttm")
    ni = _series(df, "net_income_ttm")
    ebit = _series(df, "ebit_ttm")
    ocf = _series(df, "ocf_ttm")
    fcf = _series(df, "fcf_ttm")
    assets = _series(df, "assets")
    equity = _series(df, "equity")
    debt = _series(df, "total_debt")
    cash = _series(df, "cash")
    ndebt = _series(df, "net_debt")
    mcap = _series(df, "market_cap")
    pe = _series(df, "pe_ratio")
    dy = _series(df, "dividend_yield")

    # Capex non e' nel file dei fondamentali, ma e' esattamente la differenza
    # fra i due flussi di cassa che ci sono. Segno POSITIVO = uscita.
    df["capex_ttm"] = ocf - fcf

    df["fcf_margin_pct"] = _ratio(fcf, rev, scale=100)
    df["ocf_margin_pct"] = _ratio(ocf, rev, scale=100)
    df["capex_to_revenue_pct"] = _ratio(df["capex_ttm"], rev, scale=100)
    # Conversione: quanta parte dell'utile CONTABILE diventa cassa. Ha senso
    # solo con utile positivo — con una perdita il rapporto cambia segno e si
    # leggerebbe come una conversione eccellente.
    df["fcf_conversion_pct"] = _ratio(fcf, ni, scale=100)
    df["asset_turnover"] = _ratio(rev, assets)

    invested = equity + debt.fillna(0) - cash.fillna(0)
    df["roic_pct"] = _ratio(ebit * (1 - ROIC_TAX_RATE), invested, scale=100)

    df["net_debt_to_equity"] = _ratio(ndebt, equity)
    df["debt_to_assets_pct"] = _ratio(debt, assets, scale=100)
    # Quanti anni di cassa libera servono per estinguere il debito netto. E' la
    # domanda a cui il debito/EBIT risponde solo per approssimazione: gli
    # interessi si pagano con la cassa, non con l'utile operativo.
    df["net_debt_to_fcf"] = _ratio(ndebt, fcf)
    df["cash_to_debt_pct"] = _ratio(cash, debt, scale=100)
    df["earnings_yield_pct"] = _ratio(pd.Series(100.0, index=df.index), pe)
    # Dividendi pagati (rendimento × capitalizzazione) sugli utili dello stesso
    # periodo: quanta parte dell'utile esce dall'azienda verso i soci.
    df["payout_ratio_pct"] = _ratio(dy / 100 * mcap, ni, scale=100)
    return df
