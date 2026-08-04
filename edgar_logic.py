"""
edgar_logic.py — Logica pura (niente rete) per costruire la Lynch earnings line.

Separata dalle chiamate di rete così da poterla testare offline con dati sintetici.
Le funzioni qui dentro trasformano i "facts" grezzi di SEC EDGAR in una serie
TTM EPS, e poi in una fair value line allineata ai prezzi.

Concetti chiave:
- EDGAR companyfacts espone EarningsPerShareDiluted (o ...Basic) come lista di
  "fatti", ognuno con start/end/val/fy/fp/form/frame.
- Un fatto con durata ~90 giorni e' un TRIMESTRE discreto; ~365 giorni e' un ANNO (FY).
- Il Q4 discreto spesso NON e' riportato: si ricava come FY - (Q1+Q2+Q3).
- TTM EPS a una certa data = somma degli ultimi 4 EPS trimestrali discreti.
- Fair value(t) = TTM_EPS(as-of t) * target_PE   (es. P/E 15 -> "prezzo a P/E 15").
"""

from __future__ import annotations
import math
from datetime import date, datetime
from typing import Optional


# ---------------------------------------------------------------------------
# Parsing dei facts EDGAR
# ---------------------------------------------------------------------------

MAX_PLAUSIBLE_EPS = 5000.0

# Durate (in giorni) che qualificano un fatto XBRL come trimestrale o annuale.
QUARTER_MIN_DUR, QUARTER_MAX_DUR = 80, 100
ANNUAL_MIN_DUR, ANNUAL_MAX_DUR = 330, 400

# Ampiezza ammessa per una finestra TTM, misurata tra la chiusura del primo e
# quella del quarto trimestre sommati. Quattro trimestri consecutivi distano
# ~273 giorni (3 trimestri). Un valore intorno a 365 significa che i quattro
# punti coprono CINQUE trimestri, cioe' che manca un trimestre in mezzo: la
# somma non e' un TTM e non va prodotta.
TTM_MIN_SPAN, TTM_MAX_SPAN = 240, 310

# Oltre questa eta' l'ultimo dato EDGAR non descrive piu' l'esercizio corrente.
# Berkshire, che dal 2014 tagga l'EPS per classe di azioni, espone una serie
# ferma al 2013: senza questa soglia veniva usata come se fosse attuale.
MAX_SERIES_AGE_DAYS = 200

# La stessa soglia applicata a una serie ANNUALE scarterebbe societa'
# perfettamente in regola. Chi deposita solo il bilancio d'esercizio ha, per
# costruzione, un ultimo dato vecchio di parecchi mesi: a fine luglio l'ultimo
# esercizio chiuso il 31 dicembre ha 210 giorni. Con la soglia trimestrale
# sparivano dallo screener decine di societa' per ogni centinaio esaminato,
# tutte con lo stesso motivo "serie obsoleta: 210 giorni". Un anno e mezzo e'
# il limite oltre il quale un bilancio annuale e' davvero fermo.
MAX_ANNUAL_SERIES_AGE_DAYS = 550

# SECONDA soglia, molto piu' larga: oltre questa la serie non e' nemmeno
# UTILIZZABILE e il ticker esce dal dataset.
#
# Servono due soglie perche' i due casi non sono lo stesso problema. Berkshire,
# ferma al 2013, e' una serie di un'altra epoca: va buttata. Citigroup, il cui
# ultimo dato nelle API XBRL della SEC si fermava al trimestre precedente, ha
# invece uno storico completo e corretto — solo in ritardo di un trimestre.
# Con una soglia sola finivano nello stesso cestino, e Citigroup, Molson Coors
# e Paramount sparivano dallo screener pur avendo dati validi e un EPS corrente
# disponibile dal provider di mercato. Tra le due soglie la serie si usa, con
# la sua eta' dichiarata: e' l'arbitraggio di choose_eps a preferire il dato
# aggiornato del provider per i valori "di oggi".
MAX_SERIES_USABLE_DAYS = 420
MAX_ANNUAL_SERIES_USABLE_DAYS = 800

# Concetti XBRL usati per l'EPS, in ordine di PREFERENZA (non di priorita'
# assoluta: a parita' di freschezza vince il primo, ma un tag con dati piu'
# recenti batte sempre uno piu' vecchio — vedi _pick_concept).
EPS_TAG_CANDIDATES = (
    "EarningsPerShareDiluted",
    "EarningsPerShareBasicAndDiluted",
    "EarningsPerShareBasic",
    "IncomeLossFromContinuingOperationsPerDilutedShare",
    "IncomeLossFromContinuingOperationsPerBasicShare",
)

# Concetti per l'utile netto, usati quando l'EPS non e' disponibile perche'
# la societa' lo tagga per classe di azioni (fatti dimensionali, che le API
# XBRL della SEC non espongono). Vedi build_derived_eps_facts.
NET_INCOME_TAG_CANDIDATES = (
    "NetIncomeLoss",
    "NetIncomeLossAvailableToCommonStockholdersBasic",
    "ProfitLoss",
)


def _to_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _pick_concept(gaap: dict, candidates: tuple[str, ...],
                  unit: str) -> tuple[Optional[dict], Optional[str]]:
    """
    Sceglie fra piu' concetti XBRL quello con i dati PIU' RECENTI.

    Prendere semplicemente il primo della lista che contiene dati e' sbagliato:
    una societa' puo' aver smesso di usare un tag anni fa e averlo sostituito con
    un altro. Emerson (EMR) ne e' il caso tipico: 'EarningsPerShareDiluted' e'
    fermo, mentre 'IncomeLossFromContinuingOperationsPerDilutedShare' e'
    aggiornato. A parita' di data piu' recente vince l'ordine di preferenza.
    """
    best_key = None
    best: tuple[Optional[dict], Optional[str]] = (None, None)
    for rank, name in enumerate(candidates):
        node = gaap.get(name)
        rows = (node or {}).get("units", {}).get(unit)
        if not rows:
            continue
        ends = [r["end"] for r in rows if r.get("end")]
        if not ends:
            continue
        key = (max(ends), -rank)
        if best_key is None or key > best_key:
            best_key, best = key, (node, name)
    return best


def _normalize_facts(rows: list[dict], concept: str,
                     max_abs: Optional[float] = None) -> list[dict]:
    """Normalizza i fatti grezzi XBRL nella forma usata dal resto del modulo."""
    out = []
    for r in rows:
        if "start" not in r or "end" not in r or r.get("val") is None:
            continue
        try:
            start = _to_date(r["start"])
            end = _to_date(r["end"])
        except (ValueError, TypeError):
            continue
        filed = None
        if r.get("filed"):
            try:
                filed = _to_date(r["filed"])
            except (ValueError, TypeError):
                filed = None
        val = float(r["val"])
        # scarta valori impossibili: quasi sempre errori di unita' nei dati XBRL
        if max_abs is not None and abs(val) > max_abs:
            continue
        out.append({
            "start": start,
            "end": end,
            "val": val,
            "fy": r.get("fy"),
            "fp": r.get("fp"),
            "form": r.get("form"),
            "frame": r.get("frame"),
            "filed": filed,
            "dur": (end - start).days,
            "concept": concept,
        })
    return out


def merge_predecessor_eps(current: dict, previous: dict) -> dict:
    """
    Aggiunge alla societa' di oggi gli EPS depositati dal soggetto precedente.

    PERCHE' SOLO L'EPS, e non tutto il bilancio. Il conto economico e lo stato
    patrimoniale delle due entita' non hanno lo stesso perimetro — Alphabet e'
    Google piu' Other Bets — e cucirli produrrebbe ricavi e attivo con un
    gradino che nella realta' non c'e'. L'utile PER AZIONE e' l'unica grandezza
    che sopravvive alla riorganizzazione: e' gia' normalizzata sul numero di
    azioni, e la rettifica per gli split la riporta alla base di oggi. E' anche
    l'unica di cui il grafico del fair value abbia bisogno.

    LE DATE CHE ESISTONO GIA' NON SI TOCCANO. Dove i due soggetti si
    sovrappongono — succede sempre, perche' i primi depositi della nuova
    entita' ripetono i comparativi della vecchia — vince quella di OGGI: e' la
    versione eventualmente rettificata, ed e' quella che il resto della scheda
    usa. Il precedente riempie solo il vuoto davanti.

    Restituisce una COPIA superficiale: `current` non viene modificato, perche'
    e' la stessa struttura che la build passa a tutte le altre estrazioni.
    """
    if not previous or not current:
        return current
    cur_gaap = (current.get("facts") or {}).get("us-gaap") or {}
    prev_gaap = (previous.get("facts") or {}).get("us-gaap") or {}
    if not prev_gaap:
        return current

    merged_gaap = dict(cur_gaap)
    for tag in EPS_TAG_CANDIDATES:
        prev_rows = ((prev_gaap.get(tag) or {}).get("units", {})
                     .get("USD/shares"))
        if not prev_rows:
            continue
        node = dict(cur_gaap.get(tag) or {})
        units = dict(node.get("units") or {})
        cur_rows = list(units.get("USD/shares") or [])
        have = {(r.get("start"), r.get("end")) for r in cur_rows}
        extra = [r for r in prev_rows if (r.get("start"), r.get("end")) not in have]
        if not extra:
            continue
        units["USD/shares"] = cur_rows + extra
        node["units"] = units
        merged_gaap[tag] = node

    out = dict(current)
    facts = dict(current.get("facts") or {})
    facts["us-gaap"] = merged_gaap
    out["facts"] = facts
    return out


def extract_eps_facts(companyfacts: dict) -> list[dict]:
    """
    Estrae i fatti EPS da un JSON companyfacts di EDGAR.

    Prova piu' concetti XBRL (non tutte le societa' usano EarningsPerShareDiluted)
    e sceglie quello con i dati piu' recenti.

    LIMITE NOTO DELL'API: companyfacts espone solo i fatti SENZA dimensioni. Le
    societa' con piu' classi di azioni taggano l'EPS con un asse ClassOfStockAxis,
    quindi per loro qui non arriva nulla (Visa, Constellation, KKR) oppure arriva
    una serie interrotta all'anno in cui hanno cambiato tagging (Berkshire, ferma
    al 2013). Per quei casi vedi build_derived_eps_facts.

    Ritorna lista normalizzata: {start, end, val, fy, fp, form, frame, filed,
    dur, concept}.
    """
    gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    node, name = _pick_concept(gaap, EPS_TAG_CANDIDATES, "USD/shares")
    if not node:
        return []
    rows = node.get("units", {}).get("USD/shares", [])
    return _normalize_facts(rows, name or "", max_abs=MAX_PLAUSIBLE_EPS)


def extract_net_income_facts(companyfacts: dict) -> list[dict]:
    """Fatti di utile netto (in dollari), stessa forma normalizzata dell'EPS."""
    gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    node, name = _pick_concept(gaap, NET_INCOME_TAG_CANDIDATES, "USD")
    if not node:
        return []
    return _normalize_facts(node.get("units", {}).get("USD", []), name or "")


def extract_dei_shares(companyfacts: dict) -> Optional[tuple[date, float]]:
    """
    Numero di azioni in circolazione dalla copertina dei filing (taxonomy 'dei').

    Utile come denominatore quando l'EPS non e' ricavabile. Anche questo dato
    e' dimensionale per le societa' multiclasse: in quei casi l'ultimo valore
    disponibile e' vecchio di anni (Visa si ferma al 2010) e va scartato dal
    chiamante in base alla data, che per questo viene restituita.
    """
    rows = (companyfacts.get("facts", {}).get("dei", {})
            .get("EntityCommonStockSharesOutstanding", {})
            .get("units", {}).get("shares", []))
    best: Optional[tuple[date, float]] = None
    for r in rows:
        if not r.get("end") or r.get("val") is None:
            continue
        try:
            d = _to_date(r["end"])
        except (ValueError, TypeError):
            continue
        val = float(r["val"])
        if val <= 0:
            continue
        if best is None or d > best[0]:
            best = (d, val)
    return best


def build_derived_eps_facts(companyfacts: dict, shares: float) -> list[dict]:
    """
    Ricostruisce fatti EPS dividendo l'utile netto EDGAR per un numero di azioni.

    Serve alle societa' multiclasse, per le quali l'EPS non e' esposto dalle API
    XBRL ma l'utile netto si'. Il numeratore resta quindi un dato primario SEC;
    il denominatore e' un numero di azioni COSTANTE, quindi la serie storica non
    riflette buyback e diluizioni passate. E' una stima, non un dato depositato:
    chi la usa deve etichettarla come tale (vedi eps_source='edgar-derived').
    """
    if not shares or shares <= 0:
        return []
    facts = extract_net_income_facts(companyfacts)
    out = []
    for f in facts:
        val = f["val"] / shares
        if abs(val) > MAX_PLAUSIBLE_EPS:
            continue
        out.append({**f, "val": val, "concept": f["concept"] + "/shares"})
    return out


def series_age_days(series: list[tuple[date, float]],
                    today: Optional[date] = None) -> Optional[int]:
    """Giorni trascorsi dall'ultimo punto della serie. None se la serie e' vuota."""
    if not series:
        return None
    return ((today or date.today()) - max(d for d, _ in series)).days


def max_age_for_method(method: str) -> int:
    """
    Soglia di obsolescenza adatta al passo della serie.

    Una serie TTM trimestrale deve essere recente; una serie annuale e'
    normale che abbia mesi sulle spalle. Applicare la stessa soglia a entrambe
    significa scartare chi deposita solo il 10-K.
    """
    return MAX_ANNUAL_SERIES_AGE_DAYS if method == "annual" else MAX_SERIES_AGE_DAYS


def max_usable_age_for_method(method: str) -> int:
    """Eta' oltre la quale la serie non e' piu' utilizzabile affatto."""
    return (MAX_ANNUAL_SERIES_USABLE_DAYS if method == "annual"
            else MAX_SERIES_USABLE_DAYS)


def series_is_stale(series: list[tuple[date, float]],
                    max_age_days: int = MAX_SERIES_AGE_DAYS,
                    today: Optional[date] = None) -> bool:
    """
    True se la serie e' troppo vecchia per descrivere l'esercizio corrente.

    Una serie obsoleta non e' un dato degradato: e' un dato di un'altra epoca.
    Va scartata, non pesata meno.
    """
    age = series_age_days(series, today)
    return age is None or age > max_age_days


def _dedupe_by_end(facts: list[dict]) -> dict[date, dict]:
    """
    Piu' fatti possono avere lo stesso 'end' (comparativi ri-dichiarati in filing
    successivi, es. dopo uno stock split). Teniamo il piu' affidabile:
    PREFERISCI IL FILING PIU' RECENTE (riflette split e rettifiche); a parita' di
    data di deposito, preferisci quello con 'frame' (valore standardizzato SEC).

    Questa regola e' cio' che evita la contaminazione pre/post-split: il valore
    piu' vecchio (pre-split, numero grande) viene scartato in favore di quello
    ridepositato dopo lo split.
    """
    best: dict[date, dict] = {}
    for f in facts:
        e = f["end"]
        cur = best.get(e)
        if cur is None:
            best[e] = f
            continue
        f_filed = f.get("filed") or date.min
        c_filed = cur.get("filed") or date.min
        if f_filed > c_filed:
            best[e] = f
        elif f_filed == c_filed and f.get("frame") and not cur.get("frame"):
            best[e] = f
    return best


def build_quarterly_eps(facts: list[dict]) -> list[tuple[date, float]]:
    """
    Ricostruisce una serie di EPS TRIMESTRALI DISCRETI (end_date, eps_del_trimestre).

    Strategia:
    1. Prendi i fatti "quarterly-like" (durata 80..100 gg) -> trimestri discreti diretti.
    2. Prendi i fatti "annual" (durata 330..400 gg) -> FY.
    3. Per ogni FY, se conosciamo esattamente tre trimestri di quell'esercizio,
       ricava il quarto come FY - (Q1+Q2+Q3). Il Q4 discreto quasi mai viene
       depositato: esiste il 10-K, non un "10-Q del quarto trimestre".

    LA FINESTRA DELL'ESERCIZIO E' LA PARTE DELICATA. Deve iniziare subito DOPO la
    chiusura dell'esercizio precedente. Una finestra anche solo di pochi giorni
    piu' larga vi fa rientrare il Q4 dell'anno prima — che questo stesso ciclo ha
    appena ricostruito e inserito nella mappa — portando il conteggio a quattro e
    impedendo per sempre la ricostruzione dell'anno corrente. Il difetto si
    auto-alterna (ricostruisce l'anno N solo se non ha ricostruito N-1) e
    colpiva circa meta' dell'S&P 500: Estee Lauder risultava a +1,25$ di utile
    per azione mentre il TTM reale era -0,71$.

    Per lo stesso motivo gli esercizi vanno percorsi in ORDINE CRONOLOGICO: su un
    dizionario l'ordine dipende da come sono arrivati i fatti nel JSON.
    """
    quarterly = _dedupe_by_end(
        [f for f in facts if QUARTER_MIN_DUR <= f["dur"] <= QUARTER_MAX_DUR])
    annual = _dedupe_by_end(
        [f for f in facts if ANNUAL_MIN_DUR <= f["dur"] <= ANNUAL_MAX_DUR])

    # mappa end_date -> valore del trimestre discreto
    q_by_end = {e: f["val"] for e, f in quarterly.items()}

    fy_ends = sorted(annual)
    for i, fy_end in enumerate(fy_ends):
        if fy_end in q_by_end:
            continue  # il Q4 discreto c'e' gia', niente da ricostruire

        # Inizio dell'esercizio: il giorno dopo la chiusura precedente se questa
        # e' davvero l'anno prima; altrimenti un limite prudenziale a 372 giorni,
        # che copre anche gli esercizi da 53 settimane.
        floor = date.fromordinal(fy_end.toordinal() - 372)
        prev_fy_end = fy_ends[i - 1] if i else None
        if prev_fy_end is not None and prev_fy_end > floor:
            win_start = date.fromordinal(prev_fy_end.toordinal() + 1)
        else:
            win_start = floor

        qs_in_fy = [(e, v) for e, v in q_by_end.items() if win_start <= e <= fy_end]
        if len(qs_in_fy) == 3:
            q_by_end[fy_end] = annual[fy_end]["val"] - sum(v for _, v in qs_in_fy)

    return sorted(q_by_end.items())


def build_annual_eps(facts: list[dict]) -> list[tuple[date, float]]:
    """Serie annuale FY (fallback quando i trimestri sono insufficienti)."""
    annual = _dedupe_by_end(
        [f for f in facts if ANNUAL_MIN_DUR <= f["dur"] <= ANNUAL_MAX_DUR])
    return sorted((e, f["val"]) for e, f in annual.items())


def rolling_ttm(quarters: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """
    Somma rolling di quattro trimestri discreti -> serie TTM.

    Produce un punto SOLO per le finestre che coprono davvero dodici mesi: la
    verifica dell'ampiezza e' il presidio che rende innocuo un eventuale buco
    nella serie trimestrale. Sommare quattro punti che coprono cinque trimestri
    produce un numero plausibile e sbagliato, che e' il tipo di errore peggiore.
    """
    ttm: list[tuple[date, float]] = []
    quarters = sorted(quarters)
    for i in range(3, len(quarters)):
        window = quarters[i - 3:i + 1]
        span = (window[-1][0] - window[0][0]).days
        if not (TTM_MIN_SPAN <= span <= TTM_MAX_SPAN):
            continue  # buco nella serie: questi quattro punti non sono un TTM
        ttm.append((window[-1][0], sum(v for _, v in window)))
    return ttm


def build_ttm_eps(facts: list[dict], min_quarters: int = 4) -> tuple[list[tuple[date, float]], str]:
    """
    Costruisce la serie EPS "annualizzata" da usare per la fair value line.

    Ritorna (serie, metodo) dove serie = [(end_date, eps_annualizzato), ...]
    e metodo in {"ttm", "annual"}.

    - Se abbiamo >= min_quarters trimestri discreti: TTM = somma rolling di 4,
      ma SOLO per le finestre che coprono davvero dodici mesi.
    - Altrimenti fallback: serie annuale FY.
    """
    quarters = build_quarterly_eps(facts)
    if len(quarters) >= min_quarters:
        ttm = rolling_ttm(quarters)
        if ttm:
            return ttm, "ttm"

    annual = build_annual_eps(facts)
    return annual, "annual"


# ---------------------------------------------------------------------------
# Merge as-of con i prezzi + fair value
# ---------------------------------------------------------------------------

def asof_eps_dated(eps_series: list[tuple[date, float]],
                   on: date) -> Optional[tuple[date, float]]:
    """
    Come asof_eps, ma ritorna anche la DATA del dato usato.

    Serve a sapere quanto e' vecchio l'utile che si sta attribuendo a un prezzo:
    senza quella data non si puo' distinguere un dato del trimestre scorso da uno
    di nove anni prima. Vedi build_fair_value_rows.
    """
    result = None
    for d, v in eps_series:
        if d <= on:
            result = (d, v)
        else:
            break
    return result


def asof_eps(eps_series: list[tuple[date, float]], on: date) -> Optional[float]:
    """
    Ritorna l'EPS (annualizzato) piu' recente con end_date <= 'on'.
    eps_series deve essere ordinata per data crescente.
    """
    got = asof_eps_dated(eps_series, on)
    return got[1] if got else None


# Per quanti giorni un EPS depositato descrive ancora il prezzo di quel giorno.
# Oltre, non e' un dato vecchio: e' un buco riempito con un numero di un'altra
# epoca. GoDaddy non ha EPS nelle API XBRL della SEC fra il 2017 e il 2022, e
# senza questo limite il suo utile del 2016 (-0,23$) veniva riportato in avanti
# per NOVE anni: la scheda dichiarava una perdita nel 2025, la banda rossa
# "utili negativi" copriva mezzo grafico e la mediana a 3 anni ne usciva falsata.
# 400 giorni coprono chi deposita una volta l'anno; un buco piu' lungo va
# mostrato come buco.
MAX_EPS_CARRY_DAYS = 400


def build_fair_value_rows(
    price_rows: list[tuple[date, float]],
    eps_series: list[tuple[date, float]],
    target_pes: list[int],
    max_carry_days: int = MAX_EPS_CARRY_DAYS,
) -> list[dict]:
    """
    Per ogni (data, prezzo), calcola fair value = EPS_asof * PE per ogni target PE.

    Salta le date precedenti al primo EPS disponibile E quelle per cui l'ultimo
    EPS disponibile e' piu' vecchio di max_carry_days: in un buco della serie non
    sappiamo quanto guadagnasse la societa', e riportare in avanti l'ultimo valore
    noto non colma l'ignoranza, la traveste da dato. Il grafico mostra un buco,
    che e' la rappresentazione onesta.

    Ritorna righe dict: {date, price, eps, fair_value_pe15, ...}.
    """
    eps_series = sorted(eps_series)
    rows = []
    for d, price in price_rows:
        got = asof_eps_dated(eps_series, d)
        if got is None:
            continue
        eps_date, eps = got
        if (d - eps_date).days > max_carry_days:
            continue
        # eps_date accompagna la riga: senza di essa non si sa a quale periodo
        # appartenga l'utile attribuito a quel prezzo, e qualunque verifica che
        # ragioni per finestre temporali finisce per confrontare mele con pere
        # (vedi il controllo di coerenza nella dashboard).
        row = {"date": d, "price": price, "eps": eps, "eps_date": eps_date}
        for pe in target_pes:
            # EPS negativo -> fair value non significativo, lo lasciamo None
            row[f"fair_value_pe{pe}"] = round(eps * pe, 4) if eps > 0 else None
        rows.append(row)
    return rows


def adjust_facts_for_splits(facts: list[dict],
                            splits: list[tuple[date, float]]) -> list[dict]:
    """
    Rettifica gli EPS storici per gli stock split.

    PROBLEMA: EDGAR conserva i valori come furono depositati. Un EPS depositato
    PRIMA di uno split e' espresso sul vecchio numero di azioni (es. AMZN Q1-2022
    = -7.56$ pre-split 20:1). I prezzi di mercato sono invece gia' rettificati.
    Mescolarli produce una fair value line assurda (linea blu a 972$ contro un
    prezzo di 150$).

    SOLUZIONE: per ogni fatto, dividi il valore per il prodotto degli split
    avvenuti DOPO la sua data di deposito. Un fatto depositato dopo lo split e'
    gia' rettificato e resta intatto.

    splits: [(data_split, rapporto)], es. [(date(2022,6,6), 20.0)]
    """
    if not splits:
        return facts
    out = []
    for f in facts:
        ref = f.get("filed") or f.get("end")
        factor = 1.0
        for sd, ratio in splits:
            if ref and ref < sd and ratio > 0:
                factor *= ratio
        if factor != 1.0:
            g = dict(f)
            g["val"] = f["val"] / factor
            g["split_adjusted_by"] = factor
            out.append(g)
        else:
            out.append(f)
    return out


def extract_share_counts(companyfacts: dict) -> list[tuple[date, float]]:
    """
    Estrae il numero medio di azioni diluite (serie annuale) da EDGAR.
    Serve a individuare diluizioni (emissioni) e buyback significativi.
    Ritorna [(end_date, numero_azioni), ...] ordinata.
    """
    gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    node = (gaap.get("WeightedAverageNumberOfDilutedSharesOutstanding")
            or gaap.get("WeightedAverageNumberOfSharesOutstandingBasic"))
    if not node:
        return []
    rows = node.get("units", {}).get("shares", [])
    annual: dict[date, dict] = {}
    for r in rows:
        if not r.get("end") or r.get("val") is None or not r.get("start"):
            continue
        try:
            start = _to_date(r["start"])
            end = _to_date(r["end"])
        except (ValueError, TypeError):
            continue
        if not (330 <= (end - start).days <= 400):   # solo periodi annuali
            continue
        filed = None
        if r.get("filed"):
            try:
                filed = _to_date(r["filed"])
            except (ValueError, TypeError):
                filed = None
        cur = annual.get(end)
        if cur is None or (filed and cur.get("filed") and filed > cur["filed"]):
            annual[end] = {"val": float(r["val"]), "filed": filed}
    return sorted((d, v["val"]) for d, v in annual.items())


def detect_share_events(share_counts: list[tuple[date, float]],
                        threshold_pct: float = 5.0) -> list[dict]:
    """
    Individua variazioni annue rilevanti del numero di azioni.
    +threshold% -> 'diluizione' (emissione azioni)
    -threshold% -> 'buyback'   (riacquisto azioni)
    Ritorna [{date, type, change_pct}, ...].
    """
    events = []
    for i in range(1, len(share_counts)):
        d_prev, v_prev = share_counts[i - 1]
        d_cur, v_cur = share_counts[i]
        if v_prev <= 0:
            continue
        change = (v_cur - v_prev) / v_prev * 100
        if abs(change) >= threshold_pct:
            events.append({
                "date": d_cur,
                "type": "dilution" if change > 0 else "buyback",
                "change_pct": round(change, 1),
            })
    return events


def normalized_eps(eps_series: list[tuple[date, float]], years: int = 3) -> Optional[float]:
    """
    EPS "normalizzato": media dei valori TTM degli ultimi N anni.

    Serve a smussare gli utili gonfiati o depressi da voci straordinarie
    (plusvalenze su partecipazioni, svalutazioni una-tantum, picchi ciclici).
    E' l'approccio usato dai modelli Lynch "normalizzati": il fair value che ne
    deriva e' piu' conservativo di quello su utili GAAP grezzi.

    Usiamo la MEDIANA e non la media perche' e' robusta agli outlier: un singolo
    valore corrotto da EDGAR (unita' sbagliata, ricostruzione Q4 andata male)
    trascinerebbe la media, producendo fair value da milioni di dollari.
    """
    if not eps_series:
        return None
    series = sorted(eps_series)
    cutoff = series[-1][0].toordinal() - int(365.25 * years)
    window = sorted(v for d, v in series if d.toordinal() >= cutoff)
    if not window:
        return None
    n = len(window); mid = n // 2
    return window[mid] if n % 2 else (window[mid - 1] + window[mid]) / 2


def choose_eps(eps_edgar, eps_yf, eps_norm, price, days_stale: int = 0):
    """
    Sceglie quale EPS usare quando le due fonti divergono, e spiega perche'.
    Ritorna (valore, fonte, flag).

    Regole:
      1. Se una sola fonte e' disponibile, si usa quella.
      2. Se divergono <=15%, si usa yfinance (aggiornato in tempo reale).
      3. Se divergono di piu', si arbitra con due controlli indipendenti:
         a) coerenza con l'EPS normalizzato storico (mediana 3 anni);
         b) P/E implicito (prezzo/EPS) dentro un intervallo plausibile 2-150.
         Vince chi supera piu' controlli; a parita' si preferisce EDGAR (dato
         ufficiale da bilancio) se la serie non e' vecchia.

    Caso reale all'origine: Waste Management, dove yfinance riportava 0.86
    contro 6.91 di EDGAR, con un P/E implicito di 277.
    """
    if eps_edgar is None and eps_yf is None:
        return None, "nessuna", "no-eps"
    if eps_yf is None:
        return eps_edgar, "edgar", "edgar-only"
    if eps_edgar is None:
        return eps_yf, "yfinance", "yf-only"
    if eps_yf == 0:
        return eps_edgar, "edgar", "check"

    divergence = abs(eps_edgar - eps_yf) / abs(eps_yf) * 100
    if divergence <= 15:
        return eps_yf, "yfinance", "ok"

    def score(val):
        if val is None or val <= 0:
            return -1
        s = 0
        if eps_norm and eps_norm > 0 and 0.4 <= val / eps_norm <= 2.5:
            s += 1
        if price and price > 0 and 2 <= price / val <= 150:
            s += 1
        return s

    s_edgar, s_yf = score(eps_edgar), score(eps_yf)
    if s_edgar > s_yf:
        return eps_edgar, "edgar", "check"
    if s_yf > s_edgar:
        return eps_yf, "yfinance", "check"
    if days_stale <= 200 and eps_edgar and eps_edgar > 0:
        return eps_edgar, "edgar", "check"
    return eps_yf, "yfinance", "check"


# Oltre questo multiplo del prezzo, un fair value non e' una valutazione.
# Cinque volte e' gia' generoso: che il mercato sbagli del 400% e' molto meno
# probabile che sbagli la nostra base di utili (tipicamente un utile appena
# rimbalzato da una perdita, moltiplicato per il multiplo di una fast grower).
FAIR_VALUE_MAX_RATIO = 5.0

# P/E oltre il quale la BASE di utili non descrive piu' la societa'. Sopra 100
# l'utile e' cosi' vicino a zero che qualunque multiplo produce un numero
# arbitrario: Estee Lauder usciva con un fair value dello 0,3% del prezzo, Palo
# Alto del 2,2%. Non sono giudizi di carezza, sono divisioni per quasi-zero.
#
# PERCHE' UNA SOGLIA SUL P/E E NON SUL RAPPORTO fair value/prezzo. Sono la
# stessa cosa solo in apparenza: `fair value / prezzo` e' identicamente
# `P/E equo / P/E effettivo`. Un cancello sul rapporto punisce quindi il
# multiplo equo BASSO tanto quanto la base sbagliata, e un multiplo equo basso
# e' spesso il giudizio corretto. Provato sui dati: un cancello a un quinto del
# prezzo avrebbe soppresso 3M, Fortive, W.P. Carey ed Enphase — societa' con un
# P/E normale di circa 30 che il modello giudica care contro un P/E equo di 6.
# Quello e' segnale, non rumore. La soglia sul P/E ne sopprime 40 invece di 143,
# e sono quelle in cui l'utile e' davvero evaporato.
MAX_USABLE_PE = 100.0

# Fondo assoluto sul lato basso, molto piu' largo del cap sul lato alto (1/20
# contro 5x). Serve a due cose: prendere la spazzatura anche quando il chiamante
# non passa la base di utili (e quindi il cancello sul P/E non puo' scattare), e
# lasciare comunque passare i giudizi "molto caro" legittimi. La calibrazione e'
# sui dati: a un quinto del prezzo il cancello avrebbe soppresso 3M e Fortive,
# societa' a P/E 30 che il modello giudica care contro un P/E equo di 6; a un
# ventesimo non tocca nessuno di quei casi e resta una rete di sicurezza.
MIN_FAIR_VALUE_RATIO = 1 / 20


def fair_value_check(fair_value, price, base_eps=None,
                     anchor: str = "earnings",
                     max_ratio: float = FAIR_VALUE_MAX_RATIO,
                     max_pe: float = MAX_USABLE_PE) -> tuple[bool, str]:
    """
    Il cancello di sicurezza sul fair value, con la RAGIONE del rifiuto.

    Restituire un semplice False costringeva il chiamante a scrivere None nella
    colonna, e nell'interfaccia un fair value soppresso diventava
    indistinguibile da un fair value mai calcolato. Sono due cose diverse: la
    seconda e' una categoria senza ancora, la prima e' un conto che e' stato
    fatto e ha dato un risultato non credibile. Chi legge ha diritto di sapere
    quale dei due sta guardando.

    Due controlli asimmetrici, perche' i due errori non sono simmetrici:
      - verso l'alto, il rapporto con il prezzo (max_ratio);
      - verso il basso, il P/E implicito nella BASE usata (max_pe), non il
        rapporto — vedi il commento su MAX_USABLE_PE.
    Il secondo si applica solo alle ancore sugli utili: per un asset play il
    P/E non e' lo strumento, quindi non e' nemmeno il criterio.
    """
    if fair_value is None:
        return False, "not calculated"
    if price is None or price <= 0:
        return False, "no price to compare against"
    if fair_value <= 0:
        return False, "non-positive earnings base"
    ratio = fair_value / price
    if ratio > max_ratio:
        return False, (f"suppressed: the multiple implies {ratio:.1f}x the market "
                       "price — that is a rebounding earnings base, not a "
                       "valuation")
    if anchor == "earnings" and base_eps and base_eps > 0:
        implied_pe = price / base_eps
        if implied_pe > max_pe:
            return False, (f"suppressed: the earnings used imply a P/E of "
                           f"{implied_pe:,.0f} — earnings this close to zero "
                           "make every multiple arbitrary")
    if ratio < MIN_FAIR_VALUE_RATIO:
        return False, (f"suppressed: {ratio * 100:.0f}% of the market price — "
                       "below the floor at which a fair value is still a "
                       "judgement rather than an artefact")
    return True, "ok"


def fair_value_is_plausible(fair_value, price, base_eps=None,
                            anchor: str = "earnings",
                            max_ratio: float = FAIR_VALUE_MAX_RATIO) -> bool:
    """Solo il si'/no di fair_value_check(), per i chiamanti che non usano la ragione."""
    return fair_value_check(fair_value, price, base_eps, anchor, max_ratio)[0]


def series_cagr_detail(series: list[tuple[date, float]], years: int = 5,
                       min_coverage: float = 0.7) -> Optional[dict]:
    """
    CAGR con i DUE PUNTI da cui e' ricavato, per poterlo rifare a mano.

    Ritorna {start_date, start_value, end_date, end_value, span_years, cagr_pct}
    oppure None quando il tasso non e' calcolabile. Un tasso di crescita senza i
    suoi estremi non e' verificabile: chi legge "+26% l'anno" ha diritto di
    sapere da quale valore a quale valore, e su quanti anni esatti.

    Il punto di partenza e' quello PIU' VICINO a N anni prima dell'ultimo, non il
    primo che cade dentro la finestra. La differenza non e' accademica: cercando
    il primo punto "dentro" i cinque anni, una serie trimestrale parte
    sistematicamente un trimestre dopo il bordo e il tasso viene calcolato su
    4,75 anni invece di 5 — per Apple, 5,7% invece di 6,8%. Il tasso resta
    annualizzato sullo span effettivo, che quindi conviene tenere il piu' vicino
    possibile a quello richiesto.

    None se la base di partenza e' <= 0 (il CAGR non e' definito partendo da una
    perdita) o se lo span effettivo copre meno di min_coverage della finestra
    richiesta — meglio nessun numero che un "CAGR a 5 anni" su due anni e mezzo.
    """
    if len(series) < 2:
        return None
    s = sorted(series)
    end_d, end_v = s[-1]
    target = end_d.toordinal() - int(365.25 * years)
    start_d, start_v = min(s[:-1], key=lambda p: abs(p[0].toordinal() - target))
    span_years = (end_d - start_d).days / 365.25
    if span_years < years * min_coverage or start_v <= 0 or end_v <= 0:
        return None
    return {
        "start_date": start_d,
        "start_value": start_v,
        "end_date": end_d,
        "end_value": end_v,
        "span_years": span_years,
        "cagr_pct": ((end_v / start_v) ** (1 / span_years) - 1) * 100,
    }


def series_cagr(series: list[tuple[date, float]], years: int = 5,
                min_coverage: float = 0.7) -> Optional[float]:
    """CAGR di una serie datata su N anni, in percentuale. Vedi series_cagr_detail."""
    got = series_cagr_detail(series, years, min_coverage)
    return got["cagr_pct"] if got else None


def eps_cagr(eps_series: list[tuple[date, float]], years: int = 5) -> Optional[float]:
    """CAGR degli utili per azione su N anni. Vedi series_cagr."""
    return series_cagr(eps_series, years)


def yoy_change(series: list[tuple[date, float]],
               tol_days: int = 75) -> Optional[float]:
    """
    Variazione percentuale sull'ultimo anno, confrontando per DATA e non per
    posizione: cerca il punto piu' vicino a 365 giorni prima dell'ultimo e
    rifiuta il confronto se il piu' vicino dista oltre tol_days.

    Confrontare per posizione (es. "quattro punti indietro") produce numeri
    sbagliati appena la serie ha spaziatura irregolare o un buco — che nei dati
    XBRL succede spesso.
    """
    if len(series) < 2:
        return None
    s = sorted(series)
    last_d, last_v = s[-1]
    target = last_d.toordinal() - 365
    best, best_gap = None, None
    for d, v in s[:-1]:
        gap = abs(d.toordinal() - target)
        if best_gap is None or gap < best_gap:
            best, best_gap = (d, v), gap
    if best is None or best_gap > tol_days or best[1] == 0:
        return None
    return (last_v - best[1]) / abs(best[1]) * 100.0


# Oltre questa variazione annua una societa' e' "erratica" e basta: sapere se
# l'utile e' sceso del 400% o del 4.000% non aggiunge segnale, ma in una
# deviazione standard il secondo numero pesa cento volte il primo e finisce per
# essere l'unica cosa che la misura descrive.
VOL_CLIP_PCT = 200.0

# Pavimento del denominatore, in frazione del livello TIPICO degli utili della
# societa'. Senza, una variazione calcolata su una base vicina a zero (un
# trimestre a 0,01$) produce percentuali a sei cifre.
VOL_FLOOR_FRACTION = 0.15


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _mad(values: list[float]) -> float:
    """Deviazione assoluta mediana: mediana degli scarti dalla mediana."""
    med = _median(values)
    return _median([abs(v - med) for v in values])


def earnings_volatility(eps_series: list[tuple[date, float]], years: int = 7) -> Optional[float]:
    """
    Volatilita' degli utili: deviazione standard delle variazioni YoY, in %.

    Il confronto anno su anno e' fatto per DATA. La versione precedente
    confrontava il punto i con il punto i-4 dando per scontata una serie
    trimestrale: sulle serie ANNUALI (fallback quando i trimestri EDGAR non
    bastano) quel passo confrontava valori distanti quattro anni, e la
    "volatilita' annua" che ne usciva era il tasso di crescita quadriennale.

    TRE CORREZIONI DI SCALA, senza le quali la misura non e' confrontabile fra
    societa' e la soglia "ciclica" diventa una monetina.

    1. DENOMINATORE CON PAVIMENTO. `(v - prev) / |prev|` esplode quando prev e'
       vicino a zero, ed e' vicino a zero ogni volta che una societa' attraversa
       il pareggio — cioe' esattamente nei casi che ci interessa misurare. Sul
       dataset S&P 500 + Russell 1000 questo produceva un massimo di 6,3e17 e una
       mediana di 89 su una soglia di 40: il 64% dell'universo la superava, e la
       classificazione "ciclica" diventava una funzione del rumore. Il
       denominatore e' ora `max(|prev|, 15% del livello tipico degli utili)`.

    2. RITAGLIO A ±200%. Oltre il ±200% il giudizio ("erratico") non cambia
       piu', ma in una misura di dispersione quel numero continua a pesare.

    3. DISPERSIONE ROBUSTA (MAD), NON DEVIAZIONE STANDARD. E' la correzione che
       conta di piu', e la ragione e' nella finestra: sette anni che finiscono
       oggi contengono il 2020. Il crollo e il rimbalzo del Covid hanno prodotto
       due variazioni annue enormi in societa' che non hanno niente di ciclico,
       e una deviazione standard — dominata dai quadrati — le trasformava
       nell'intera misura. Con lo scarto quadratico TJX segnava 76 e Ross 71,
       piu' di General Motors; Procter & Gamble segnava 73 per la sola
       svalutazione Gillette del 2019.

       La ciclicita' non e' "un colpo", e' "colpi ripetuti". La deviazione
       assoluta mediana (riscalata per 1,4826, cosi' che su dati normali
       coincida con la deviazione standard) ignora un episodio isolato e resta
       alta solo se le oscillazioni sono la norma. Sugli stessi titoli: TJX 21,
       Ross 25, P&G 4 — mentre Ford resta 223, Alcoa 186, Micron 174, Occidental
       118. Quello e' il segnale che serve.
    """
    if len(eps_series) < 5:
        return None
    s = sorted(eps_series)
    cutoff = s[-1][0].toordinal() - int(365.25 * years)
    pts = [(d, v) for d, v in s if d.toordinal() >= cutoff]
    if len(pts) < 5:
        return None

    # Livello tipico degli utili nella finestra: mediana dei valori assoluti.
    # Mediana e non media, per lo stesso motivo per cui la usa normalized_eps.
    floor = VOL_FLOOR_FRACTION * _median([abs(v) for _, v in pts])

    changes = []
    for i, (d, v) in enumerate(pts):
        target = d.toordinal() - 365
        prev = None
        best_gap = None
        for pd_, pv in pts[:i]:
            gap = abs(pd_.toordinal() - target)
            if best_gap is None or gap < best_gap:
                prev, best_gap = pv, gap
        if prev is None or best_gap > 75:
            continue
        den = max(abs(prev), floor)
        if den <= 0:
            continue
        change = (v - prev) / den * 100
        changes.append(max(-VOL_CLIP_PCT, min(VOL_CLIP_PCT, change)))
    if len(changes) < 3:
        return None
    return _mad(changes) * 1.4826


def eps_growth_trend(eps_series: list[tuple[date, float]],
                     years: int = 5) -> Optional[float]:
    """
    Crescita annua degli utili stimata come TENDENZA, non come rapporto fra due
    estremi: regressione dei minimi quadrati su log(EPS) nella finestra, e il
    coefficiente angolare riportato ad anni.

    PERCHE' NON IL CAGR. Il CAGR guarda due soli punti, quindi consegna il
    giudizio sulla societa' al trimestre di partenza e a quello di arrivo. Se il
    primo capita in una recessione, la "crescita a cinque anni" e' il rimbalzo;
    se capita in un picco, e' un crollo. Su questi due numeri poggia poi il
    MULTIPLO EQUO di categoria (fair P/E = crescita), che e' esattamente il
    punto in cui un fair value diventa privo di senso: PEG = 1 su una crescita
    misurata male e' un multiplo misurato male.

    La regressione usa TUTTI i punti della finestra, quindi un singolo trimestre
    anomalo la sposta di poco. In log, perche' la crescita e' composta: una
    retta su log(EPS) e' un tasso annuo costante.

    Restituisce None quando la finestra contiene valori non positivi (il
    logaritmo non esiste) o ha meno di sei punti: preferiamo dire "non
    calcolabile" che estrapolare da quattro trimestri.
    """
    if not eps_series:
        return None
    s = sorted(eps_series)
    cutoff = s[-1][0].toordinal() - int(365.25 * years)
    pts = [(d, v) for d, v in s if d.toordinal() >= cutoff]
    if len(pts) < 6 or any(v <= 0 for _, v in pts):
        return None

    t0 = pts[0][0].toordinal()
    xs = [(d.toordinal() - t0) / 365.25 for d, _ in pts]
    ys = [math.log(v) for _, v in pts]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx               # log-punti per anno
    # exp(slope) - 1 = tasso annuo composto. Limitato a ±100%/anno: oltre, la
    # retta sta descrivendo un rimbalzo da una base depressa, non una tendenza.
    rate = (math.exp(max(-2.0, min(2.0, slope))) - 1) * 100
    return max(-99.0, min(300.0, rate))


def loss_profile(eps_series: list[tuple[date, float]], years: int = 5) -> dict:
    """
    Anatomia delle perdite degli ultimi N anni, invece del solo si'/no.

    PERCHE' IL SI'/NO NON BASTAVA. La regola precedente — "un solo TTM negativo
    negli ultimi cinque anni => Turnaround, nessun multiplo" — classificava come
    societa' in ripresa dal dissesto il 36% dell'S&P 500 + Russell 1000:
    Amazon, AMD, AIG, Allstate. Sono societa' che hanno attraversato UN anno
    difficile e sono tornate a guadagnare, non turnaround nel senso di Lynch
    ("bail-us-out", "who-would-have-thunk-it": aziende sull'orlo del dissesto).
    Il costo dell'errore era doppio, perche' proprio a quelle societa' il
    programma rifiutava poi qualunque fair value.

    Cio' che distingue davvero un turnaround e' QUANDO e QUANTO:
      - sta ancora perdendo adesso                    -> turnaround pieno
      - ha smesso di perdere da poco (< 2 anni)       -> turnaround in uscita
      - perdite ripetute (>= 1/4 dei periodi)         -> utili inaffidabili
      - un solo episodio, chiuso da anni              -> non e' un turnaround

    Ritorna: {any, current, periods, share, quarters_since, episodes}
    """
    out = {"any": False, "current": False, "periods": 0, "share": 0.0,
           "quarters_since": None, "episodes": 0}
    if not eps_series:
        return out
    s = sorted(eps_series)
    last_date = s[-1][0]
    cutoff = last_date.toordinal() - int(365.25 * years)
    window = [(d, v) for d, v in s if d.toordinal() >= cutoff]
    if not window:
        return out

    negatives = [(d, v) for d, v in window if v < 0]
    out["any"] = bool(negatives)
    out["periods"] = len(negatives)
    out["share"] = len(negatives) / len(window)
    out["current"] = window[-1][1] < 0
    if negatives:
        out["quarters_since"] = (last_date - negatives[-1][0]).days / 365.25
        # Episodi distinti: due perdite separate da almeno un anno di utili
        # sono due crisi, non una lunga. Una societa' con un solo episodio
        # chiuso e' un'altra cosa da una che ci ricade ogni due anni.
        episodes, prev_d = 1, negatives[0][0]
        for d, _ in negatives[1:]:
            if (d - prev_d).days > 365:
                episodes += 1
            prev_d = d
        out["episodes"] = episodes
    return out


def had_recent_losses(eps_series: list[tuple[date, float]], years: int = 5) -> bool:
    """True se negli ultimi N anni c'e' stato almeno un TTM in perdita.

    Conservata perche' e' il dato grezzo esportato nel CSV e usato dal controllo
    di coerenza della dashboard. Per CLASSIFICARE si usa loss_profile(), che
    distingue una crisi in corso da un anno storto di dieci anni fa.
    """
    return loss_profile(eps_series, years)["any"]


# Soglia di volatilita' degli utili oltre la quale una societa' di settore
# ciclico viene classificata come "Ciclica".
#
# TARATA SUI DATI, non scelta a priori. Sul dataset S&P 500 + Russell 1000 la
# nuova misura ha mediana 46 sull'intero universo, 43 sui settori ciclici e
# terzo quartile 85. A 50 restano fuori KO (16), Microsoft (10), Waste
# Management (16), TJX (21), Ross (25), McDonald's (27), UPS (31) e restano
# dentro SLB (54), Freeport (72), Devon (77), Thor (80), Occidental (118),
# Cleveland-Cliffs (141), Alcoa (186), Ford (223). Seleziona il 45% dei settori
# ciclici e il 20% dell'universo.
#
# Il caso limite noto e' General Motors a 49, che resta appena fuori. Sbagliare
# per eccesso di prudenza sarebbe preferibile — per una ciclica il P/E corrente
# inganna — ma abbassare la soglia per catturarla vi farebbe entrare anche
# American Tower (48), che ciclica non e'.
CYCLICAL_VOL_THRESHOLD = 50.0

# Settori strutturalmente ciclici (classificazione yfinance)
CYCLICAL_SECTORS = {
    "Energy", "Basic Materials", "Industrials",
    "Consumer Cyclical", "Real Estate",
}

# INDUSTRIE CHE STANNO IN UN SETTORE CICLICO SENZA ESSERE CICLICHE.
#
# Il settore da solo e' troppo grosso per questa decisione. "Industrials" nella
# tassonomia di yfinance contiene le acciaierie e i processori di buste paga;
# "Consumer Cyclical" contiene i produttori di automobili e Amazon; "Real
# Estate" contiene gli uffici e i REIT delle torri di telecomunicazione. Dare a
# tutti loro il multiplo prudente delle cicliche (P/E 12 su utili di meta'
# ciclo) significa produrre proprio quei fair value senza senso che il modello
# dovrebbe evitare.
#
# Il criterio per stare in questo elenco: gli utili dell'industria NON oscillano
# con il ciclo delle materie prime o degli investimenti in capacita' produttiva.
# Se hanno oscillato lo stesso, e' stato per uno shock (il Covid sui viaggi) o
# per una voce straordinaria — e uno shock non e' un ciclo. La volatilita' li
# segnala comunque nella scheda; semplicemente non gli impone il multiplo.
NON_CYCLICAL_INDUSTRIES = {
    # servizi e piattaforme finite dentro Industrials / Consumer Cyclical
    "Specialty Business Services", "Consulting Services",
    "Staffing & Employment Services", "Security & Protection Services",
    "Waste Management", "Personal Services", "Education & Training Services",
    "Internet Retail", "Travel Services",
    # contratti pluriennali, non ciclo delle materie prime
    "Aerospace & Defense",
    # REIT il cui affitto non segue il ciclo industriale
    "REIT - Specialty", "REIT - Healthcare Facilities", "REIT - Residential",
    "REIT - Industrial", "Real Estate Services",
}


# Multiplo di ripresa applicato agli utili normalizzati di un turnaround. Non e'
# un PEG: la crescita di una societa' che esce da una crisi e' il rimbalzo, non
# una tendenza, e usarla come multiplo produrrebbe i numeri assurdi che questo
# modulo esiste per evitare. E' un multiplo prudente, sotto la media di mercato.
TURNAROUND_RECOVERY_PE = 10.0

# Multiplo delle cicliche, applicato agli utili NORMALIZZATI (di meta' ciclo).
CYCLICAL_PE = 12.0

# Fast grower: oltre questo multiplo non si paga, qualunque sia la crescita.
FAST_GROWER_PE_CAP = 25.0

# Tetto al multiplo quando la crescita alta e' misurata da una base in perdita:
# e' un rimbalzo, non una tendenza (vedi il ramo "Stalwart (recovering)").
REBOUND_PE_CAP = 15.0

# Slow grower: pavimento e tetto del multiplo.
SLOW_GROWER_PE_FLOOR, SLOW_GROWER_PE_CAP = 6.0, 12.0

# Soglie di crescita fra le categorie (CAGR degli utili, %).
FAST_GROWER_MIN_GROWTH = 20.0
STALWART_MIN_GROWTH = 10.0

# Capitalizzazione oltre la quale una stalwart e' "large cap".
STALWART_LARGE_CAP = 5e10


def growth_estimate(eps_series: list[tuple[date, float]]) -> dict:
    """
    La stima di crescita su cui poggia il multiplo equo, con la sua provenienza.

    PERCHE' UNA SCALA E NON UN NUMERO. Il fair value di categoria e' `EPS x
    crescita` (PEG = 1): la crescita non e' un dato accessorio, e' il moltiplicatore.
    Prendere il solo CAGR a 5 anni significava due cose, entrambe dannose:

      - quando il CAGR non era calcolabile — e non lo e' per 299 societa' su 989,
        perche' il valore di partenza era una perdita — la societa' finiva in
        "Unclassified" e restava senza alcun fair value;
      - quando era calcolabile, dipendeva da due soli trimestri.

    La scala, dal piu' affidabile al meno:
      1. TENDENZA a 5 anni (regressione su log EPS): usa tutti i punti;
      2. CAGR a 5 anni: due estremi, ma la finestra giusta;
      3. TENDENZA a 3 anni;
      4. CAGR a 3 anni: finestra corta, si dichiara.

    Ritorna {value, basis, confidence}. `basis` finisce nell'interfaccia: chi
    legge un multiplo equo di 14 ha diritto di sapere da quale misura viene.
    """
    trend5 = eps_growth_trend(eps_series, years=5)
    if trend5 is not None:
        return {"value": trend5, "basis": "5-year trend (regression on all points)",
                "confidence": "high", "rebound_risk": False}
    # Da qui in giu' la regressione non e' stata possibile, e la ragione e'
    # sempre la stessa: nella finestra ci sono utili non positivi. Il che
    # significa che ogni tasso calcolato su quella finestra parte da una base
    # depressa e misura in buona parte il rimbalzo. Lo diciamo, e chi classifica
    # ne tiene conto invece di promuovere la societa' a fast grower.
    cagr5 = eps_cagr(eps_series, years=5)
    if cagr5 is not None:
        return {"value": cagr5, "basis": "5-year CAGR (two endpoints, window "
                                         "contains losses)",
                "confidence": "medium", "rebound_risk": True}
    trend3 = eps_growth_trend(eps_series, years=3)
    if trend3 is not None:
        return {"value": trend3, "basis": "3-year trend — no usable 5-year history",
                "confidence": "low", "rebound_risk": False}
    cagr3 = eps_cagr(eps_series, years=3)
    if cagr3 is not None:
        return {"value": cagr3, "basis": "3-year CAGR — no usable 5-year history",
                "confidence": "low", "rebound_risk": True}
    return {"value": None, "basis": "not calculable", "confidence": "low",
            "rebound_risk": False}


def classify_lynch(
    growth_pct: Optional[float],
    eps_now: Optional[float],
    volatility: Optional[float],
    sector: Optional[str],
    dividend_yield: Optional[float],
    price_to_book: Optional[float],
    market_cap: Optional[float],
    recent_losses: bool = False,
    *,
    losses: Optional[dict] = None,
    eps_normalized: Optional[float] = None,
    growth_basis: str = "5-year CAGR",
    growth_confidence: str = "medium",
    rebound_risk: bool = False,
    industry: Optional[str] = None,
    classification_source: str = "yfinance",
) -> dict:
    """
    Classifica una societa' nelle sei categorie di Peter Lynch e assegna
    l'ANCORA DI VALUTAZIONE corrispondente.

    Le categorie (da 'One Up on Wall Street'):
      - turnaround   : societa' in dissesto o appena uscita. Gli utili correnti
                       non descrivono la societa' a regime -> multiplo prudente
                       sugli utili NORMALIZZATI.
      - asset play   : quota sotto il valore degli asset (P/B < 1). Il valore sta
                       nel patrimonio -> ancora sul patrimonio netto, non sul P/E.
      - cyclical     : utili erratici legati al ciclo. Il P/E corrente inganna
                       (basso ai massimi del ciclo) -> multiplo prudente su utili
                       normalizzati di meta' ciclo.
      - fast grower  : crescita > 20%. Multiplo = crescita, con cap a 25.
      - stalwart     : crescita 10-20%. Multiplo = crescita (PEG = 1).
      - slow grower  : crescita < 10%. Multiplo = crescita + dividendo, 6-12.

    OGNI CATEGORIA HA UN'ANCORA. Nella versione precedente quattro categorie su
    dieci uscivano con `fair_pe = None`, e siccome il fair value nasce da li',
    meta' del dataset (494 righe su 989) non aveva alcuna valutazione. Non era
    una scelta: era la conseguenza di non aver deciso cosa fare quando il P/E
    non e' lo strumento giusto. Lynch, in quei casi, non smette di valutare —
    cambia strumento: il patrimonio per un asset play, gli utili di un anno
    normale per un turnaround. E' quello che fa ora questa funzione, dichiarando
    ogni volta QUALE ancora ha usato (`anchor`) e su quali utili (`eps_base`),
    invece di restituire un multiplo muto.

    L'ORDINE DI VERIFICA CONTA. Le condizioni che invalidano il P/E vengono
    prima delle fasce di crescita, perche' una societa' in perdita con crescita
    del 40% non e' una fast grower: quel 40% e' un rimbalzo.

    Ritorna: {category, fair_pe, eps_base, anchor, fair_pb, basis, note,
              confidence, confidence_note}
    """
    g = growth_pct
    div = dividend_yield or 0.0
    lp = losses or {"any": bool(recent_losses), "current": bool(recent_losses),
                    "periods": 1 if recent_losses else 0,
                    "share": 0.5 if recent_losses else 0.0,
                    "quarters_since": 0.0 if recent_losses else None,
                    "episodes": 1 if recent_losses else 0}

    reasons: list[str] = []
    if growth_pct is None:
        reasons.append("growth not calculable on any window")
    elif growth_confidence == "low":
        reasons.append(f"growth measured on a short window ({growth_basis})")
    # BASE DI UTILI INSTABILE. Quando l'ultimo TTM e la mediana a tre anni
    # divergono di piu' di tre volte, non esiste "l'utile" di questa societa':
    # ce ne sono due molto diversi, e il fair value dipende interamente da quale
    # si sceglie. E' la causa piu' frequente dei fair value estremi in entrambe
    # le direzioni — Teradata (corrente 4,37$ contro 0,98$ normalizzato) esce a
    # 3,6 volte il prezzo, Qorvo (3,62$ contro 0,29$) al 2% del prezzo.
    # Non si puo' decidere quale sia quello giusto da qui, ma si puo' — e si
    # deve — dire che la scelta c'e' ed e' determinante.
    if (eps_now and eps_now > 0 and eps_normalized and eps_normalized > 0
            and max(eps_now / eps_normalized, eps_normalized / eps_now) > 3.0):
        reasons.append(
            f"unstable earnings base: latest TTM ${eps_now:,.2f} vs 3-year "
            f"median ${eps_normalized:,.2f} — the fair value depends on which "
            "one is used")
    if str(classification_source).startswith(("sic", "none")):
        # Non e' solo la ciclicita' a dipendere dal settore: ci dipende anche
        # tutto il confronto con i pari. Se la classificazione e' una
        # supposizione, chi legge deve saperlo prima di fidarsi dei delta.
        reasons.append("sector/industry guessed from the SEC SIC code — the "
                       "market data provider had no profile for this symbol")
    if volatility is not None and volatility > CYCLICAL_VOL_THRESHOLD:
        reasons.append(f"erratic earnings (volatility {volatility:.0f})")

    def out(category, *, fair_pe=None, eps_base="current", anchor="earnings",
            fair_pb=None, basis="", note="", extra_reasons=(), floor_conf=None):
        rs = list(reasons) + list(extra_reasons)
        conf = "high" if not rs else ("medium" if len(rs) == 1 else "low")
        if floor_conf == "low":
            conf = "low"
        elif floor_conf == "medium" and conf == "high":
            conf = "medium"
        return {
            "category": category, "fair_pe": fair_pe, "eps_base": eps_base,
            "anchor": anchor, "fair_pb": fair_pb, "basis": basis, "note": note,
            "confidence": conf,
            "confidence_note": ("; ".join(rs) if rs
                                else "consistent earnings, growth measured over "
                                     "the full 5-year window"),
        }

    norm_ok = eps_normalized is not None and eps_normalized > 0
    cheap_on_book = price_to_book is not None and 0 < price_to_book < 1.0

    # -----------------------------------------------------------------
    # 1) NESSUNA BASE DI UTILI — in perdita ora e anche in media su 3 anni.
    # Qui non c'e' un P/E da correggere: non c'e' un denominatore.
    # -----------------------------------------------------------------
    if (eps_now is None or eps_now <= 0) and not norm_ok:
        if cheap_on_book:
            return out("Asset Play", anchor="book", fair_pb=1.0,
                       basis="net asset value (no earnings to capitalise)",
                       note=(f"Loss-making and trading at {price_to_book:.2f}x book "
                             "value: the only floor left is the balance sheet. "
                             "Valued on assets, not on earnings."),
                       floor_conf="low")
        return out("Turnaround", anchor="none",
                   basis="no positive earnings, current or normalized",
                   note=("Loss-making now and on a 3-year average: no earnings "
                         "base exists to capitalise. Any P/E-derived fair value "
                         "would be invented. Assess the cash burn, the runway "
                         "and the balance sheet instead."),
                   floor_conf="low")

    # -----------------------------------------------------------------
    # 2) TURNAROUND — nel senso di Lynch: dissesto in corso o appena chiuso.
    #
    # La regola precedente ("una sola perdita in cinque anni") classificava qui
    # 359 societa' su 989 — Amazon, AMD, AIG, Allstate — e a tutte negava poi il
    # fair value. Un anno storto non e' un turnaround. Servono almeno una di:
    #   - perdita in corso;
    #   - ultima perdita da meno di un anno (la ripresa non e' confermata);
    #   - piu' episodi distinti di perdita, l'ultimo non ancora lontano.
    #
    # LA SOGLIA E' UN ANNO, NON DUE, PER VIA DEL TTM. La serie e' su dodici mesi
    # mobili: un solo trimestre in perdita tiene il TTM sotto zero per i quattro
    # trimestri successivi. "Ultima perdita un anno fa" sulla serie TTM
    # corrisponde quindi a circa due anni dal trimestre che l'ha causata. Con la
    # soglia a due anni finivano qui 109 societa' tornate all'utile da tempo.
    #
    # E "RICORRENTE" VUOL DIRE PIU' EPISODI, non "molti trimestri". La quota di
    # periodi in perdita era il criterio sbagliato per la stessa ragione: un
    # unico anno storto smerigliato dal TTM occupa da solo un quarto della
    # finestra. Allstate, in perdita nel 2022-23 e a 38$ di utile per azione nel
    # 2025, veniva classificata societa' in dissesto.
    # -----------------------------------------------------------------
    since = lp.get("quarters_since")
    recurring = lp.get("episodes", 0) > 1 and (since is None or since < 3.0)
    fresh = since is not None and since < 1.0
    if lp.get("current") or fresh or (lp.get("any") and recurring):
        if lp.get("current"):
            why = "currently loss-making"
        elif fresh:
            why = f"back to profit only {since:.1f} years ago"
        else:
            why = (f"recurring losses ({lp.get('episodes')} separate episodes, "
                   f"the last {since:.1f} years ago)")
        if norm_ok:
            return out("Turnaround", fair_pe=TURNAROUND_RECOVERY_PE,
                       eps_base="normalized",
                       basis=f"P/E {TURNAROUND_RECOVERY_PE:.0f} on 3-year normalized "
                             f"earnings — {why}",
                       note=("A recovery multiple, not a PEG multiple: the growth "
                             "rate of a company coming out of losses measures the "
                             "rebound, not a trend, and using it as a multiple is "
                             "how a fair value becomes nonsense. Applied to "
                             "normalized earnings, and deliberately below the "
                             "market average."),
                       floor_conf="low")
        return out("Turnaround", anchor="none",
                   basis=f"earnings not normalizable — {why}",
                   note=("Losses too recent or too frequent for the P/E to be a "
                         "reliable basis. Assess the margin recovery and the "
                         "balance sheet strength."),
                   floor_conf="low")

    # Una perdita isolata e chiusa da anni NON declassa la societa': viene solo
    # dichiarata, perche' abbassa la fiducia nella crescita misurata su quella
    # finestra (il CAGR parte da una base depressa).
    healed = []
    if lp.get("any"):
        healed.append(f"one loss episode {since:.0f} years ago, profitable since")

    # -----------------------------------------------------------------
    # 3) ASSET PLAY — quota sotto il patrimonio netto contabile.
    # -----------------------------------------------------------------
    if cheap_on_book:
        return out("Asset Play", anchor="book", fair_pb=1.0,
                   basis="net asset value (fair P/B = 1)",
                   note=(f"Trading at {price_to_book:.2f}x book value: the market "
                         "prices the company below its accounting net worth. The "
                         "anchor is the balance sheet, not the P/E. Book value is "
                         "a starting point, not an appraisal — it understates land "
                         "carried at cost and overstates obsolete inventory."),
                   extra_reasons=healed, floor_conf="medium")

    # -----------------------------------------------------------------
    # 4) CICLICA — settore ciclico, industria non esclusa, utili erratici.
    #
    # TRE CONDIZIONI, NON DUE. Alle due originali (settore + volatilita') se ne
    # aggiungono altrettante clausole di sicurezza, perche' questo ramo impone un
    # multiplo fisso di 12 e quindi un fair value sbagliato qui e' costoso:
    #
    #   - l'INDUSTRIA non deve essere fra quelle non cicliche. Il settore da solo
    #     metteva in questo ramo Amazon ed eBay ("Internet Retail" dentro
    #     Consumer Cyclical), Booking ("Travel Services"), i REIT delle torri e
    #     quelli sanitari, i contractor della difesa.
    #
    #   - la CLASSIFICAZIONE deve essere affidabile. Quando il settore non viene
    #     dal provider ma dal ripiego sul codice SIC, e' una supposizione: il SIC
    #     7389 ("Business Services, NEC") e' un contenitore in cui stanno Visa,
    #     Accenture, Uber e Fiserv, e ripiegarci sopra piazzava Fiserv in
    #     Industrials — settore ciclico — per poi valutarla come una ciclica.
    #     Una supposizione non basta a far scattare un multiplo fisso.
    # -----------------------------------------------------------------
    sector_reliable = not str(classification_source).startswith(("sic", "none"))
    industry_ok = (industry or "") not in NON_CYCLICAL_INDUSTRIES
    if sector in CYCLICAL_SECTORS and sector_reliable and industry_ok \
            and volatility is not None \
            and volatility > CYCLICAL_VOL_THRESHOLD:
        if norm_ok:
            return out("Cyclical", fair_pe=CYCLICAL_PE, eps_base="normalized",
                       basis=f"P/E {CYCLICAL_PE:.0f} on 3-year normalized (mid-cycle) "
                             "earnings",
                       note=("Erratic, cycle-driven earnings: a low P/E can signal "
                             "the peak of the cycle, not a bargain. The multiple is "
                             "applied to mid-cycle earnings, so the fair value does "
                             "not follow the peak."),
                       extra_reasons=healed)
        return out("Cyclical", fair_pe=CYCLICAL_PE, eps_base="current",
                   basis=f"P/E {CYCLICAL_PE:.0f} on current earnings — no "
                         "normalized figure available",
                   note=("Erratic, cycle-driven earnings. Ideally the multiple "
                         "applies to mid-cycle earnings, but this company has no "
                         "usable 3-year median, so it is applied to the current "
                         "figure: if these are peak earnings, the fair value is "
                         "overstated."),
                   extra_reasons=healed, floor_conf="low")

    # -----------------------------------------------------------------
    # 5) FASCE DI CRESCITA (PEG = 1)
    # -----------------------------------------------------------------
    if g is None:
        return out("Unclassified", anchor="none",
                   basis="growth not calculable on any window",
                   note=("Not enough usable earnings history to estimate growth on "
                         "any of the four windows tried (5-year trend and CAGR, "
                         "3-year trend and CAGR). No category multiple is assigned: "
                         "the fixed P/E 15 line is the only reading available."),
                   extra_reasons=healed, floor_conf="low")

    if g > FAST_GROWER_MIN_GROWTH:
        # IL RIMBALZO NON E' CRESCITA. Quando la finestra contiene perdite, il
        # tasso e' calcolato da una base depressa e sovrastima sistematicamente:
        # una societa' che passa da -1$ a +2$ di utile per azione esce con
        # percentuali a tre cifre, entra fra le fast grower e si prende un
        # multiplo di 25. E' il modo piu' rapido per produrre un fair value
        # senza senso, ed e' esattamente cio' contro cui Lynch mette in guardia
        # quando distingue una societa' in crescita da una in ripresa. In quel
        # caso il multiplo si ferma al livello di una stalwart.
        # Anche qui il dividendo, per la stessa ragione di continuita' sul
        # confine del 20% (una fast grower ne paga di rado, quindi in pratica
        # e' zero; ma il modello non deve avere gradini).
        if rebound_risk:
            return out("Stalwart (recovering)", fair_pe=REBOUND_PE_CAP,
                       basis=f"P/E {REBOUND_PE_CAP:.0f} — growth of {g:.0f}% measured "
                             f"from a loss-making base ({growth_basis})",
                       note=("The measured growth rate is a rebound off depressed "
                             "earnings, not a trend, so it does not earn a fast "
                             "grower's multiple. The multiple is capped at the "
                             "stalwart level until the company has a full window "
                             "of profitable history."),
                       extra_reasons=healed, floor_conf="low")
        return out("Fast Grower", fair_pe=min(g + div, FAST_GROWER_PE_CAP),
                   basis=f"P/E = growth {g:.0f}% + dividend {div:.1f}% "
                         f"(capped at {FAST_GROWER_PE_CAP:.0f}) · {growth_basis}",
                   note=("High growth: the multiple tracks the growth rate but is "
                         f"capped at {FAST_GROWER_PE_CAP:.0f}, because very few "
                         "companies sustain that pace for years."),
                   extra_reasons=healed)

    if g >= STALWART_MIN_GROWTH:
        if market_cap is None:
            label = "Stalwart (size unknown)"
        else:
            label = ("Stalwart" if market_cap > STALWART_LARGE_CAP
                     else "Stalwart (mid cap)")
        # IL DIVIDENDO ENTRA QUI COME NELLE SLOW GROWER, e non e' un dettaglio
        # estetico: senza, il modello si INVERTIVA sul confine del 10%. Una
        # societa' al 9,9% prendeva min(9,9 + dividendo, 12) = 11,9; la stessa
        # societa' al 10,0% prendeva 10,0 secco. Passare nella categoria
        # migliore faceva SCENDERE il fair value del 16%.
        # Lynch somma sempre il rendimento alla crescita quando misura cosa
        # rende un titolo; applicarlo solo sotto il 10% era un'incoerenza.
        return out(label, fair_pe=g + div,
                   basis=f"P/E = growth {g:.0f}% + dividend {div:.1f}% · "
                         f"{growth_basis}",
                   note=("Solid, predictable growth: the fair P/E matches the "
                         "growth rate plus the dividend yield (PEG = 1)."),
                   extra_reasons=healed)

    # 6) SLOW GROWER — crescita sotto il 10%, dividendo incluso.
    #
    # Anche a crescita NEGATIVA. La versione precedente restituiva qui
    # `fair_pe = None` per 82 societa', motivando che "un multiplo pari alla
    # crescita non ha senso se la crescita e' negativa" — vero, ma la formula
    # non e' "pari alla crescita": e' `crescita + dividendo`, con un PAVIMENTO a
    # 6 che esiste proprio per questo. Il pavimento gia' esprime il giudizio
    # giusto ("una societa' ferma ma solida non vale 2-3 volte gli utili") e
    # vale identico quando la crescita e' -3%. Lasciare 82 societa' senza alcuna
    # valutazione era una lacuna, non una posizione.
    #
    # Alla crescita negativa non si concede pero' credito: entra nella formula
    # come zero, e il multiplo si applica agli utili normalizzati quando ci sono,
    # perche' l'ultimo TTM di una societa' in calo e' il punto piu' basso.
    growth_credit = max(g, 0.0)
    fair = min(max(growth_credit + div, SLOW_GROWER_PE_FLOOR), SLOW_GROWER_PE_CAP)
    div_txt = (f"dividend {div:.1f}%" if dividend_yield is not None
               else "dividend not available (treated as 0)")
    # LA BASE NON DIPENDE DALLA CATEGORIA — in nessuna delle tre fasce di
    # crescita. E' la condizione perche' il fair value sia continuo.
    #
    # Prima il ramo "declining" usava gli utili normalizzati e gli altri
    # l'ultimo TTM, e ogni confine in cui la base cambiava produceva un salto:
    # per una societa' con mediana a 3$ e TTM a 4$, il fair value saltava del
    # 33% passando da -0,1% a +0,1% di crescita. Spostare la mediana su tutta la
    # fascia slow grower non risolveva, spostava soltanto il gradino sul confine
    # del 10%, dove il salto misurato era del 34%.
    #
    # Un gradino del 34% innescato da due decimi di punto di crescita — cioe'
    # dal rumore della misura — e' esattamente il difetto che rende un modello
    # inaffidabile: due societa' identiche finiscono valutate una un terzo piu'
    # dell'altra per il modo in cui e' caduto un arrotondamento.
    #
    # Le fasce di crescita usano quindi tutte l'ULTIMO TTM. La prudenza verso
    # chi ha utili in calo resta, ma passa dal MULTIPLO (nessun credito alla
    # crescita negativa) invece che dalla base — e i casi in cui l'ultimo TTM
    # non e' rappresentativo hanno ora due presidi dedicati: la segnalazione di
    # base instabile qui sopra e il cancello sul P/E massimo utilizzabile in
    # fair_value_check(). Cicliche e turnaround continuano a usare la mediana,
    # ma i loro rami sono governati dal profilo delle perdite e dalla
    # volatilita', non dalla crescita: non c'e' un confine che si attraversa
    # per variazione continua.
    if g < 0:
        return out("Slow Grower (declining earnings)", fair_pe=fair,
                   basis=f"P/E = 0% growth credit + {div_txt} "
                         f"(floor {SLOW_GROWER_PE_FLOOR:.0f}) · {growth_basis}",
                   note=("Earnings are contracting, so the model gives no credit "
                         "for growth: the multiple comes from the dividend and "
                         "the floor alone. Applied to normalized earnings where "
                         "available, since the latest figure of a declining "
                         "company is its lowest point. Check whether the decline "
                         "is cyclical or structural."),
                   extra_reasons=healed + [f"earnings declining {g:.0f}%/year"])

    return out("Slow Grower", fair_pe=fair,
               basis=f"P/E = growth {g:.0f}% + {div_txt} "
                     f"(floor {SLOW_GROWER_PE_FLOOR:.0f}, cap {SLOW_GROWER_PE_CAP:.0f}) "
                     f"· {growth_basis}",
               note=("Modest growth: deserves a contained multiple. The dividend "
                     "partly offsets the lower growth."),
               extra_reasons=healed)


def lynch_ratio(growth_pct: Optional[float], dividend_yield: Optional[float],
                pe_ratio: Optional[float]) -> Optional[float]:
    """
    Lynch ratio = (crescita % + rendimento dividendo %) / P/E effettivo.
    Lettura: >1.5 interessante · ~1 equo · <1 caro.
    """
    if not pe_ratio or pe_ratio <= 0 or growth_pct is None:
        return None
    return (growth_pct + (dividend_yield or 0.0)) / pe_ratio


def ttm_growth_yoy_dated(eps_series: list[tuple[date, float]],
                         tol_days: int = 60) -> Optional[float]:
    """
    Crescita YoY robusta basata sulle DATE reali (non sugli indici).
    Confronta l'ultimo valore con quello ~365 giorni prima (entro tol_days).
    Evita il gonfiaggio quando la serie ha spaziatura irregolare o buchi.
    """
    if len(eps_series) < 2:
        return None
    series = sorted(eps_series)
    last_date, last_val = series[-1]
    target = last_date.toordinal() - 365
    # cerca il punto piu' vicino a un anno prima
    best = None
    best_gap = None
    for d, v in series[:-1]:
        gap = abs(d.toordinal() - target)
        if best is None or gap < best_gap:
            best, best_gap = (d, v), gap
    if best is None or best_gap > tol_days:
        return None
    prev_val = best[1]
    if prev_val == 0:
        return None
    return (last_val - prev_val) / abs(prev_val) * 100.0


def ttm_growth_yoy(eps_series: list[tuple[date, float]]) -> Optional[float]:
    """
    Crescita EPS YoY corretta: confronta l'ultimo EPS annualizzato con quello
    di ~4 punti prima (per serie TTM trimestrale = 1 anno). Per serie annuale,
    confronta ultimo vs penultimo. Ritorna percentuale.
    """
    if len(eps_series) < 2:
        return None
    latest = eps_series[-1][1]
    # distanza tipica: se la serie e' trimestrale, ~4 passi indietro = 1 anno
    # euristica: se abbiamo >=5 punti e i due ultimi distano <200gg, e' trimestrale
    idx_prev = -2
    if len(eps_series) >= 5:
        gap = (eps_series[-1][0] - eps_series[-2][0]).days
        if gap < 200:  # trimestrale
            idx_prev = -5
    prev = eps_series[idx_prev][1]
    if prev == 0:
        return None
    return (latest - prev) / abs(prev) * 100.0


# ===========================================================================
# BILANCIO E CONTO ECONOMICO — grandezze oltre l'EPS
#
# Serve alla sezione "Financials" della scheda titolo: ricavi, utile netto,
# free cash flow, patrimonio, attivo, debito a lungo termine. Tutto dagli
# stessi companyfacts EDGAR gia' scaricati per l'EPS, quindi senza nuove
# dipendenze ne' chiamate a pagamento.
#
# Due forme di fatto XBRL, che vanno trattate diversamente:
#   - DURATA  (conto economico, rendiconto finanziario): hanno start e end.
#   - ISTANTE (stato patrimoniale): hanno solo end. E' questo che li distingue.
#
# E il punto delicato: nel rendiconto finanziario i valori sono CUMULATI DA
# INIZIO ESERCIZIO (year-to-date), non per trimestre. Il fatto Q3 di Apple
# copre nove mesi, non tre. Sommare quattro fatti YTD darebbe un numero
# gonfiato di circa 2,5 volte. Vanno prima differenziati (vedi
# build_quarterly_flow).
# ===========================================================================

# Ricavi. L'ordine e' di preferenza: dal 2018 (ASC 606) il tag corretto e'
# RevenueFromContractWithCustomer*, prima si usava SalesRevenueNet/Revenues.
# Per avere cinque anni pieni di storia i concetti vengono UNITI (vedi
# extract_flow_facts): il preferito vince dove c'e', gli altri riempiono i buchi.
REVENUE_TAG_CANDIDATES = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "RevenuesNetOfInterestExpense",          # banche
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
    "TotalRevenuesAndOtherIncome",           # oil & gas
)

# Risultato operativo (EBIT). Per le banche OperatingIncomeLoss spesso manca e
# l'equivalente e' l'utile ante imposte.
OPERATING_INCOME_TAG_CANDIDATES = (
    "OperatingIncomeLoss",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
)

OCF_TAG_CANDIDATES = (
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
)

CAPEX_TAG_CANDIDATES = (
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
    "PaymentsForCapitalImprovements",
    "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets",
)

DIVIDENDS_PAID_TAG_CANDIDATES = (
    "PaymentsOfDividendsCommonStock",
    "PaymentsOfDividends",
)

# Oneri finanziari. Servono a UNA cosa: il costo del debito effettivamente
# pagato da questa societa' (interessi / debito medio), che e' la componente
# del costo del capitale che si puo' leggere da un bilancio invece di stimarla.
#
# L'ORDINE E' DAL PIU' PULITO AL PIU' SPORCO. `InterestExpenseDebt` e' proprio
# quello che si cerca. `InterestExpense` include spesso anche gli interessi sui
# leasing e su altre passivita', che gonfiano leggermente il tasso. Il netto
# (`InterestIncomeExpenseNet`) e' l'ultimo perche' sottrae gli interessi
# ATTIVI: su una societa' con molta cassa puo' risultare negativo, e un costo
# del debito negativo non e' un costo del debito.
INTEREST_EXPENSE_TAG_CANDIDATES = (
    "InterestExpenseDebt",
    "InterestExpense",
    "InterestExpenseNonoperating",
    "InterestAndDebtExpense",
)

ASSETS_TAG_CANDIDATES = ("Assets",)

# Patrimonio netto. Il primo esclude le minoranze (definizione corretta per il
# ROE dell'azionista ordinario), il secondo le include ed e' il fallback.
EQUITY_TAG_CANDIDATES = (
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    "MembersEquity",
    "PartnersCapital",
)

# DEBITO: PRIMA I TAG GENERICI, POI QUELLI DELLO STRUMENTO.
#
# L'ordine e' la regola che tiene in piedi tutto il resto. `extract_instant_series`
# sceglie, PER OGNI DATA, il tag meglio classificato fra quelli che hanno un
# valore a quella data: non somma i candidati. Quindi una societa' che deposita
# sia `LongTermDebtNoncurrent` (il totale) sia `ConvertibleDebtNoncurrent` (una
# sua componente) usa il primo e ignora il secondo, senza contare due volte;
# una che deposita solo il secondo ottiene finalmente un dato invece di niente.
#
# PERCHE' SERVIVA. Super Micro tagga il proprio debito come
# `ConvertibleLongTermNotesPayable` — sono obbligazioni convertibili, e per il
# filer sono quelle, non "debito a lungo termine generico". Il tag generico
# esiste nei suoi depositi ma si ferma a marzo 2024, quando la societa' ha
# cambiato modo di taggare: il controllo di obsolescenza scartava (giustamente)
# un dato di due anni prima, e la scheda dichiarava zero debito su una societa'
# che ne ha per miliardi. Su un campione di trentacinque societa' senza debito
# nel dataset, VENTICINQUE avevano una voce recente sotto un altro tag.
LONG_TERM_DEBT_TAG_CANDIDATES = (
    "LongTermDebtNoncurrent",
    "LongTermDebtAndCapitalLeaseObligations",
    "LongTermDebt",
    # Ultima risorsa fra i generici, e con una definizione piu' larga (include
    # le quote in scadenza entro l'anno): senza di essa le banche, che usano
    # solo questo tag, non hanno alcun dato sul debito.
    "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
    # --- specifici dello strumento, usati solo dove i generici tacciono ---
    "ConvertibleLongTermNotesPayable",
    "ConvertibleDebtNoncurrent",
    "SeniorNotesNoncurrent",
    "SecuredDebtNoncurrent",
    "UnsecuredDebtNoncurrent",
    "NotesPayableToBankNoncurrent",
    "LongTermNotesPayable",
    "LongTermLoansPayable",
    "LongTermLineOfCredit",
    "OtherLongTermDebtNoncurrent",
    # I leasing finanziari SONO debito: dal principio contabile ASC 842 stanno
    # in bilancio con il loro valore attuale, e ogni fornitore di dati li conta
    # come tali. Stanno per ultimi perche' vanno usati solo quando la societa'
    # non ha altro debito da dichiarare — molte catene di negozi e compagnie di
    # trasporto sono esattamente in quel caso. NON si sommano al debito
    # ordinario: dove entrambi esistono vince il debito ordinario, che e' la
    # scelta prudente finche' non si distingue una voce dall'altra.
    "FinanceLeaseLiabilityNoncurrent",
)

# Debito a breve: quota corrente del debito a lungo piu' i finanziamenti
# a breve. Serve al TOTALE del debito, che e' la grandezza che conta nella
# struttura del capitale: un debito a lungo modesto accanto a una montagna di
# carta commerciale in scadenza non e' una societa' poco indebitata.
SHORT_TERM_DEBT_TAG_CANDIDATES = (
    "DebtCurrent",
    "LongTermDebtCurrent",
    "ShortTermBorrowings",
    "OtherShortTermBorrowings",
    "CommercialPaper",
    # --- specifici dello strumento, stessa regola dei precedenti ---
    "ConvertibleDebtCurrent",
    "ConvertibleNotesPayableCurrent",
    "SeniorNotesCurrent",
    "SecuredDebtCurrent",
    "NotesPayableCurrent",
    "LoansPayableCurrent",
    "LinesOfCreditCurrent",
    "FinanceLeaseLiabilityCurrent",
)

# TOTALI GIA' PRONTI, come ultima risorsa.
#
# Alcuni filer non separano lungo e breve: dichiarano un solo importo
# complessivo. Non possono stare nelle due liste sopra — sommati a una parte
# corrente conterebbero quella parte due volte — quindi si usano solo quando
# lungo + breve non produce nulla a quella data.
TOTAL_DEBT_TAG_CANDIDATES = (
    "DebtLongtermAndShorttermCombinedAmount",
    "DebtAndCapitalLeaseObligations",
    "FinanceLeaseLiability",
)

# Cassa e disponibilita' liquide. Il secondo tag include la cassa vincolata ed
# e' quello che molti filer usano dal 2018 (ASU 2016-18); il terzo e' la voce
# delle banche, che non hanno "cash equivalents" nel senso industriale.
CASH_TAG_CANDIDATES = (
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    "CashAndDueFromBanks",
    "CashCashEquivalentsAndShortTermInvestments",
)

# Eta' massima di un dato di stato patrimoniale perche' descriva "adesso".
# Oltre, quasi sempre significa che la societa' ha cambiato tagging.
MAX_BALANCE_AGE_DAYS = 400


def _normalize_duration_facts(rows: list[dict], concept: str) -> list[dict]:
    """Fatti di DURATA (conto economico / rendiconto): richiedono start e end."""
    return _normalize_facts([r for r in rows if r.get("start")], concept)


def extract_flow_facts(companyfacts: dict, candidates: tuple[str, ...],
                       unit: str = "USD") -> list[dict]:
    """
    Fatti di durata per una grandezza economica, UNENDO piu' concetti XBRL.

    Il concetto piu' preferito vince su ogni periodo che copre; gli altri
    riempiono solo i buchi. Serve perche' le societa' cambiano tag nel tempo
    (ASC 606 nel 2018 ha spostato tutti i ricavi su un tag nuovo): prendendo un
    solo concetto la serie storica si interrompe e il CAGR a 5 anni sparisce
    proprio dove servirebbe.
    """
    gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    merged: dict[tuple[date, date], dict] = {}
    for rank, name in enumerate(candidates):
        rows = (gaap.get(name) or {}).get("units", {}).get(unit)
        if not rows:
            continue
        for f in _normalize_duration_facts(rows, name):
            key = (f["start"], f["end"])
            cur = merged.get(key)
            if cur is None:
                merged[key] = {**f, "_rank": rank}
            elif rank < cur["_rank"]:
                merged[key] = {**f, "_rank": rank}
            elif rank == cur["_rank"]:
                # stesso concetto, stesso periodo: vince il deposito piu' recente
                if (f.get("filed") or date.min) > (cur.get("filed") or date.min):
                    merged[key] = {**f, "_rank": rank}
    return sorted(merged.values(), key=lambda f: (f["end"], f["start"]))


def extract_instant_series(companyfacts: dict, candidates: tuple[str, ...],
                           unit: str = "USD") -> list[tuple[date, float]]:
    """
    Serie di ISTANTE (stato patrimoniale): [(data, valore), ...] ordinata.

    Riconosce i fatti di istante dall'assenza di 'start'. A parita' di data
    vince il deposito piu' recente (riflette le rettifiche successive).
    """
    gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    best: dict[date, tuple[int, date, float]] = {}
    for rank, name in enumerate(candidates):
        rows = (gaap.get(name) or {}).get("units", {}).get(unit)
        if not rows:
            continue
        for r in rows:
            if r.get("start") or not r.get("end") or r.get("val") is None:
                continue
            try:
                end = _to_date(r["end"])
                filed = _to_date(r["filed"]) if r.get("filed") else date.min
            except (ValueError, TypeError):
                continue
            cur = best.get(end)
            cand = (rank, filed, float(r["val"]))
            # rank piu' basso = concetto piu' preferito; a parita', filed piu' recente
            if cur is None or cand[0] < cur[0] or (cand[0] == cur[0] and cand[1] > cur[1]):
                best[end] = cand
    return sorted((d, v[2]) for d, v in best.items())


def build_quarterly_flow(facts: list[dict]) -> list[tuple[date, float]]:
    """
    Trimestri DISCRETI da fatti di durata, gestendo i valori cumulati YTD.

    Due sorgenti, in ordine di fiducia:
      1. Fatti gia' trimestrali (durata 80..100 giorni): valgono cosi' come sono.
      2. Differenza fra due cumulati consecutivi dello STESSO esercizio
         (stesso 'start'): Q3 = YTD_9mesi - YTD_6mesi. E' cosi' che si ricava il
         trimestre discreto dal rendiconto finanziario, che EDGAR pubblica solo
         in forma cumulata, e il Q4 dal bilancio annuale (FY - YTD_9mesi).

    Senza il passo 2, sommare quattro fatti di cash flow darebbe un "TTM" pari a
    circa due volte e mezzo il valore vero.
    """
    # dedupe per periodo esatto: vince il deposito piu' recente
    best: dict[tuple[date, date], dict] = {}
    for f in facts:
        key = (f["start"], f["end"])
        cur = best.get(key)
        if cur is None or (f.get("filed") or date.min) > (cur.get("filed") or date.min):
            best[key] = f
    facts = sorted(best.values(), key=lambda f: (f["start"], f["end"]))

    quarters: dict[date, float] = {}
    for f in facts:                                    # 1) trimestri diretti
        if QUARTER_MIN_DUR <= f["dur"] <= QUARTER_MAX_DUR:
            quarters[f["end"]] = f["val"]

    by_start: dict[date, list[dict]] = {}
    for f in facts:
        by_start.setdefault(f["start"], []).append(f)

    for _, group in by_start.items():                   # 2) differenze YTD
        group.sort(key=lambda f: f["end"])
        for prev, cur in zip(group, group[1:]):
            if cur["dur"] <= QUARTER_MAX_DUR:
                continue                                # gia' un trimestre discreto
            gap = (cur["end"] - prev["end"]).days
            if not (QUARTER_MIN_DUR <= gap <= QUARTER_MAX_DUR):
                continue                                # non sono consecutivi
            quarters.setdefault(cur["end"], cur["val"] - prev["val"])

    return sorted(quarters.items())


def build_annual_flow(facts: list[dict]) -> list[tuple[date, float]]:
    """Serie annuale FY da fatti di durata (durata 330..400 giorni)."""
    annual = _dedupe_by_end(
        [f for f in facts if ANNUAL_MIN_DUR <= f["dur"] <= ANNUAL_MAX_DUR])
    return sorted((e, f["val"]) for e, f in annual.items())


def combine_series(a: list[tuple[date, float]], b: list[tuple[date, float]],
                   op) -> list[tuple[date, float]]:
    """
    Combina due serie datate sulle sole date presenti in ENTRAMBE.

    Serve per il free cash flow = flusso operativo - investimenti in immobilizzi:
    allineare per data e' l'unico modo per non sottrarre periodi diversi.
    """
    bd = dict(b)
    return sorted((d, op(v, bd[d])) for d, v in a if d in bd)


def latest_value(series: list[tuple[date, float]],
                 max_age_days: Optional[int] = MAX_BALANCE_AGE_DAYS,
                 today: Optional[date] = None) -> Optional[tuple[date, float]]:
    """Ultimo punto di una serie, se non piu' vecchio di max_age_days."""
    if not series:
        return None
    d, v = max(series)
    if max_age_days is not None and ((today or date.today()) - d).days > max_age_days:
        return None
    return d, v


def safe_div(num: Optional[float], den: Optional[float]) -> Optional[float]:
    """Divisione che ritorna None (invece di esplodere o dare inf) sui casi degeneri."""
    if num is None or den is None or den == 0:
        return None
    return num / den


def extract_financials(companyfacts: dict,
                       today: Optional[date] = None) -> dict:
    """
    Estrae da un companyfacts EDGAR il quadro economico-patrimoniale completo.

    Ritorna un dizionario di SERIE (non di singoli numeri), cosi' che il
    chiamante possa calcolare TTM, ultimo esercizio, CAGR e andamento a cinque
    anni senza rileggere il JSON:

      revenue_q/ttm/fy, net_income_q/ttm/fy, ebit_ttm/fy,
      ocf_q/ttm/fy, capex_q/ttm/fy, fcf_ttm/fy,
      assets, equity, long_term_debt  (serie di istante)

    Tutti i valori sono in dollari. Le serie possono essere vuote: nessun
    campo e' garantito, perche' nessun tag XBRL e' obbligatorio per tutti i
    filer (le banche, per esempio, non hanno un capex confrontabile).
    """
    rev_f = extract_flow_facts(companyfacts, REVENUE_TAG_CANDIDATES)
    ni_f = extract_flow_facts(companyfacts, NET_INCOME_TAG_CANDIDATES)
    ebit_f = extract_flow_facts(companyfacts, OPERATING_INCOME_TAG_CANDIDATES)
    ocf_f = extract_flow_facts(companyfacts, OCF_TAG_CANDIDATES)
    capex_f = extract_flow_facts(companyfacts, CAPEX_TAG_CANDIDATES)
    div_f = extract_flow_facts(companyfacts, DIVIDENDS_PAID_TAG_CANDIDATES)
    int_f = extract_flow_facts(companyfacts, INTEREST_EXPENSE_TAG_CANDIDATES)

    out: dict = {}
    for name, facts in (("revenue", rev_f), ("net_income", ni_f), ("ebit", ebit_f),
                        ("ocf", ocf_f), ("capex", capex_f), ("dividends_paid", div_f),
                        ("interest_expense", int_f)):
        q = build_quarterly_flow(facts)
        out[f"{name}_q"] = q
        out[f"{name}_ttm"] = rolling_ttm(q)
        out[f"{name}_fy"] = build_annual_flow(facts)

    # Free cash flow = flusso di cassa operativo - investimenti in immobilizzi.
    # Definizione volutamente semplice e verificabile a mano sul rendiconto.
    out["fcf_q"] = combine_series(out["ocf_q"], out["capex_q"], lambda a, b: a - b)
    out["fcf_ttm"] = rolling_ttm(out["fcf_q"])
    out["fcf_fy"] = combine_series(out["ocf_fy"], out["capex_fy"], lambda a, b: a - b)

    out["assets"] = extract_instant_series(companyfacts, ASSETS_TAG_CANDIDATES)
    out["equity"] = extract_instant_series(companyfacts, EQUITY_TAG_CANDIDATES)
    out["long_term_debt"] = extract_instant_series(
        companyfacts, LONG_TERM_DEBT_TAG_CANDIDATES)
    out["short_term_debt"] = extract_instant_series(
        companyfacts, SHORT_TERM_DEBT_TAG_CANDIDATES)
    out["cash"] = extract_instant_series(companyfacts, CASH_TAG_CANDIDATES)

    # Debito totale = lungo + breve, allineati per data. Quando manca la parte a
    # breve si usa il solo lungo: dichiararlo incompleto sarebbe piu' onesto di
    # sommarlo a zero, ma la maggioranza dei filer che non taggano il debito
    # corrente semplicemente non ne ha, e rifiutare il totale renderebbe vuoto
    # il grafico della struttura del capitale per meta' del listino.
    # IL TOTALE SI COSTRUISCE SULL'UNIONE DELLE DATE, non sull'intersezione.
    #
    # `combine_series` incrocia le due serie e tiene solo le date presenti in
    # entrambe: bastava che una societa' smettesse di taggare la parte corrente
    # — cosa che succede appena il debito a breve si azzera — perche' il totale
    # sparisse anche dalle date in cui il debito a lungo era regolarmente
    # depositato. Su Super Micro il totale finiva addirittura SOTTO il solo
    # lungo termine, che e' aritmeticamente impossibile e si vedeva.
    #
    # Dove esistono entrambe le parti si sommano; dove ne esiste una sola quella
    # E' il totale — dichiarare un totale parziale e' piu' utile che dichiarare
    # il nulla, e la parte mancante quasi sempre manca perche' vale zero.
    ltd = dict(out["long_term_debt"] or [])
    std = dict(out["short_term_debt"] or [])
    total = {d: ltd.get(d, 0.0) + std.get(d, 0.0) for d in set(ltd) | set(std)}

    # Solo per le date che nessuna delle due parti raggiunge si ricorre
    # all'importo complessivo gia' pronto. Non sostituisce mai un totale
    # costruito dalle parti: cambiare definizione a meta' serie produrrebbe nel
    # grafico del debito un gradino che nella realta' non c'e'.
    for d, v in extract_instant_series(companyfacts, TOTAL_DEBT_TAG_CANDIDATES):
        total.setdefault(d, v)
    out["total_debt"] = sorted(total.items())

    # Da QUALI tag XBRL sono venuti i numeri. Nessun tag e' obbligatorio e le
    # societa' cambiano il proprio nel tempo: senza questa mappa, "ricavi 44,1
    # B$" non e' verificabile, perche' non si sa quale voce del bilancio sia.
    # Con essa si apre il concetto sulle API della SEC e si confronta.
    out["concepts"] = {
        "revenue": concepts_used(rev_f),
        "net_income": concepts_used(ni_f),
        "ebit": concepts_used(ebit_f),
        "ocf": concepts_used(ocf_f),
        "capex": concepts_used(capex_f),
        "interest_expense": concepts_used(int_f),
        "assets": instant_concept_used(companyfacts, ASSETS_TAG_CANDIDATES),
        "equity": instant_concept_used(companyfacts, EQUITY_TAG_CANDIDATES),
        "long_term_debt": instant_concept_used(
            companyfacts, LONG_TERM_DEBT_TAG_CANDIDATES),
        "short_term_debt": instant_concept_used(
            companyfacts, SHORT_TERM_DEBT_TAG_CANDIDATES),
        "cash": instant_concept_used(companyfacts, CASH_TAG_CANDIDATES),
    }
    return out


def concepts_used(facts: list[dict], max_names: int = 3) -> str:
    """
    I concetti XBRL che hanno effettivamente contribuito, dal piu' recente.

    Piu' di uno e' la norma, non un'anomalia: ASC 606 nel 2018 ha spostato tutti
    i ricavi su un tag nuovo, e una serie di dieci anni attraversa quel confine.
    """
    if not facts:
        return ""
    by_recency: dict[str, date] = {}
    for f in facts:
        c = f.get("concept") or ""
        if c and (c not in by_recency or f["end"] > by_recency[c]):
            by_recency[c] = f["end"]
    names = sorted(by_recency, key=lambda c: by_recency[c], reverse=True)
    return "+".join(names[:max_names])


def instant_concept_used(companyfacts: dict, candidates: tuple[str, ...],
                         unit: str = "USD") -> str:
    """Il concetto di stato patrimoniale che fornisce il dato piu' recente."""
    gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    best: Optional[tuple[int, date, str]] = None
    for rank, name in enumerate(candidates):
        rows = (gaap.get(name) or {}).get("units", {}).get(unit)
        if not rows:
            continue
        ends = [r["end"] for r in rows if r.get("end") and not r.get("start")]
        if not ends:
            continue
        try:
            newest = _to_date(max(ends))
        except (ValueError, TypeError):
            continue
        cand = (rank, newest, name)
        if best is None or cand[0] < best[0]:
            best = cand
    return best[2] if best else ""


def compute_ratios(fin: dict, price: Optional[float] = None,
                   market_cap: Optional[float] = None,
                   shares: Optional[float] = None,
                   today: Optional[date] = None) -> dict:
    """
    Indicatori di qualita' e di prezzo, dalle serie prodotte da extract_financials.

    Ogni voce e' calcolata da grandezze depositate alla SEC, non da campi
    pre-confezionati di un provider: la formula e' quindi verificabile riga per
    riga, ed e' quella dichiarata nei tooltip dell'interfaccia.

      ROE            = utile netto TTM / patrimonio netto medio del periodo
      Equity ratio   = patrimonio netto / totale attivo
      Earning power  = EBIT TTM / totale attivo   (rendimento del capitale
                       investito prima di struttura finanziaria e fisco)
      P/S            = capitalizzazione / ricavi TTM
      FCF yield      = free cash flow TTM / capitalizzazione
      Net margin     = utile netto TTM / ricavi TTM
      Debt / equity  = debito a lungo termine / patrimonio netto

    Il patrimonio netto MEDIO (inizio + fine periodo) e' la definizione corretta
    per un rapporto fra una grandezza di flusso e una di stato: usare solo il
    dato finale gonfia il ROE delle societa' che hanno appena fatto buyback.
    """
    r: dict = {}

    def last(series, max_age=MAX_BALANCE_AGE_DAYS):
        got = latest_value(series, max_age, today)
        return got[1] if got else None

    def flow_now(name: str) -> Optional[float]:
        """
        Grandezza "ultimi dodici mesi", con ripiego sull'ultimo esercizio.

        Il TTM e' preferibile perche' include gli ultimi trimestri, ma non
        tutti lo consentono: i filer piu' piccoli depositano solo il bilancio
        annuale, e le societa' che hanno appena cambiato tag XBRL hanno una
        serie trimestrale interrotta. Senza questo ripiego l'intera scheda
        finanziaria (ricavi, margini, ROE, FCF) resterebbe vuota per loro,
        pur avendo un bilancio annuale perfettamente utilizzabile.
        """
        ttm = fin.get(f"{name}_ttm") or []
        got = latest_value(ttm, MAX_BALANCE_AGE_DAYS, today)
        if got:
            return got[1]
        got = latest_value(fin.get(f"{name}_fy") or [], 550, today)
        if got:
            r.setdefault("_annual_fallback", []).append(name)
            return got[1]
        return None

    ni_ttm = flow_now("net_income")
    rev_ttm = flow_now("revenue")
    ebit_ttm = flow_now("ebit")
    fcf_ttm = flow_now("fcf")
    ocf_ttm = flow_now("ocf")

    equity_now = last(fin.get("equity", []))
    assets_now = last(fin.get("assets", []))
    ltd_now = last(fin.get("long_term_debt", []))
    debt_now = last(fin.get("total_debt", []))
    cash_now = last(fin.get("cash", []))

    # patrimonio medio sull'anno che il TTM copre
    equity_avg = equity_now
    eq = sorted(fin.get("equity", []))
    if eq and len(eq) >= 2 and equity_now is not None:
        target = eq[-1][0].toordinal() - 365
        prior = [(abs(d.toordinal() - target), v) for d, v in eq[:-1]]
        if prior:
            gap, v_prior = min(prior, key=lambda t: t[0])
            if gap <= 75:
                equity_avg = (equity_now + v_prior) / 2

    r["revenue_ttm"] = rev_ttm
    r["net_income_ttm"] = ni_ttm
    r["ebit_ttm"] = ebit_ttm
    r["ocf_ttm"] = ocf_ttm
    r["fcf_ttm"] = fcf_ttm
    # Oneri finanziari degli ultimi dodici mesi. Non serve a un rapporto di
    # bilancio ma al costo del debito: quanto questa societa' paga davvero
    # sul proprio debito, invece di quanto si stima che lo paghi.
    r["interest_expense_ttm"] = flow_now("interest_expense")
    r["fcf_latest_fy"] = fin["fcf_fy"][-1][1] if fin.get("fcf_fy") else None
    r["revenue_latest_fy"] = fin["revenue_fy"][-1][1] if fin.get("revenue_fy") else None
    r["equity"] = equity_now
    r["assets"] = assets_now
    r["long_term_debt"] = ltd_now
    r["total_debt"] = debt_now
    r["cash"] = cash_now
    r["net_debt"] = (debt_now - cash_now) if (debt_now is not None
                                              and cash_now is not None) else None
    # Enterprise value = capitalizzazione + debito - cassa. E' il prezzo
    # dell'AZIENDA, non delle sole azioni: due societa' con la stessa
    # capitalizzazione ma una carica di debito e l'altra piena di cassa non
    # costano la stessa cifra a chi le comprasse intere.
    r["enterprise_value"] = ((market_cap + (debt_now or 0) - (cash_now or 0))
                             if market_cap and market_cap > 0 else None)
    r["ev_to_ebit"] = (safe_div(r["enterprise_value"], ebit_ttm)
                       if (r["enterprise_value"] and ebit_ttm and ebit_ttm > 0)
                       else None)
    r["net_debt_to_ebit"] = (safe_div(r["net_debt"], ebit_ttm)
                             if (r["net_debt"] is not None and ebit_ttm
                                 and ebit_ttm > 0) else None)

    r["roe_pct"] = (safe_div(ni_ttm, equity_avg) or 0) * 100 if (
        ni_ttm is not None and equity_avg and equity_avg > 0) else None
    r["equity_ratio_pct"] = (safe_div(equity_now, assets_now) or 0) * 100 if (
        equity_now is not None and assets_now and assets_now > 0) else None
    r["earning_power_pct"] = (safe_div(ebit_ttm, assets_now) or 0) * 100 if (
        ebit_ttm is not None and assets_now and assets_now > 0) else None
    r["net_margin_pct"] = (safe_div(ni_ttm, rev_ttm) or 0) * 100 if (
        ni_ttm is not None and rev_ttm and rev_ttm > 0) else None
    r["operating_margin_pct"] = (safe_div(ebit_ttm, rev_ttm) or 0) * 100 if (
        ebit_ttm is not None and rev_ttm and rev_ttm > 0) else None
    r["debt_to_equity"] = safe_div(ltd_now, equity_now) if (
        ltd_now is not None and equity_now and equity_now > 0) else None

    if market_cap and market_cap > 0:
        r["ps_ratio"] = safe_div(market_cap, rev_ttm) if (rev_ttm and rev_ttm > 0) else None
        r["fcf_yield_pct"] = ((fcf_ttm / market_cap) * 100) if fcf_ttm is not None else None
        r["pfcf_ratio"] = safe_div(market_cap, fcf_ttm) if (fcf_ttm and fcf_ttm > 0) else None
    else:
        r["ps_ratio"] = r["fcf_yield_pct"] = r["pfcf_ratio"] = None

    if shares and shares > 0:
        r["revenue_per_share"] = safe_div(rev_ttm, shares)
        r["fcf_per_share"] = safe_div(fcf_ttm, shares)
        r["book_value_per_share"] = safe_div(equity_now, shares)
    else:
        r["revenue_per_share"] = r["fcf_per_share"] = r["book_value_per_share"] = None

    # crescita: FY su FY (il confronto che fa il mercato quando esce il bilancio),
    # con fallback al TTM se manca l'esercizio precedente
    r["fcf_growth_yoy_pct"] = (yoy_change(fin.get("fcf_fy", []))
                               if len(fin.get("fcf_fy", [])) >= 2
                               else yoy_change(fin.get("fcf_ttm", [])))
    r["revenue_growth_yoy_pct"] = (yoy_change(fin.get("revenue_ttm", []))
                                   or yoy_change(fin.get("revenue_fy", [])))
    # Dichiara quali grandezze non sono su base TTM ma sull'ultimo esercizio:
    # e' una differenza che il lettore ha diritto di conoscere prima di
    # confrontare questa societa' con un'altra.
    fallback = r.pop("_annual_fallback", None)
    r["financials_basis"] = ("annual:" + ",".join(sorted(set(fallback))
                             ) if fallback else "ttm")
    return r


def cagr_sources(fin: dict, eps_series: list[tuple[date, float]]) -> dict:
    """
    Quale serie alimenta ciascun CAGR, e con che passo.

    Ritorna {nome: (serie, base)} dove base e' 'ttm' o 'annual'. Il chiamante ha
    bisogno di sapere entrambe le cose: un tasso calcolato su una serie annuale
    non e' confrontabile con uno calcolato su un TTM, e finora la differenza non
    era visibile da nessuna parte.
    """
    out: dict[str, tuple[list, str]] = {"eps": (eps_series, "ttm")}
    for name in ("revenue", "fcf", "net_income", "ocf"):
        ttm = fin.get(f"{name}_ttm") or []
        if ttm:
            out[name] = (ttm, "ttm")
        else:
            out[name] = (fin.get(f"{name}_fy") or [], "annual")
    return out


def compute_cagr_details(fin: dict, eps_series: list[tuple[date, float]],
                         horizons: tuple[int, ...] = (3, 5, 10)) -> list[dict]:
    """
    Righe di verifica per ogni CAGR: metrica, orizzonte, i due estremi, lo span
    effettivo e il tasso. Una riga per combinazione calcolabile.

    E' il materiale del "sanity check": con questa tabella si rifa' il conto a
    mano, ((fine/inizio)^(1/anni) - 1), e si vede subito se un tasso spettacolare
    dipende da una base di partenza depressa.
    """
    rows: list[dict] = []
    for name, (series, basis) in cagr_sources(fin, eps_series).items():
        for years in horizons:
            got = series_cagr_detail(series, years)
            if got is None:
                continue
            rows.append({"metric": name, "horizon_years": years,
                         "series_basis": basis, "n_points": len(series), **got})
    return rows


def compute_cagrs(fin: dict, eps_series: list[tuple[date, float]],
                  horizons: tuple[int, ...] = (3, 5)) -> dict:
    """
    CAGR di utile per azione, ricavi, free cash flow e utile netto sugli
    orizzonti richiesti.

    Il CAGR e' calcolato sulle serie TTM (o annuali se il TTM non e'
    ricostruibile), quindi su dodici mesi pieni: confrontare un trimestre con
    un altro trimestre darebbe un numero dominato dalla stagionalita'.
    Resta None quando il valore di partenza e' <= 0, perche' un tasso composto
    a partire da una perdita non ha significato.
    """
    out: dict = {}
    sources = {
        "eps": eps_series,
        "revenue": fin.get("revenue_ttm") or fin.get("revenue_fy") or [],
        "fcf": fin.get("fcf_ttm") or fin.get("fcf_fy") or [],
        "net_income": fin.get("net_income_ttm") or fin.get("net_income_fy") or [],
        "ocf": fin.get("ocf_ttm") or fin.get("ocf_fy") or [],
    }
    for name, series in sources.items():
        for y in horizons:
            out[f"cagr_{name}_{y}y"] = series_cagr(series, y)
    return out


def annual_financial_table(fin: dict, eps_series: list[tuple[date, float]],
                           n_years: int = 5) -> list[dict]:
    """
    Ultimi N esercizi in forma tabellare, per la sezione "andamento a 5 anni".

    L'ancora sono gli esercizi dei RICAVI (o, se mancano, dell'utile netto): le
    altre grandezze vengono agganciate alla stessa data di chiusura, con una
    tolleranza di pochi giorni per gli esercizi da 52/53 settimane, che non
    cadono mai due volte nello stesso giorno.
    """
    anchor = fin.get("revenue_fy") or fin.get("net_income_fy") or []
    if not anchor:
        return []
    rows: list[dict] = []
    for fy_end, revenue in sorted(anchor)[-n_years:]:
        row = {"fy_end": fy_end, "revenue": revenue}
        for key, series in (("net_income", fin.get("net_income_fy")),
                            ("ebit", fin.get("ebit_fy")),
                            ("ocf", fin.get("ocf_fy")),
                            ("capex", fin.get("capex_fy")),
                            ("fcf", fin.get("fcf_fy")),
                            ("assets", fin.get("assets")),
                            ("equity", fin.get("equity")),
                            ("total_debt", fin.get("total_debt")),
                            ("cash", fin.get("cash")),
                            ("dividends_paid", fin.get("dividends_paid_fy")),
                            ("eps", eps_series)):
            row[key] = _value_near(series or [], fy_end, tol_days=10)
        rows.append(row)
    return rows


def _value_near(series: list[tuple[date, float]], when: date,
                tol_days: int = 10) -> Optional[float]:
    """Valore della serie alla data piu' vicina a 'when', entro tol_days."""
    best, best_gap = None, None
    for d, v in series:
        gap = abs((d - when).days)
        if best_gap is None or gap < best_gap:
            best, best_gap = v, gap
    return best if (best_gap is not None and best_gap <= tol_days) else None
