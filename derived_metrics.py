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


# ---------------------------------------------------------------------------
# COSTO DEL CAPITALE
#
# COSA SOSTITUISCE. Prima era una costante: 9% per tutte le societa'. Il
# problema di una soglia fissa non era l'imprecisione ma la DISTORSIONE
# SISTEMATICA: con un beta medio di 0,62 le utility hanno un costo del capitale
# intorno al 6%, e la soglia fissa le marchiava come distruttrici di valore
# rendendo l'8%; le tecnologiche, con beta 1,35, arrivano oltre l'11% e la
# stessa soglia le promuoveva rendendo il 10%. Un errore che punta sempre nella
# stessa direzione per interi settori e' peggio del rumore.
#
# COSA RESTA UNA STIMA. Il premio per il rischio azionario. Non e' un dato
# depositato da nessuno e chiunque calcoli un WACC ne assume uno; qui e'
# dichiarato in una costante invece di essere nascosto dentro una soglia.
# Tutto il resto viene da grandezze reali: il beta dal provider, il tasso privo
# di rischio da FRED, il costo del debito dagli interessi effettivamente pagati
# e depositati alla SEC.
# ---------------------------------------------------------------------------

EQUITY_RISK_PREMIUM = 5.0

# Il beta oltre questa forbice non descrive piu' il rischio dell'impresa: e'
# il sintomo di un titolo poco scambiato o di una serie storica corta. Fuori
# dai limiti si usa la mediana del settore, che e' stabile.
BETA_FLOOR, BETA_CAP = 0.4, 2.0

# Un costo del debito fuori da questa forbice non e' un costo del debito: e'
# un rapporto fra due voci disallineate — interessi di un anno su un debito
# appena estinto, o su un debito appena acceso a fine esercizio.
COST_OF_DEBT_FLOOR, COST_OF_DEBT_CAP = 1.0, 15.0


def add_cost_of_capital(df: pd.DataFrame,
                        risk_free: float | None = None) -> pd.DataFrame:
    """
    Costo dei mezzi propri, costo del debito e WACC, societa' per societa'.

    IL RIPIEGO E' SUL SETTORE, NON SU UNA MEDIA GENERALE. Dove il beta manca o
    e' assurdo si usa la mediana del proprio settore: un'utility senza beta
    somiglia molto piu' alle altre utility che alla mediana del listino.
    """
    rf = risk_free
    if rf is None and "risk_free_pct" in df.columns:
        rf = pd.to_numeric(df["risk_free_pct"], errors="coerce").dropna().median()
    if rf is None or not (0 < rf < 20):
        from data_sources import RISK_FREE_FALLBACK
        rf = RISK_FREE_FALLBACK
    df["risk_free_pct"] = rf

    beta = _series(df, "beta")
    usable = beta.where(beta.between(BETA_FLOOR, BETA_CAP))
    if "sector" in df.columns:
        by_sector = usable.groupby(df["sector"]).transform("median")
        beta_used = usable.fillna(by_sector)
    else:
        beta_used = usable
    beta_used = beta_used.fillna(usable.median()).clip(BETA_FLOOR, BETA_CAP)
    df["beta_used"] = beta_used

    # Costo dei mezzi propri: il CAPM, che con tre input e' l'unica formula
    # ricostruibile a mano da chi legge.
    df["cost_of_equity_pct"] = rf + beta_used * EQUITY_RISK_PREMIUM

    # Costo del debito: quello EFFETTIVAMENTE pagato, non stimato. Dove manca —
    # una societa' senza debito, o che non tagga gli oneri finanziari — si usa
    # il tasso privo di rischio piu' uno scarto, che e' quanto costa il debito
    # a un emittente di qualita' media.
    interest = _series(df, "interest_expense_ttm").abs()
    debt = _series(df, "total_debt")
    rd = _ratio(interest, debt, scale=100)
    rd = rd.where(rd.between(COST_OF_DEBT_FLOOR, COST_OF_DEBT_CAP))
    df["cost_of_debt_pct"] = rd.fillna(rf + 1.5)

    # WACC: la media dei due costi pesata su quanto capitale arriva da ciascuna
    # fonte, con lo scudo fiscale sugli interessi. Il debito entra al valore di
    # bilancio e i mezzi propri a quello di mercato — e' la convenzione, e la
    # ragione e' che il debito si rimborsa al nominale mentre l'azionista
    # incassa il prezzo.
    equity_value = _series(df, "market_cap")
    total_value = equity_value.fillna(0) + debt.fillna(0)
    we = _ratio(equity_value, total_value)
    wd = _ratio(debt, total_value)
    df["wacc_pct"] = (we.fillna(1.0) * df["cost_of_equity_pct"]
                      + wd.fillna(0.0) * df["cost_of_debt_pct"] * (1 - ROIC_TAX_RATE))
    # Senza capitalizzazione non c'e' peso da assegnare: resta il solo costo
    # dei mezzi propri, che e' la stima piu' prudente.
    df.loc[total_value <= 0, "wacc_pct"] = df["cost_of_equity_pct"]
    return df


# I settori dove il ROIC nella forma «EBIT su capitale investito» non misura
# nulla, e quindi confrontarlo con un costo del capitale non significa niente.
#
# Per una banca il debito non e' finanziamento, e' MATERIA PRIMA: raccoglie
# depositi per impiegarli, e metterli al denominatore del capitale investito
# produce un numero che non ha interpretazione. Per un REIT il risultato
# operativo e' depresso dagli ammortamenti su immobili che si rivalutano, ed e'
# il motivo per cui il settore usa gli FFO invece dell'utile.
SECTORS_WITHOUT_MEANINGFUL_ROIC = {"Financial Services", "Real Estate"}


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

    df = add_cost_of_capital(df)
    # Quanto il ROIC supera cio' che il capitale costa. E' il numero che il
    # commento descrive a parole: positivo, crescere crea valore; negativo, lo
    # consuma. Vuoto dove il ROIC non e' interpretabile.
    spread = _series(df, "roic_pct") - df["wacc_pct"]
    if "sector" in df.columns:
        spread = spread.where(~df["sector"].isin(SECTORS_WITHOUT_MEANINGFUL_ROIC))
    df["roic_spread_pp"] = spread
    return df
