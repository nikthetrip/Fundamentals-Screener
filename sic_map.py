"""
sic_map.py — Codice SIC (SEC) -> settore e industria.

PERCHE' ESISTE. Settore e industria arrivano da yfinance, che pero' li lascia
vuoti per una quota tutt'altro che marginale dei titoli minori — e sull'intero
listino USA i titoli minori sono la maggioranza. Un titolo senza industria non
ha gruppo di confronto: sparisce da ogni delta "rispetto alla media della sua
industria", che e' l'informazione centrale dello screener.

Il codice SIC invece c'e' SEMPRE: la SEC lo assegna a ogni filer e lo espone
nel file submissions che scarichiamo gia' per i link ai 10-K. E' quindi un
ripiego gratuito e completo, con una granularita' (circa 450 codici a quattro
cifre) confrontabile con quella delle industrie di yfinance.

I nomi dei settori seguono la tassonomia di yfinance, non le "divisioni" SIC,
perche' i due dati finiscono nella stessa colonna: mescolare "Manufacturing"
(SIC) e "Technology" (yfinance) spaccherebbe in due il gruppo di confronto.
"""

from __future__ import annotations
from typing import Optional

# Intervalli SIC -> settore in tassonomia yfinance.
# L'ordine conta: vince il primo intervallo che contiene il codice, quindi le
# eccezioni specifiche vanno prima degli intervalli larghi.
_SECTOR_RANGES: tuple[tuple[int, int, str], ...] = (
    # --- eccezioni specifiche, prima degli intervalli generali ---
    (2833, 2836, "Healthcare"),          # farmaceutica e biotech
    (3826, 3826, "Healthcare"),          # strumenti di laboratorio
    (3841, 3851, "Healthcare"),          # dispositivi medici
    (3570, 3579, "Technology"),          # computer e periferiche
    (3661, 3679, "Technology"),          # elettronica, semiconduttori
    (3690, 3695, "Technology"),
    (7370, 7379, "Technology"),          # software e servizi IT
    (3711, 3716, "Consumer Cyclical"),   # auto e veicoli
    (2000, 2099, "Consumer Defensive"),  # alimentare
    (2100, 2199, "Consumer Defensive"),  # tabacco
    (2840, 2844, "Consumer Defensive"),  # detergenti e cosmetici
    (4810, 4899, "Communication Services"),
    (2711, 2796, "Communication Services"),   # editoria e stampa
    (7810, 7841, "Communication Services"),   # cinema e video
    (7900, 7999, "Communication Services"),   # intrattenimento
    # --- intervalli generali ---
    (100, 999, "Basic Materials"),       # agricoltura
    (1000, 1119, "Basic Materials"),     # metalli e minerali
    (1120, 1399, "Energy"),              # petrolio e gas
    (1400, 1499, "Basic Materials"),
    (1500, 1799, "Industrials"),         # costruzioni
    (2200, 2399, "Consumer Cyclical"),   # tessile e abbigliamento
    (2400, 2599, "Industrials"),         # legno e arredo
    (2600, 2699, "Basic Materials"),     # carta
    (2800, 2899, "Basic Materials"),     # chimica
    (2900, 2999, "Energy"),              # raffinazione
    (3000, 3299, "Basic Materials"),     # gomma, plastica, vetro, cemento
    (3300, 3399, "Basic Materials"),     # siderurgia
    (3400, 3569, "Industrials"),         # metalmeccanica
    (3580, 3660, "Industrials"),
    (3680, 3689, "Technology"),
    (3696, 3710, "Industrials"),
    (3717, 3799, "Industrials"),         # componentistica, aerospazio, difesa
    (3800, 3825, "Industrials"),
    (3827, 3840, "Industrials"),
    (3852, 3999, "Consumer Cyclical"),   # beni vari di consumo
    (4000, 4799, "Industrials"),         # trasporti e logistica
    (4900, 4949, "Utilities"),
    (4950, 4999, "Industrials"),         # servizi ambientali
    (5000, 5199, "Industrials"),         # commercio all'ingrosso
    (5200, 5399, "Consumer Cyclical"),   # distribuzione
    (5400, 5499, "Consumer Defensive"),  # alimentari al dettaglio
    (5500, 5799, "Consumer Cyclical"),
    (5800, 5899, "Consumer Cyclical"),   # ristorazione
    (5900, 5912, "Consumer Defensive"),  # farmacie
    (5913, 5999, "Consumer Cyclical"),
    (6000, 6199, "Financial Services"),  # banche e credito
    (6200, 6299, "Financial Services"),  # intermediazione
    (6300, 6499, "Financial Services"),  # assicurazioni
    (6500, 6599, "Real Estate"),
    (6798, 6798, "Real Estate"),         # REIT
    (6600, 6799, "Financial Services"),  # holding e finanziarie
    (7000, 7099, "Consumer Cyclical"),   # alberghi
    (7200, 7299, "Consumer Cyclical"),   # servizi alla persona
    (7300, 7369, "Industrials"),         # servizi alle imprese
    (7380, 7599, "Industrials"),
    (7600, 7699, "Consumer Cyclical"),
    (8000, 8099, "Healthcare"),          # strutture sanitarie
    (8100, 8299, "Consumer Defensive"),  # istruzione e servizi legali
    (8300, 8399, "Healthcare"),          # servizi sociali
    (8400, 8999, "Industrials"),         # servizi professionali
)

# Nomi di industria per i codici SIC piu' frequenti fra le societa' quotate.
# Dove il codice non e' in tabella si usa la descrizione testuale che la SEC
# stessa restituisce (sicDescription), che e' sempre presente: la tabella serve
# solo a rendere piu' leggibili i nomi piu' comuni.
_INDUSTRY_OVERRIDES: dict[int, str] = {
    2834: "Drug Manufacturers",
    2836: "Biotechnology",
    3571: "Computer Hardware",
    3572: "Computer Storage",
    3576: "Computer Networking",
    3577: "Computer Peripherals",
    3674: "Semiconductors",
    3661: "Communication Equipment",
    3663: "Communication Equipment",
    3711: "Auto Manufacturers",
    3714: "Auto Parts",
    3721: "Aerospace & Defense",
    3812: "Aerospace & Defense",
    3841: "Medical Devices",
    3845: "Medical Devices",
    4813: "Telecom Services",
    4911: "Utilities — Regulated Electric",
    4923: "Utilities — Regulated Gas",
    4924: "Utilities — Regulated Gas",
    4931: "Utilities — Diversified",
    5812: "Restaurants",
    6021: "Banks — Diversified",
    6022: "Banks — Regional",
    6035: "Banks — Savings & Loans",
    6199: "Capital Markets",
    6211: "Capital Markets",
    6311: "Insurance — Life",
    6331: "Insurance — Property & Casualty",
    6798: "REIT",
    7370: "Information Technology Services",
    7372: "Software — Application",
    7389: "Specialty Business Services",
    8731: "Biotechnology Research",
    1311: "Oil & Gas E&P",
    2911: "Oil & Gas Refining",
    1389: "Oil & Gas Equipment & Services",
}


# Codici SIC di veicoli che NON sono aziende operative: fondi, ETF, ETP su
# commodity, SPAC. Verificati sui dati reali della SEC, non presunti:
#
#   6221  Commodity Contracts Brokers & Dealers — e' qui che stanno gli ETP su
#         materie prime (GLD, AAAU, USO). Si dichiarano entityType "operating",
#         quindi questo codice e' l'unico modo per riconoscerli.
#   6722  Management Investment Offices, Open-End — fondi aperti
#   6726  Investment Offices NEC — fondi chiusi ed ETF
#   6770  Blank Checks — SPAC: societa' quotate senza attivita', per le quali
#         una valutazione sugli utili non ha alcun oggetto
#   6799  Investors NEC — trust e veicoli di investimento vari
#
# NON sono in lista, di proposito: 6798 (REIT) e 6282 (Investment Advice). Un
# REIT e un gestore di fondi sono aziende vere, con utili, dipendenti e bilanci
# da valutare — escluderli perche' "somigliano" a un fondo sarebbe un errore.
NON_OPERATING_SIC = frozenset({6221, 6722, 6726, 6770, 6799})

# entityType della SEC che identifica un veicolo di investimento.
#
# SOLO "investment". "other" NON va qui, per quanto sembri: la SEC lo usa per
# gli EMITTENTI ESTERI, quelli che depositano il 20-F invece del 10-K. Provato
# sull'universo reale, includerlo escludeva 22 aziende in piena attivita' —
# Spotify, Birkenstock, GlobalFoundries, Qiagen, Nubank, Bank OZK, On Holding —
# tutte con entityType "other" perche' straniere, non perche' fondi. Lo stesso
# valore ce l'ha SPY, quindi come discriminante non separa nulla: distingue i
# filer non statunitensi, non i fondi dalle aziende.
NON_OPERATING_ENTITY_TYPES = frozenset({"investment"})


def is_operating_company(sic: Optional[str | int] = None,
                         entity_type: Optional[str] = None) -> bool:
    """
    True se l'emittente e' un'azienda, non un fondo o un ETP.

    Due segnali indipendenti, perche' nessuno dei due basta da solo: gli ETF su
    indice si riconoscono dall'entityType, quelli su commodity solo dal SIC.
    In dubbio si tiene: un falso positivo tolto dal listino e' un'azienda che
    spariva senza motivo, mentre un fondo che passa viene comunque scartato piu'
    a valle, perche' non deposita utili in us-gaap.
    """
    if entity_type and entity_type.strip().lower() in NON_OPERATING_ENTITY_TYPES:
        return False
    code = _as_int(sic)
    if code is not None and code in NON_OPERATING_SIC:
        return False
    return True


def non_operating_reason(sic: Optional[str | int] = None,
                         entity_type: Optional[str] = None,
                         sic_description: Optional[str] = None) -> Optional[str]:
    """Perche' un emittente e' stato classificato come non operativo."""
    if entity_type and entity_type.strip().lower() in NON_OPERATING_ENTITY_TYPES:
        return f"veicolo di investimento (entityType SEC '{entity_type}')"
    code = _as_int(sic)
    if code is not None and code in NON_OPERATING_SIC:
        label = (sic_description or "").strip() or "veicolo non operativo"
        return f"fondo/ETP: SIC {code} — {label}"
    return None


def sector_from_sic(sic: Optional[str | int]) -> Optional[str]:
    """Settore (tassonomia yfinance) dal codice SIC. None se il codice manca."""
    code = _as_int(sic)
    if code is None:
        return None
    for lo, hi, sector in _SECTOR_RANGES:
        if lo <= code <= hi:
            return sector
    return None


def industry_from_sic(sic: Optional[str | int],
                      sic_description: Optional[str] = None) -> Optional[str]:
    """
    Industria dal codice SIC: nome della tabella se presente, altrimenti la
    descrizione ufficiale SEC. None solo se manca tutto.
    """
    code = _as_int(sic)
    if code is not None and code in _INDUSTRY_OVERRIDES:
        return _INDUSTRY_OVERRIDES[code]
    desc = (sic_description or "").strip()
    return desc.title() if desc else None


def _as_int(sic: Optional[str | int]) -> Optional[int]:
    if sic is None or sic == "":
        return None
    try:
        return int(str(sic).strip())
    except (ValueError, TypeError):
        return None
