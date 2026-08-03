#!/usr/bin/env python3
"""
segments.py — Da dove arrivano davvero i ricavi: per attivita' e per area.

DOVE STA IL DATO, E PERCHE' NON L'AVEVAMO. Il resto dell'applicazione legge i
bilanci dalle API `companyfacts` della SEC, che restituiscono i soli valori
CONSOLIDATI: un ricavo totale, senza sapere da quale segmento o da quale paese
arrivi. La ripartizione esiste, ma vive un livello piu' sotto — nelle
DIMENSIONI del documento XBRL allegato a ogni singolo deposito, che va
scaricato e interpretato deposito per deposito. E' quello che fa questo file.

Su Alphabet produce esattamente le cifre che pubblicano i terminali di mercato:
Google Services 85,1%, Cloud 14,6%, Other Bets 0,4%; Stati Uniti 48,2%, EMEA
29,1%, APAC 16,8%.

TRE INSIDIE, TUTTE GESTITE QUI DENTRO.

1. I MEMBRI CHE SONO SOTTOTOTALI. Nell'asse dei prodotti di Google la voce
   "Advertising" CONTIENE Search, YouTube e Network: sommare tutti i membri
   conterebbe quei ricavi due volte, e la torta risulterebbe del 150%. Si
   smaschera confrontando la somma con il ricavo consolidato dello stesso
   periodo — che sta nello stesso documento — e togliendo la voce che fa
   quadrare il conto.
2. GLI ASSI NON DICONO SEMPRE LA STESSA COSA. I "segmenti di business" di Apple
   SONO aree geografiche (Americas, Europe, Greater China, Japan). Per questo
   il nome mostrato viene dall'ETICHETTA depositata dalla societa', non dal
   nome tecnico dell'asse.
3. LE ETICHETTE TECNICHE NON SI LEGGONO. `goog:GoogleServicesMember` diventa
   "Google Services" passando dal label linkbase, il file di traduzione che la
   societa' deposita insieme ai numeri.

QUANDO IL CONTO NON TORNA NON SI DISEGNA NIENTE. Una torta che non somma al
totale depositato e' peggio di nessuna torta: sembra un'informazione e invece
e' un errore di lettura. In quel caso il modulo dichiara di non essere
affidabile e la scheda rimanda alla nota sui segmenti del 10-K.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from collections import defaultdict

import streamlit as st

import data_sources as ds

# Concetti che rappresentano "ricavo". L'ordine non conta: si tiene il piu'
# ricco per ogni contesto, perche' una societa' puo' taggarne piu' d'uno.
REVENUE_TAGS = {
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
}

# Gli assi che interessano, nell'ordine in cui si leggono.
# (nome tecnico, titolo, spiegazione, parola da usare nel commento)
AXES: tuple[tuple[str, str, str, str], ...] = (
    ("StatementBusinessSegmentsAxis", "By segment",
     "The operating segments the company reports to its own board — the "
     "divisions it is actually run as.", "segment"),
    ("StatementGeographicalAxis", "By region",
     "Where the customers are, not where the company is registered.", "region"),
    ("ProductOrServiceAxis", "By product or service",
     "The revenue lines within the business: what the company actually sells.",
     "product line"),
)

# ASSI DI QUALIFICAZIONE, non di ripartizione.
#
# Apple tagga i ricavi di segmento con DUE assi: quello dei segmenti e
# `ConsolidationItemsAxis = OperatingSegmentsMember`, che non e' una seconda
# suddivisione — dice soltanto "questa e' la riga del segmento, non un
# elisione fra segmenti". Scartare ogni fatto con piu' di una dimensione,
# come faceva la prima versione, faceva sparire l'intera ripartizione di
# Apple. Si accettano quindi le dimensioni di questa lista, e SOLO con questi
# membri: le righe di elisione fra segmenti hanno segno opposto e sommate al
# resto falserebbero la torta.
QUALIFIER_AXES = {"ConsolidationItemsAxis", "StatementConsolidationItemsAxis"}
QUALIFIER_MEMBERS = {"OperatingSegmentsMember", "ReportableSegmentsMember",
                     "ReportableSubsegmentsMember"}

# GLI SCAMBI FRA SEGMENTI, che sono la ragione per cui la somma non torna.
#
# Un gruppo con piu' divisioni si vende servizi al proprio interno: le
# discariche di Waste Management fatturano alle sue societa' di raccolta. I
# ricavi di ciascun segmento sono quindi al LORDO di quelle partite, e il
# totale consolidato le elimina — 30,89 miliardi di ricavi di segmento contro
# 25,20 consolidati, cinque miliardi e mezzo di differenza. Sommando i segmenti
# lordi nessuna verifica di quadratura poteva riuscire, e l'intera ripartizione
# veniva scartata su ogni gruppo che dichiara le eliminazioni — cioe' su quasi
# tutti gli industriali con piu' divisioni.
#
# L'eliminazione e' depositata segmento per segmento, sullo stesso asse ma con
# questo qualificatore: si sottrae a ciascuno e si ottiene il ricavo NETTO, che
# e' anche la grandezza giusta da mostrare — quella che arriva da clienti veri.
ELIMINATION_MEMBERS = {"IntersegmentEliminationMember",
                       "IntersegmentEliminationsMember",
                       "EliminationsMember", "ConsolidationEliminationsMember"}

# Membri standard del vocabolario comune, che la societa' non ridefinisce e che
# quindi nel suo label linkbase non ci sono.
STANDARD_LABELS = {
    "country:US": "United States", "country:CN": "China", "country:JP": "Japan",
    "country:GB": "United Kingdom", "country:DE": "Germany",
    "country:CA": "Canada", "country:FR": "France", "country:IN": "India",
    "us-gaap:EMEAMember": "EMEA", "us-gaap:NonUsMember": "Outside the US",
    "srt:AsiaPacificMember": "Asia Pacific",
    "srt:EuropeMember": "Europe", "srt:AmericasMember": "Americas",
    "srt:NorthAmericaMember": "North America",
    "srt:LatinAmericaMember": "Latin America",
    "us-gaap:AllOtherSegmentsMember": "Other segments",
    "us-gaap:CorporateAndOtherMember": "Corporate and other",
    "us-gaap:MaterialReconcilingItemsMember": "Reconciling items",
}

# Scarto tollerato fra la somma dei membri e il totale consolidato.
TOLERANCE = 0.02

XBRLI = "{http://www.xbrl.org/2003/instance}"
XBRLDI = "{http://xbrl.org/2006/xbrldi}"
LINK = "{http://www.xbrl.org/2003/linkbase}"
XLINK = "{http://www.w3.org/1999/xlink}"


def _tidy(text: str) -> str:
    """
    Ripulisce un'etichetta depositata.

    Toglie il suffisso tecnico e ricompone le sigle: la SEC genera etichette
    automatiche in cui ogni lettera di un acronimo diventa una parola, e nel
    deposito di Nike il marchio si chiama davvero «N I K E Brand». Sono
    lettere singole separate da spazi, quindi si riconoscono e si riuniscono.
    """
    # Le etichette arrivano da XML e portano le entita' HTML come sono state
    # depositate: Caterpillar chiama un segmento "Power &amp; Energy".
    text = html.unescape(text or "")
    text = re.sub(r"\s*\[(Member|Domain|Axis)\]\s*$", "", text).strip()
    text = re.sub(r"\b(?:[A-Z] ){1,}[A-Z]\b",
                  lambda m: m.group(0).replace(" ", ""), text)
    return text.strip()


def _pretty(tag: str, labels: dict) -> str:
    """Il nome leggibile di un membro: etichetta depositata, vocabolario
    standard, oppure il nome tecnico spezzato sulle maiuscole."""
    key = tag.replace(":", "_")
    lab = labels.get(key)
    if lab:
        cleaned = _tidy(lab)
        if cleaned:
            return cleaned
    if tag in STANDARD_LABELS:
        return STANDARD_LABELS[tag]
    raw = tag.split(":")[-1]
    raw = re.sub(r"(Member|Domain)$", "", raw)
    # Si spezza fra minuscola e maiuscola e prima dell'ultima maiuscola di una
    # sequenza ("EMEARegion" -> "EMEA Region"), non fra maiuscole consecutive:
    # altrimenti ogni acronimo esce sillabato.
    raw = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", raw)
    return raw.strip() or tag


def _full_year(start: str, end: str) -> bool:
    """Solo periodi di dodici mesi: un trimestre accanto a un esercizio nella
    stessa torta sommerebbe grandezze non confrontabili."""
    if not start or not end:
        return False
    try:
        months = (int(end[:4]) - int(start[:4])) * 12 + int(end[5:7]) - int(start[5:7])
    except (ValueError, IndexError):
        return False
    return months >= 11


def _drop_internal_subtotals(members: dict[str, float]) -> dict[str, float]:
    """
    Toglie le voci che sono somme di altre voci della STESSA nota, senza
    guardare il totale consolidato.

    SERVE DOVE LA RIPARTIZIONE E' PARZIALE. Alphabet elenca per prodotto
    "Google Search & other", "YouTube ads", "Google Network", il loro subtotale
    "Google advertising", e "Subscriptions, platforms and devices". Quelle voci
    coprono Google Services e basta: Cloud e Other Bets non sono ripartiti per
    prodotto, quindi la somma non tornera' MAI al ricavo consolidato e il
    confronto con quello non puo' individuare il subtotale.

    Qui il subtotale si riconosce per quello che e': una voce il cui valore e'
    la somma di altre due o piu' voci dell'elenco. Se non lo si togliesse,
    l'advertising verrebbe contato due volte e la torta direbbe che la
    pubblicita' vale il doppio di quanto vale.
    """
    from itertools import combinations

    keys = sorted(members, key=lambda k: -members[k])
    drop: set[str] = set()
    for k in keys:
        others = [x for x in keys if x != k and x not in drop]
        target = members[k]
        # Da due voci in su: una voce uguale a un'altra sola non e' un
        # subtotale, e' un duplicato — e non e' questo il caso da risolvere.
        for size in range(2, min(len(others), 6) + 1):
            if any(abs(sum(members[x] for x in combo) / target - 1) <= TOLERANCE
                   for combo in combinations(others, size) if target):
                drop.add(k)
                break
    return {k: v for k, v in members.items() if k not in drop}


def _drop_subtotals(members: dict[str, float], total: float
                    ) -> tuple[dict[str, float], bool]:
    """
    Toglie le voci che sono somme di altre voci, confrontando con il totale
    consolidato dello stesso periodo.

    Restituisce (membri, affidabile). Si prova prima cosi' com'e', poi togliendo
    una voce, poi due: oltre non si insiste, perche' a quel punto la struttura
    della nota non e' una semplice ripartizione e indovinarla sarebbe peggio
    che dichiarare di non saperla leggere.
    """
    if not members or not total:
        return members, False
    if abs(sum(members.values()) / total - 1) <= TOLERANCE:
        return members, True

    keys = sorted(members, key=lambda k: -members[k])
    for k in keys:
        rest = {x: v for x, v in members.items() if x != k}
        if rest and abs(sum(rest.values()) / total - 1) <= TOLERANCE:
            return rest, True
    for i, k1 in enumerate(keys):
        for k2 in keys[i + 1:]:
            rest = {x: v for x, v in members.items() if x not in (k1, k2)}
            if rest and abs(sum(rest.values()) / total - 1) <= TOLERANCE:
                return rest, True
    return members, False


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_segments(ticker: str, cik: str) -> dict | None:
    """
    Ripartizione dei ricavi dall'ultimo 10-K depositato.

    Cache di 24 ore: e' un dato che cambia una volta l'anno, quando esce il
    bilancio. Una sola richiesta pesante (il documento XBRL, fra 1 e 15 MB) piu'
    due leggere, tutte attraverso il limitatore di frequenza gia' usato dal
    resto dell'applicazione — le API della SEC sono gratuite e chiedono solo di
    non superare le dieci richieste al secondo.
    """
    if not cik:
        return None
    try:
        sub = ds._sec_get(f"https://data.sec.gov/submissions/CIK{cik}.json")
        if sub is None or sub.status_code != 200:
            return None
        rec = sub.json().get("filings", {}).get("recent", {})
        accession = filed = None
        for form, acc, date in zip(rec.get("form", []),
                                   rec.get("accessionNumber", []),
                                   rec.get("filingDate", [])):
            if form in ("10-K", "20-F", "40-F"):
                accession, filed = acc, date
                break
        if not accession:
            return None

        base = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{accession.replace('-', '')}")
        idx = ds._sec_get(f"{base}/index.json")
        if idx is None or idx.status_code != 200:
            return None
        names = [i["name"] for i in idx.json()["directory"]["item"]]
        instance = next((n for n in names if n.endswith("_htm.xml")), None)
        label_file = next((n for n in names if n.endswith("_lab.xml")), None)
        if not instance:
            return None

        resp = ds._sec_get(f"{base}/{instance}", timeout=90)
        if resp is None or resp.status_code != 200:
            return None
        root = ET.fromstring(resp.content)
    except Exception:                                        # noqa: BLE001
        return None

    # LE ETICHETTE, DA DUE FONTI IN ORDINE DI QUALITA'.
    #
    # `MetaLinks.json` e' il file che la SEC genera per ogni deposito moderno e
    # porta anche l'etichetta BREVE, che e' quella scritta per essere letta:
    # dove il linkbase dice "Microsoft Three Six Five Commercial Products And
    # Cloud Services [Member]" — generata meccanicamente dal nome tecnico —
    # MetaLinks dice "Microsoft 365 Commercial Products and Cloud Services".
    # Il linkbase resta come ripiego: in alcuni depositi MetaLinks non c'e', in
    # altri (Microsoft, appunto) manca invece il linkbase.
    labels = _fetch_meta_labels(base, instance)
    if label_file:
        try:
            lr = ds._sec_get(f"{base}/{label_file}", timeout=60)
            if lr is not None and lr.status_code == 200:
                for k, v in _parse_labels(ET.fromstring(lr.content)).items():
                    labels.setdefault(k, v)
        except Exception:                                    # noqa: BLE001
            pass

    # --- contesti: periodo e dimensioni ---
    contexts = {}
    for ctx in root.findall(f"{XBRLI}context"):
        period = ctx.find(f"{XBRLI}period")
        if period is None:
            continue
        start = period.findtext(f"{XBRLI}startDate", default="")
        end = period.findtext(f"{XBRLI}endDate", default="")
        dims = {m.get("dimension"): (m.text or "").strip()
                for m in ctx.iter(f"{XBRLDI}explicitMember")}
        contexts[ctx.get("id")] = (start, end, dims)

    # --- fatti di ricavo, con e senza dimensioni ---
    by_axis: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    eliminations: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    # I MARGINI DELLE MATRICI, tenuti a parte. Vedi la nota sotto.
    cross: dict[tuple[str, str], dict[str, float]] = defaultdict(
        lambda: defaultdict(float))
    totals: dict[str, float] = {}
    for el in root.iter():
        if el.tag.split("}")[-1] not in REVENUE_TAGS:
            continue
        ref = el.get("contextRef")
        if ref not in contexts:
            continue
        start, end, dims = contexts[ref]
        if not _full_year(start, end):
            continue
        try:
            value = float(el.text)
        except (TypeError, ValueError):
            continue
        if not dims:
            # Il totale consolidato: si tiene il PIU' GRANDE, perche' una
            # societa' che tagga sia "ricavi netti" sia una loro componente
            # senza dimensioni darebbe altrimenti un totale troppo basso, e la
            # verifica scarterebbe una ripartizione corretta.
            totals[end] = max(totals.get(end, 0.0), value)
            continue

        breakdown = [(ax, m) for ax, m in dims.items()
                     if ax.split(":")[-1] in {a for a, _, _, _ in AXES}]
        others = [(ax, m) for ax, m in dims.items() if (ax, m) not in breakdown]
        # DUE ASSI INSIEME SONO UNA MATRICE (prodotto × segmento, segmento ×
        # area). Sommarne le celle dentro un asse solo conterebbe ogni ricavo
        # due volte, ed e' per questo che la prima versione le scartava. Ma il
        # MARGINE di una matrice — la somma lungo l'altro asse — e' un dato
        # legittimo, ed e' l'unico che alcune societa' pubblicano.
        #
        # Alphabet tagga TUTTI i suoi ricavi per prodotto incrociati con il
        # segmento: "Google Services × Google Search & other" e cosi' via.
        # Scartandoli spariva l'intera ripartizione per prodotto — la piu'
        # interessante che quella societa' pubblichi — e la scheda dichiarava
        # che non esisteva.
        #
        # Si tengono quindi da parte, sommati per membro di ciascun asse, e si
        # usano SOLO dove non ci sono fatti a un asse solo: quelli, quando ci
        # sono, sono la ripartizione che la societa' ha voluto dichiarare.
        if len(breakdown) > 1:
            if not any(m.split(":")[-1] in ELIMINATION_MEMBERS
                       for _, m in dims.items()):
                for ax, member in breakdown:
                    cross[(ax.split(":")[-1], end)][member] += value
            continue
        if len(breakdown) != 1:
            continue
        if any(ax.split(":")[-1] not in QUALIFIER_AXES for ax, _ in others):
            continue
        quals = {m.split(":")[-1] for _, m in others}
        is_elimination = bool(quals & ELIMINATION_MEMBERS)
        if not is_elimination and not quals <= QUALIFIER_MEMBERS:
            continue
        axis, member = breakdown[0]
        target = eliminations if is_elimination else by_axis
        target[(axis.split(":")[-1], end)][member] = value

    out: dict[str, dict] = {}
    for axis_key, title, note, word in AXES:
        periods = {end: members for (ax, end), members in by_axis.items()
                   if ax == axis_key and len(members) >= 2}
        if not periods:
            periods = {end: dict(members) for (ax, end), members in cross.items()
                       if ax == axis_key and len(members) >= 2}
        if not periods:
            continue
        cleaned, reliable_any, coverage = {}, False, {}
        for end in sorted(periods, reverse=True):
            # Dal lordo al netto: si toglie a ogni segmento cio' che ha
            # fatturato agli altri segmenti dello stesso gruppo. E' il passaggio
            # che rende confrontabile la somma con il totale consolidato, ed e'
            # anche la grandezza giusta da mostrare — i ricavi da clienti veri.
            elim = eliminations.get((axis_key, end), {})
            gross = dict(periods[end])
            members = {m: v - elim.get(m, 0.0) for m, v in gross.items()}
            members = {m: v for m, v in members.items() if v > 0}
            members, ok = _drop_subtotals(members, totals.get(end))
            if ok:
                reliable_any = True
                cleaned[end] = {_pretty(m, labels): v for m, v in members.items()}
                coverage[end] = 1.0
                continue

            # NON TORNA AL CONSOLIDATO: due casi molto diversi.
            #
            # Se la somma SUPERA il totale c'e' un doppio conteggio che non si
            # e' riusciti a togliere, e mostrare quella torta sarebbe peggio che
            # non mostrarne nessuna. Se invece resta SOTTO, la nota copre solo
            # una parte dell'attivita' — ed e' un'informazione vera, che va
            # mostrata dicendo quanta parte copre.
            total = totals.get(end)
            partial = _drop_internal_subtotals(members)
            if not total or not partial:
                continue
            share = sum(partial.values()) / total
            if share <= 1 + TOLERANCE:
                cleaned[end] = {_pretty(m, labels): v for m, v in partial.items()}
                coverage[end] = share
        if not cleaned:
            continue
        out[axis_key] = {
            "title": title, "note": note, "word": word, "periods": cleaned,
            "reliable": reliable_any,
            # Quanta parte del ricavo consolidato copre la ripartizione. 1.0
            # significa che le parti fanno il tutto; meno significa che la nota
            # riguarda solo un pezzo dell'attivita', e la scheda lo dichiara.
            "coverage": coverage,
            "total": {e: totals.get(e) for e in cleaned},
        }

    if not out:
        return None
    out["_meta"] = {"accession": accession, "filed": filed,
                    "url": f"{base}/{instance}",
                    "filing_index": f"{base}/"}
    return out


def _fetch_meta_labels(base: str, instance: str) -> dict[str, str]:
    """Etichette da `MetaLinks.json`, preferendo quella breve."""
    try:
        resp = ds._sec_get(f"{base}/MetaLinks.json", timeout=60)
        if resp is None or resp.status_code != 200:
            return {}
        data = resp.json().get("instance", {})
        block = data.get(instance) or (next(iter(data.values()), {}))
        out = {}
        for tag, spec in (block.get("tag") or {}).items():
            roles = (spec.get("lang", {}).get("en-us", {}).get("role", {}))
            text = roles.get("terseLabel") or roles.get("label")
            if text:
                out[tag] = text
        return out
    except Exception:                                        # noqa: BLE001
        return {}


def _parse_labels(root: ET.Element) -> dict[str, str]:
    """Il file di traduzione: da `goog_GoogleServicesMember` a
    "Google Services". Si tiene solo l'etichetta standard, non quelle
    alternative (terse, negated, di periodo)."""
    labels: dict[str, str] = {}
    for link in root.iter(f"{LINK}labelLink"):
        loc = {l.get(f"{XLINK}label"): l.get(f"{XLINK}href", "").split("#")[-1]
               for l in link.findall(f"{LINK}loc")}
        txt = {l.get(f"{XLINK}label"): (l.text or "")
               for l in link.findall(f"{LINK}label")
               if l.get(f"{XLINK}role", "").endswith("/label")}
        for arc in link.findall(f"{LINK}labelArc"):
            a, b = arc.get(f"{XLINK}from"), arc.get(f"{XLINK}to")
            if a in loc and b in txt and txt[b]:
                labels.setdefault(loc[a], txt[b])
    return labels


def concentration_note(shares: dict[str, float], what: str) -> tuple[str, str]:
    """
    Il commento sulla ripartizione: (tono, testo).

    La torta da sola dice come sono divisi i ricavi; la domanda utile e' quanto
    l'azienda dipenda da UNA voce. Stesse regole del resto dei commenti:
    aritmetica sui numeri che si vedono, nessun giudizio inventato.

    `what` arriva dall'asse — "segment", "region", "product line" — e NON si
    indovina qui: scriverlo a mano voleva dire chiamare "region" la
    ripartizione per prodotto di Apple, che e' esattamente il genere di
    sciatteria che rende inattendibile tutto il resto della frase.
    """
    if not shares:
        return "neutral", ""
    total = sum(shares.values())
    if total <= 0:
        return "neutral", ""
    ranked = sorted(shares.items(), key=lambda x: -x[1])
    top_name, top_val = ranked[0]
    top = top_val / total * 100
    others = len(ranked) - 1

    if top >= 75:
        rest = (f"The other {others} contribute the rest between them"
                if others > 1 else "Everything else is the remainder")
        return "bear", (
            f"**{top:.0f}% of revenue comes from one {what}** — {top_name}. "
            f"{rest}: this is effectively a single-{what} company, and "
            f"whatever happens to that {what} happens to the whole business.")
    if top >= 50:
        second = ranked[1]
        return "flag", (
            f"**{top_name} alone is {top:.0f}% of revenue**, with "
            f"{second[0]} second at {second[1] / total * 100:.0f}%. The "
            f"concentration is high but not absolute — the second {what} is "
            f"large enough to matter.")
    return "bull", (
        f"No single {what} dominates: the largest, {top_name}, is "
        f"**{top:.0f}%** of revenue across {len(ranked)} of them. Spread like "
        f"this, a problem in one {what} does not decide the year.")


def growth_note(periods: dict[str, dict[str, float]]) -> str:
    """Chi e' cresciuto e chi si e' ristretto fra i due esercizi piu' recenti."""
    ends = sorted(periods, reverse=True)
    if len(ends) < 2:
        return ""
    now, before = periods[ends[0]], periods[ends[1]]
    moves = []
    for name, value in sorted(now.items(), key=lambda x: -x[1]):
        prev = before.get(name)
        if not prev or prev <= 0:
            continue
        moves.append((name, (value / prev - 1) * 100))
    if len(moves) < 2:
        return ""
    fastest = max(moves, key=lambda x: x[1])
    slowest = min(moves, key=lambda x: x[1])
    if fastest[0] == slowest[0]:
        return ""
    txt = (f"Between {ends[1][:4]} and {ends[0][:4]} the fastest growing was "
           f"**{fastest[0]}** ({fastest[1]:+.0f}%)")
    if slowest[1] < 0:
        txt += f", while **{slowest[0]}** shrank ({slowest[1]:+.0f}%)."
    else:
        txt += f", the slowest **{slowest[0]}** ({slowest[1]:+.0f}%)."
    return txt
