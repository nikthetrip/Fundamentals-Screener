# Lynch Valuation Dashboard

Replica la **Peter Lynch earnings line** (tipo GuruFocus): prezzo reale vs
"prezzo a P/E 15/20/25" nel tempo, più uno screener che ordina i titoli per
sconto sul fair value — estendibile a **tutte le società quotate negli USA**.

**Fonti dati (tutte gratuite, senza API key a pagamento):**
- **SEC EDGAR** → utili storici (EPS diluito) dal ~2009, bilanci completi
  (ricavi, utile netto, cash flow, patrimonio), elenco ufficiale dei quotati,
  codice SIC per settore e industria, link ai 10-K/10-Q
- **yfinance / Stooq** → prezzi storici (fallback automatico), capitalizzazione,
  P/E prospettico, data della prossima trimestrale

Il fair value è calcolato **per azione**: `fair_value = EPS_TTM × P/E_target`.
L'EPS TTM è ricostruito dai trimestri EDGAR (con Q4 derivato da FY quando manca).

Oltre alla valutazione, ogni titolo ha una **scheda finanziaria** (ROE, equity
ratio, earning power, free cash flow, margini, CAGR a 3/5/10 anni) in cui ogni
voce è confrontata con la **mediana della sua industria**.

---

## I due modelli Lynch (attenzione: sono diversi)

Il tool calcola **due** fair value distinti, perché Lynch usava due strumenti diversi.

### 1. Lynch Chart — P/E 15 fisso
`fair value = EPS TTM × 15`. È la *earnings line* di *One Up on Wall Street*:
un filtro visivo rapido, uguale per tutti. Ottimo per scansionare 500 titoli.
Limite: penalizza le aziende a forte crescita e premia quelle lente.

### 2. Lynch Fair Value — multiplo per categoria (PEG)
`fair value = EPS × P/E equo della categoria`, dove il P/E equo dipende dal tipo
di azienda (principio PEG = 1: il P/E equo tende al tasso di crescita).

| Categoria | Criterio | P/E equo assegnato |
|---|---|---|
| 🚀 **Fast Grower** | crescita 5 anni > 20% | = crescita, **massimo 25** |
| 🏛️ **Stalwart** | crescita 10–20% | = crescita |
| 🐢 **Slow Grower** | crescita 0–10% | crescita + dividendo, **minimo 6, massimo 12** |
| 🔄 **Ciclica** | settore ciclico **e** volatilità utili > 40 | 12 su utili **normalizzati** |
| 🏗️ **Asset Play** | P/B < 1 | nessuno — il valore è negli asset |
| 🔧 **Turnaround** | perdite negli ultimi 5 anni | nessuno — utili non affidabili |

**Ordine di verifica:** Turnaround e Asset Play hanno priorità su tutto, poi le
Cicliche, poi le fasce di crescita. Una società in forte crescita ma con perdite
recenti è classificata Turnaround, non Fast Grower: per lei il P/E non è una base
di valutazione affidabile.

**Lynch ratio** = (crescita % + dividendo %) ÷ P/E effettivo.
Sopra 1,5 interessante · intorno a 1 equo · sotto 1 caro.

### Come leggerli insieme
Quando i due modelli **concordano**, il segnale è robusto. Quando **divergono
oltre il 35%** l'app te lo segnala: di solito significa che il titolo è in una
categoria dove il P/E 15 non ha senso (una fast grower al 30% o una slow grower
al 3%), oppure che gli utili contengono voci straordinarie.

---

## File

| File | Cosa fa |
|------|---------|
| `edgar_logic.py` | Logica pura: parsing EDGAR, TTM EPS, bilanci, indicatori, fair value |
| `data_sources.py` | Rete: universo SEC, companyfacts, submissions, prezzi yfinance/Stooq |
| `sic_map.py` | Codice SIC della SEC → settore e industria (ripiego quando yfinance non li ha) |
| `build_dataset.py` | Orchestratore: genera tutti i CSV in `data/` |
| `app.py` | Dashboard Streamlit (Screener + Details) — interfaccia in inglese |
| `test_logic.py` | Test offline della matematica di TTM/fair value |
| `test_financials.py` | Test offline di bilanci, FCF, CAGR, volatilita' |
| `test_categories.py` · `test_guardrails.py` · `test_filings.py` | Classificazione Lynch, arbitraggio fonti, filing |
| `test_ttm_regression.py` | Regressione su companyfacts SEC reali congelati |
| `requirements.txt` | Dipendenze |

### File generati in `data/`

| File | Contenuto |
|------|-----------|
| `history.csv.gz` | serie storica prezzo + EPS per ticker |
| `fundamentals.csv` | una riga per ticker: valutazione, bilancio, tutti i CAGR |
| `financials_annual.csv` | ultimi esercizi per ticker: ricavi, utili, cash flow, patrimonio |
| `cagr_detail.csv` | ogni CAGR con i **due estremi** da cui è ricavato, per rifare il conto |
| `events.csv` | split, diluizioni, buyback |
| `filings.csv` | link diretti agli ultimi 10-K/10-Q |
| `skipped.csv` | ticker esclusi, **con il motivo** |

---

## Passo 1 — Setup locale

```bash
# nella cartella del progetto
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### ⚠️ Obbligatorio: imposta il tuo contatto per SEC EDGAR
La SEC blocca (403) le richieste senza un User-Agent con un contatto reale. È una
loro regola. Va messa in una variabile d'ambiente, non nel codice:

```bash
export SEC_USER_AGENT="Lynch Research tua@email.com"
```

Su Windows (cmd): `set SEC_USER_AGENT=Lynch Research tua@email.com`

Su GitHub Actions è il secret `SEC_USER_AGENT`.

---

## Passo 2 — Verifica la logica (facoltativo, 5 secondi)

```bash
python test_logic.py && python test_financials.py && python test_categories.py
python test_guardrails.py && python test_filings.py && python test_ttm_regression.py
```
Devono stampare tutti `✅ TUTTI I TEST PASSATI`, senza toccare la rete. Coprono
TTM, ricostruzione Q4, differenziazione dei flussi di cassa cumulati, free cash
flow, CAGR, classificazione Lynch e arbitraggio fra fonti EPS discordi.

---

## Passo 3 — Genera i dati

Dalla prova rapida all'intero mercato USA. Più universi si uniscono con `+`,
senza elaborare due volte le società in comune:

```bash
python build_dataset.py                                  # 10 ticker, ~1 min
python build_dataset.py --universe sp500                 # ~500 societa', ~6 min
python build_dataset.py --universe sp500+russell1000     # ~990 societa', ~12 min
python build_dataset.py --universe us-all                # ~6.600 societa', ~75 min
```

`sp500+russell1000` è il default della GitHub Action: copre large e mid cap
americane e porta la densità del confronto per industria a ~7,6 società per
industria (contro 4,5 del solo S&P 500), cioè molti meno confronti che devono
ripiegare sul settore.

`--full` resta valido come alias di `--universe sp500`.

### L'universo `us-all`

L'elenco viene da **`company_tickers_exchange.json` della SEC**: è la lista
ufficiale dei filer con la borsa di quotazione, l'unica gratuita, completa e già
allineata ai CIK che usiamo per i bilanci. Sono tenute NYSE, Nasdaq, NYSE
American e CBOE; l'OTC è escluso, perché lì prezzi e depositi sono troppo
irregolari per una valutazione.

Vengono escluse anche **privilegiate, warrant, diritti e unit**, che la SEC
elenca sotto lo stesso CIK dell'ordinaria. Il motivo è concreto: il loro
*prezzo* è quello del titolo derivato, ma gli *utili* che gli assoceremmo sono
quelli della società, e il rapporto fra i due non è un P/E. Arbor Realty
preferred serie E risultava a P/E 7,4 e sarebbe finita in cima allo screener
come occasione.

Opzioni utili su grandi universi:

| Opzione | A cosa serve |
|---|---|
| `--workers 6` | ticker elaborati in parallelo (il limitatore SEC resta rispettato) |
| `--freq M` | storico mensile: obbligatorio su `us-all`, altrimenti il file esplode |
| `--min-market-cap 5e7` | esclude le micro-cap prima ancora di scaricarne i bilanci |
| `--bulk-facts` | scarica **una volta** l'archivio companyfacts (~1,5 GB) invece di una richiesta per società |
| `--shard i --shards n` | divide la build fra più processi/job |
| `--limit 50` | prova la pipeline su pochi ticker |

Build divisa in quattro pezzi e poi unita:

```bash
for i in 0 1 2 3; do
  python build_dataset.py --universe us-all --freq M --workers 6 --shards 4 --shard $i &
done
wait
python build_dataset.py --merge
```

Ogni job scrive `data/<nome>.partN.csv`; `--merge` li concatena, toglie i
duplicati e produce i file definitivi. È idempotente.

### Titoli extra

Aggiungi il ticker a `extra_tickers.txt` (uno per riga): si somma all'universo
scelto, qualunque esso sia. Oppure al volo:

```bash
python build_dataset.py --universe sp500 --tickers RDDT,ARM
```

### Cosa aspettarsi nel log

```
AAPL: ✓ 4234 righe | EPS=8.26 (div=0.0%) | Consumer Electronics | OVER
```

Qualche ticker viene saltato: società senza EPS né utile netto in us-gaap
(spesso filer esteri o trust), ETF, prezzi irraggiungibili. Il motivo esatto per
ciascuno finisce in `data/skipped.csv` ed è visibile nello Screener in un
pannello dedicato — così un titolo mancante non sembra un problema dei filtri.

---

## Passo 4 — Lancia la dashboard

```bash
./run.sh
```

Si apre su `http://localhost:8501`. Lo script ricorda al posto tuo il percorso
del virtualenv e la variabile per la SEC.

| Comando | Cosa fa |
|---|---|
| `./run.sh` | apre la dashboard con i dati che ci sono |
| `./run.sh --update` | **prima** rigenera i dati, poi apre |
| `./run.sh --update-only` | rigenera e basta |
| `./run.sh --update --universe sp500` | aggiorna un universo diverso |
| `./run.sh --port 8600` | usa un'altra porta |

Per aggiornare i dati serve il contatto per la SEC. Scrivilo una volta in un
file locale (non versionato, vedi `.gitignore`):

```bash
echo "Lynch Research tua@email.com" > .sec_user_agent
```

### ⚠️ Aprire la dashboard NON aggiorna i dati

Sono due operazioni separate, di proposito:

- `build_dataset.py` scarica da SEC e yfinance e scrive i CSV in `data/`. Minuti.
- `app.py` legge solo quei CSV. Non tocca la rete. Secondi.

Tenerle separate significa che la dashboard si apre subito invece di far
aspettare un quarto d'ora. In alto a sinistra c'è sempre scritto `updated
<data e ora>`, così sai a quando risalgono i numeri che stai guardando. Per
aggiornarli: `./run.sh --update`, oppure il bottone della GitHub Action.

NB: l'interfaccia della dashboard è in **inglese** (etichette, caption, nomi
colonna); questo README resta in italiano per chi la mantiene.

### Screener

Tabella ordinata dal più scontato, con **settore e industria** in colonna,
filtri per valutazione / settore / industria / categoria Lynch e ricerca per
ticker o nome.

La colonna **Δ P/E vs ind.** confronta il P/E del titolo con la **mediana della
sua industria**: un P/E di 30 è caro per una utility ed economico per il
software, quindi il numero assoluto da solo dice metà della storia. Mediana e
non media perché i multipli hanno una coda lunga a destra e una sola società a
P/E 900 sposterebbe la media di tutto il gruppo. Se l'industria ha meno di **5**
società nel dataset il confronto ripiega sul settore, e il valore è marcato con
un **asterisco**: un gruppo più largo, quindi meno stringente, e va detto.

L'export CSV contiene **tutto**: bilanci, indicatori e ogni CAGR (3, 5 e 10 anni
per utile per azione, ricavi, free cash flow, utile netto e flusso di cassa
operativo), oltre alle colonne diagnostiche.

### Details (una scheda per titolo, quattro tab)

| Tab | Contenuto |
|---|---|
| 📉 **Valuation** | prezzo vs fair value nel tempo, **EPS e P/E accanto al prezzo**, categoria Lynch, confronto fra i due modelli, utili normalizzati |
| 📊 **Financials** | il quadro finanziario completo, con il delta di ogni voce rispetto all'industria |
| 📈 **Growth (5y)** | CAGR a 3/5/10 anni, ultimi esercizi come depositati, grafici |
| 🔍 **Data quality** | **formula e fonte di ogni metrica con link di verifica**, estremi di ogni CAGR, divergenze fra fonti, eventi societari, filing SEC |

Nella scheda **Valuation** i sei KPI sono in fila e il conto si chiude a vista:
`EPS × P/E target = fair value` e `prezzo ÷ EPS = P/E`.

### Il sanity check (scheda Data quality)

Due tabelle pensate per non fidarsi del programma:

**Come è calcolata ogni voce.** Per ciascuna: la formula in chiaro, se viene da
un deposito SEC o dal provider di mercato, e **il tag XBRL effettivamente letto
per quella società** — con il link diretto all'endpoint `companyconcept` della
SEC, che restituisce i fatti grezzi depositati per quel tag. Più tag su una voce
è normale: ASC 606 ha spostato tutti i ricavi su un tag nuovo nel 2018 e una
serie decennale attraversa quel confine.

**Ogni tasso di crescita con i suoi due estremi.** Metrica, finestra, base della
serie (TTM o esercizi), valore e data di partenza, valore e data di arrivo, span
effettivo in anni, tasso. Si verifica con
`((arrivo ÷ partenza) ^ (1 ÷ anni)) − 1`. Il CAGR è il numero più facile da far
sembrare qualunque cosa scegliendo l'anno di partenza: mostrare gli estremi rende
la scelta ispezionabile, e rende visibile quando un "+98% l'anno" dipende da una
base depressa. Le combinazioni non calcolabili sono elencate come tali invece di
restare vuote senza spiegazione.

---

## Aggiornare i dati da GitHub

### Il bottone

**Actions** → **Build Lynch data** → **Run workflow**. Tre scelte:

| Campo | Significato |
|---|---|
| **scope** | quale universo: `test`, `sp500`, `sp500+russell1000` (default), `russell1000`, `us-all` |
| **freq** | risoluzione dello **storico dei prezzi** — vedi sotto, non è la frequenza dei run |
| **shards** | in quanti job dividere il lavoro; serve solo a `us-all` |

Alla fine un job unisce i pezzi e committa i CSV aggiornati nella repo. Se hai
l'app su Streamlit Cloud, si riavvia da sola con i dati nuovi.

### Le cadenze automatiche

Attiva ora: **ogni giorno alle 4 del mattino, ora di Roma** (`cron: "0 2 * * *"`).

Nel workflow ci sono tutte e tre pronte all'uso, una attiva e due commentate:

```yaml
schedule:
  - cron: "0 2 * * *"      # ATTIVA — ogni giorno alle 4
  # - cron: "0 2 * * 1"    # ogni lunedì alle 4
  # - cron: "0 2 1 * *"    # il primo del mese alle 4
```

Per cambiare cadenza: commenta quella in uso, togli il commento a quella che
vuoi. Il bottone **Run workflow** resta sempre disponibile per un aggiornamento
immediato, indipendentemente dalla schedulazione.

#### ⚠️ Sono alternative, non cumulative

Attivarne più di una non raddoppia gli aggiornamenti: li fa litigare. Il dataset
è **un unico set di CSV** in `data/`, quindi se la giornaliera scrive
`sp500+russell1000` e la settimanale scrive `us-all`, il lunedì il file è
l'intero listino e il martedì torna alle 990 società — l'ultima che gira vince e
i dati oscillano fra universi diversi senza che nulla lo segnali. E se girassero
tutte con lo stesso universo, la più frequente renderebbe le altre inutili.
Tienine **una**.

#### L'ora è in UTC

GitHub non accetta fusi orari. `02:00` UTC sono le **04:00 a Roma** da fine marzo
a fine ottobre (ora legale) e le 03:00 nei mesi di ora solare. Non vale la pena
inseguire lo scarto: GitHub accoda i job schedulati e li avvia con ritardi che
arrivano a mezz'ora, quindi la precisione al minuto non esiste comunque.

#### Cosa protegge la run giornaliera

- **`concurrency`**: una build alla volta. Una run lunga può accavallarsi con la
  successiva, e due job che scrivono gli stessi CSV e fanno push sullo stesso
  branch si sovrascrivono a vicenda. Vanno in coda, senza cancellare quella in
  corso.
- **Controllo del secret in testa**: se manca `SEC_USER_AGENT` (o non contiene
  una email) la Action si ferma in dieci secondi con l'istruzione esatta, invece
  di installare tutto, girare i test e fallire venti minuti dopo.
- **Riepilogo nella pagina della run**: universo, risoluzione, job paralleli e
  tipo di avvio, così a colpo d'occhio sai cosa ha girato.
- **Se uno shard fallisce, il commit non parte.** Con `us-all` diviso su più job,
  un dataset a cui manca un pezzo sembrerebbe completo: meglio nessun
  aggiornamento che un universo troncato spacciato per intero.

#### Consumo di minuti Actions

Una run giornaliera su `sp500+russell1000` costa ~14 minuti (test + build +
commit), cioè ~420 minuti al mese. Su repository **pubbliche** i minuti sono
illimitati; su una privata il piano gratuito ne dà 2.000 al mese, quindi ci stai
comodamente. Con `us-all` quotidiano invece sforeresti: usalo a cadenza
settimanale.

### freq: cosa cambia (e cosa non cambia)

`freq` è la **risoluzione dello storico dei prezzi**, non la frequenza con cui
gira la build. Tocca solo il grafico della scheda Valuation e il peso del file.
**Non tocca** KPI, ROE, FCF, CAGR, delta di industria, screener e tabelle
finanziarie: quelli vengono dai depositi EDGAR e dai valori correnti.

| freq | Punti/anno | 17 anni di storia | File (990 società) | Cosa vedi |
|---|---|---|---|---|
| `D` giornaliero | ~250 | ~4.250 punti | ~21 MB | dettaglio non distinguibile a quello zoom |
| `W` settimanale | ~52 | ~880 punti | **4,2 MB** | ogni movimento leggibile — **default consigliato** |
| `M` mensile | 12 | ~200 punti | ~1 MB | i cali che rientrano in poche settimane spariscono |

Il mensile serve su `us-all`, dove il giornaliero sfonderebbe il limite di 100 MB
per file di GitHub. Un'avvertenza: la mediana a 3 anni dell'EPS normalizzato nel
grafico è calcolata su questa serie campionata, quindi con dati mensili userebbe
~36 punti invece di ~156 (la dashboard lo dichiara).

### Quanto pesa una build al giorno

Ogni run riscrive ~6 MB di CSV, e git conserva ogni versione come oggetto nuovo
(un `.gz` cambia per intero, non si comprime per differenze). In accodamento sono
**circa 2 GB di oggetti git all'anno**, mentre GitHub consiglia di restare sotto
1 GB.

Nel workflow c'è un interruttore per questo, in cima allo step *Commit dei dati
aggiornati*:

- `KEEP_SINGLE_DATA_COMMIT: "false"` (default) — ogni aggiornamento è un commit
  in più: storia completa dei dati, repo che cresce, e un `git pull` dal tuo
  computer funziona sempre.
- `KEEP_SINGLE_DATA_COMMIT: "true"` — l'ultimo commit di dati viene **riscritto**
  invece di accodato, quindi la repo resta a dimensione costante. Usa force-push,
  quindi se tieni una copia locale dopo un aggiornamento serve
  `git pull --rebase`. I commit di codice non vengono mai toccati.

Se aggiorni ogni giorno e non ti serve la storia dei dataset passati, metti
`"true"`.

---

## I dati finanziari: da dove escono e come sono calcolati

Tutto il **bilancio** viene dai depositi della società (XBRL company facts della
SEC), non da campi pre-confezionati di un provider: ogni formula è verificabile
riga per riga sul 10-K. Dal provider di mercato arriva solo ciò che nessun
bilancio contiene — prezzo, capitalizzazione, P/E prospettico, data della
prossima trimestrale.

| Voce | Formula |
|---|---|
| **P/E (trailing)** | prezzo ÷ EPS TTM *scelto in questa stessa riga* (mai copiato dal provider) |
| **P/E (forward)** | prezzo ÷ EPS atteso dagli analisti — l'unico dato che guarda avanti |
| **P/S** | capitalizzazione ÷ ricavi TTM |
| **ROE** | utile netto TTM ÷ patrimonio netto **medio** del periodo |
| **Equity ratio** | patrimonio netto ÷ totale attivo |
| **Earning power** | EBIT TTM ÷ totale attivo (per le banche: utile ante imposte) |
| **FCF** | flusso di cassa operativo − investimenti in immobilizzi |
| **FCF yield** | FCF TTM ÷ capitalizzazione |
| **Debito a lungo termine** | debito non corrente dall'ultimo stato patrimoniale |

Tre punti dove è facile sbagliare, e come sono risolti qui:

1. **I flussi di cassa in EDGAR sono cumulati da inizio esercizio**, non per
   trimestre: il fatto "Q3" di Apple copre nove mesi. Sommarne quattro darebbe
   un TTM gonfiato di circa due volte e mezzo. I trimestri discreti vengono
   ricavati differenziando i cumulati consecutivi, e il Q4 come *esercizio meno
   nove mesi*.
2. **Il ROE si calcola sul patrimonio medio**, non su quello di fine anno:
   dividere un flusso per una fotografia finale sovrastima il rendimento delle
   società che hanno appena fatto buyback.
3. **Il CAGR da una base negativa non esiste** e non viene inventato: resta
   vuoto. Un tasso composto che parte da una perdita non significa nulla.

Chi deposita solo il bilancio annuale (niente trimestrali) non viene scartato:
si usa l'ultimo esercizio e la scheda **lo dichiara**, invece di mescolarlo in
silenzio con i dati TTM delle altre.

### Affidabilità: cosa succede quando una fonte sbaglia o è in ritardo

**Due fonti indipendenti per l'EPS, e un arbitro.** EDGAR (ricostruito dai
trimestri depositati) e il provider di mercato. Se divergono oltre il 15% non si
sceglie a priori: si verifica quale valore è coerente con lo storico
normalizzato e produce un P/E implicito plausibile. Il caso che ha motivato
questo arbitro è Waste Management, dove il provider riportava 0,86 contro 6,91
reali — un P/E implicito di 277.

**Il P/E pubblicato è sempre prezzo ÷ EPS pubblicato**, ricalcolato, mai copiato
dal provider. Copiarlo produceva righe internamente incoerenti: Howmet mostrava
P/E 1.694 accanto a un prezzo e a un EPS che ne implicavano 67. Il valore del
provider resta in una colonna separata, per il confronto.

**Dati SEC in ritardo: due soglie, non una.** Una serie ferma al 2013 (Berkshire,
dopo il cambio di tagging) va buttata. Una serie ferma al trimestre precedente
(Citigroup, Molson Coors, Paramount) è completa e corretta, solo in ritardo:
buttarla farebbe sparire dallo screener società perfettamente valide. Sotto i
~200 giorni la serie è "corrente"; fino a 420 (800 per le annuali) si usa con
**l'età dichiarata** nel tab *Data quality*, e per i valori "di oggi" l'arbitro
preferisce il dato del provider; oltre, il ticker esce dal dataset con il motivo
scritto in `skipped.csv`.

**Blocco per troppe richieste.** Yahoo non pubblica un limite ma lo applica:
dopo qualche migliaio di chiamate ravvicinate risponde `YFRateLimitError` a
tutto. Il danno non è un errore visibile, è un dataset che si interrompe a metà
senza dirlo — in un test su S&P 500 sono state 34 società di fila, fra cui
Verizon e Vertex, tutte etichettate "senza prezzi". Ora c'è un limitatore di
frequenza in ingresso (`YF_RATE_LIMIT`, default 3/s) e una **pausa condivisa fra
tutti i thread** quando il blocco scatta comunque. Prezzi e split arrivano da una
sola richiesta invece di due, un terzo di occasioni in meno di essere bloccati.

**Un buco nella serie non si riempie con il dato vecchio.** Un EPS depositato
descrive il prezzo per al massimo ~400 giorni (che coprono anche chi deposita una
volta l'anno); oltre, il grafico mostra un **buco**, che è la rappresentazione
onesta. GoDaddy non ha EPS nelle API XBRL della SEC fra il 2017 e il 2022: senza
questo limite il suo utile del 2016 (−0,23 $) veniva riportato in avanti per nove
anni, la scheda dichiarava una perdita nel 2025 e la banda rossa "utili negativi"
copriva mezzo grafico.

**Cancelli di plausibilità.** Un fair value distante dal prezzo più di 20 volte
non è una valutazione, è un errore nei dati: non viene mostrato (Halliburton
risultava a 10 milioni di dollari per azione). Un EPS oltre 5.000 $ viene
scartato all'origine come errore di unità nei dati XBRL.

**Titoli senza dati non spariscono in silenzio:** finiscono in `skipped.csv` con
il motivo, e la dashboard li elenca in un pannello dedicato — così un titolo
mancante non sembra un problema dei filtri.

### Settore e industria

Prima yfinance; dove manca — e sull'intero listino manca spesso — si ricava dal
**codice SIC** che la SEC assegna a ogni filer, già presente nel file
`submissions` che scarichiamo per i link ai 10-K. Costa zero richieste in più ed
è completo: senza industria un titolo non avrebbe gruppo di confronto e
sparirebbe da ogni delta. La provenienza è dichiarata nel tab *Data quality*.

---

## Passo 5 — Deploy su Streamlit Cloud (opzionale)

Stessa logica del tuo setup Patterns-Screener.

1. **Crea la repo GitHub** e carica i file:
   ```bash
   cd lynch
   git init
   git add edgar_logic.py data_sources.py build_dataset.py app.py requirements.txt README.md
   git commit -m "Lynch valuation dashboard"
   git branch -M main
   git remote add origin https://github.com/TUO-USER/lynch-valuation.git
   git push -u origin main
   ```

2. **Committa anche i dati** (così Streamlit Cloud li trova senza rigenerarli):
   ```bash
   python build_dataset.py --universe sp500     # genera data/
   git add -f data/history.csv.gz data/fundamentals.csv data/financials_annual.csv
   git add -f data/events.csv data/filings.csv data/skipped.csv
   git commit -m "dataset"
   git push
   ```
   > In automatico ci pensa la GitHub Action `Build Lynch data`: scegli lo
   > scope (`test` / `sp500` / `us-all`) e, per l'intero listino, in quanti job
   > paralleli dividerlo. I job scrivono pezzi separati, un ultimo job li
   > unisce e committa — così nessuno supera il tetto di sei ore e non ci sono
   > commit concorrenti.

3. **Streamlit Cloud** → https://share.streamlit.io → *New app* → seleziona la repo,
   branch `main`, main file `app.py` → *Deploy*.

---

## Limiti da sapere (onesti)

- **Profondità storica:** EDGAR ha EPS dal ~2009 (obbligo XBRL). La linea storica
  parte da lì, non dal 1990 come la WMT di GuruFocus (che usa un DB a pagamento).
  I *prezzi* vanno più indietro, ma senza utili non c'è fair value da disegnare.
- **P/E fisso 15/20/25:** è la versione "classica" della linea. Lynch usava anche
  un fair-P/E legato alla crescita; qui è tenuto semplice e trasparente.
- **EPS negativo:** il fair value non viene disegnato (non ha senso un P/E su utili negativi).
- **yfinance rate limit:** su universi grandi può rallentare; il fallback Stooq
  aiuta sui prezzi, e settore/industria hanno il ripiego sul codice SIC.
- **Chi resta fuori:** filer esteri che depositano in IFRS (niente tag us-gaap),
  trust e fondi, e le società con EPS taggato solo per classe di azioni e senza
  utile netto ricavabile. Il motivo per ciascuno è in `data/skipped.csv`.
- **Il confronto con l'industria** usa le società **presenti nel dataset**, non
  l'intero mercato: con un universo parziale la mediana descrive ciò che è stato
  caricato. Il numero di società del gruppo è sempre dichiarato accanto al delta.
- **Il P/E prospettico e la data della prossima trimestrale** vengono dal
  consenso degli analisti: sono aspettative, non dati depositati.

---

## ⚠️ Disclaimer

Strumento educativo, **non consulenza finanziaria**. Il fair value a P/E fisso è
una semplificazione: considera sempre crescita, debito, marginalità, settore e
ciclo di mercato prima di qualsiasi decisione.

---

## Altre API gratuite valutate (e perché non sono state usate)

La pipeline resta su **SEC EDGAR + yfinance/Stooq**, che non hanno né chiavi né
tetti giornalieri. Le alternative esaminate:

| Fonte | Tier gratuito | Verdetto |
|---|---|---|
| **SEC `frames` API** | illimitata, nessuna chiave | **Utile in futuro.** Restituisce un concetto per *tutti* i filer di un trimestre: ~70 richieste coprirebbero l'EPS di tutto il mercato invece di una per società. Non adottata perché i filer con esercizio non solare vengono riassegnati al trimestre solare più vicino, e la ricostruzione TTM per società diventa meno controllabile. Da considerare se il tempo di build diventasse un problema. |
| **Financial Modeling Prep** | 250 richieste/giorno | Inutilizzabile: coprirebbe il 3% del listino al giorno. |
| **Alpha Vantage** | 25 richieste/giorno | Idem. |
| **Finnhub** | 60/minuto, ma i fondamentali sono a pagamento | Solo i prezzi sarebbero gratuiti, e per quelli yfinance basta. |
| **Tiingo** | 500 simboli/mese | Troppo poco per l'intero listino. |
| **Nasdaq Data Link (Sharadar)** | fondamentali a pagamento | Fuori dal vincolo "solo free". |

Le due integrazioni gratuite effettivamente aggiunte in questa revisione sono
entrambe SEC: l'**elenco ufficiale dei quotati** (`company_tickers_exchange.json`,
che rende possibile l'universo `us-all`) e il **codice SIC** dal file
`submissions` (settore e industria per ogni società, a costo zero perché quel
file lo scarichiamo già per i link ai 10-K). In più l'archivio **bulk
companyfacts** (`--bulk-facts`), che sostituisce migliaia di richieste con un
unico download.
