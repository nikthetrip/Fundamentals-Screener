# Fundamentals Screener — app iPhone

L'applicazione iOS che legge lo stesso dataset della dashboard Streamlit.
SwiftUI nativa, nessuna dipendenza esterna, nessun server.

---

## Come sta insieme

```
GitHub Action (ogni notte)                    iPhone
──────────────────────────                    ──────
build_dataset.py    → data/*.csv
audit_dataset.py    verifica
build_extras.py     → profile.csv          ┌─ scarica screener.sqlite.gz (7 MB)
build_mobile_db.py  → screener.sqlite.gz ──┤  con ETag: se non e' cambiato,
                      committato nel repo  └─ risposta 304 e nessun traffico
                                              └─ apre il database in sola lettura
```

**Il telefono non calcola niente.** Fair value, categoria di Lynch, mediane di
settore, CAGR: arrivano gia' fatti dalla pipeline Python, che e' quella che i
test verificano. Se l'app ricalcolasse anche un solo numero, esisterebbero due
implementazioni della stessa logica e prima o poi mostrerebbero due valori
diversi per la stessa societa'.

L'unica trasformazione fatta a bordo e' la mediana mobile a tre anni dell'EPS
per il grafico normalizzato: e' una scelta di rappresentazione (quale serie
disegnare), e anche la dashboard la fa nello strato che disegna.

---

## Aprire il progetto

```bash
open ios/FundamentalsScreener.xcodeproj
```

Non serve nient'altro: niente CocoaPods, niente Swift Package Manager. SQLite e
Swift Charts sono dentro iOS.

## Compilare e provare dal terminale

```bash
xcodebuild -project ios/FundamentalsScreener.xcodeproj -scheme FundamentalsScreener -sdk iphonesimulator -destination 'platform=iOS Simulator,name=iPhone 17 Pro' build
```

Durante lo sviluppo, per aprire direttamente una scheda senza doverla toccare
(utile quando si verifica da riga di comando):

```bash
SIMCTL_CHILD_OPEN_TICKER=AAPL SIMCTL_CHILD_OPEN_SECTION=Valutazione xcrun simctl launch booted com.nikthetrip.FundamentalsScreener
```

Le due variabili funzionano **solo nella build di Debug**: nella versione che
finisce sul telefono quel codice non esiste.

---

## Installarla sul proprio iPhone

1. Collega l'iPhone al Mac e sbloccalo; alla richiesta rispondi **Autorizza**.
2. In Xcode, seleziona il dispositivo nella barra in alto al posto del
   simulatore.
3. Target `FundamentalsScreener` → **Signing & Capabilities**: «Automatically
   manage signing» attivo, Team impostato sul proprio.
4. Premi Esegui. Alla prima installazione l'iPhone rifiuta l'avvio finche' non
   si autorizza lo sviluppatore in *Impostazioni → Generali → VPN e gestione
   dispositivi*.

**Quanto dura.** Con un Apple ID gratuito il profilo scade dopo **7 giorni** e
l'app va reinstallata; con l'Apple Developer Program (99 $/anno) dura un anno.

**Sulla distribuzione.** Questa app e' pensata per uso personale sul proprio
telefono. Pubblicarla sull'App Store porrebbe due questioni che qui non si
pongono: l'icona e' derivata da un'incisione di terzi che ritrae una persona
reale, e un'applicazione che assegna giudizi di valutazione a titoli quotati
ricade nelle regole Apple sulle app finanziarie.

---

## Struttura

| Cartella | Cosa contiene |
|---|---|
| `Data/` | Accesso a SQLite, scaricamento con ETag, scompattazione gzip, modelli |
| `Design/` | Colori, tipografia, formati, componenti condivisi |
| `Views/` | Screener, filtri, scheda titolo con le cinque sezioni |

L'icona viene da `ios/icon-source.jpeg` — un hedcut esistente — adattata da
`tools/make_icon.py`:

```bash
python tools/make_icon.py
```

Lo script non ridisegna niente: risolve tre problemi di formato. La sorgente e'
160x148 e l'icona ne vuole 1024 (sei ingrandimenti e mezzo, su un disegno fatto
di linee sottili); e' un JPEG, quindi ogni linea ha attorno un alone di
compressione che ingrandito diventa sfocatura; e non e' quadrata, con il disegno
che tocca il bordo superiore, mentre iOS smussa gli angoli. Si allarga il
contrasto **prima** di ingrandire — cosi' l'alone viene schiacciato sul bianco
invece di essere interpolato — poi si riaffilano le linee, e le spalle sfumano
verso la carta perche' un rettangolo incollato dentro un'icona si vede subito.

Per sostituirla basta mettere un'altra immagine in `ios/icon-source.jpeg`.

Con `--preview` salva anche la mappa di toni in `/tmp/icon_tone.png`, che e' il
modo per capire perche' un'ombra viene dove viene.

Restano due modalita' per **generare** un'incisione da zero, se un giorno non
si avesse un ritratto di partenza:

```bash
python tools/make_icon.py --photo ritratto.jpg   # incide una fotografia
python tools/make_icon.py --no-source            # ritratto costruito a profili
```

La seconda serve a poco e vale la pena dire perche': un volto costruito con
profili interpolati e ombre calcolate puo' diventare un uomo anziano credibile
con gli occhiali pesanti, ma non diventera' mai *quella* persona. Una
somiglianza sta nelle asimmetrie e nelle proporzioni irripetibili di un viso
vero — esattamente cio' che una costruzione geometrica non ha. Con `--photo`
invece la somiglianza arriva gratis, perche' e' gia' nel materiale di partenza.

---

## Note di progetto

**Perche' un solo file SQLite e non i CSV.** Lo storico e' 658.000 righe. Un
telefono non le carica in memoria per mostrarne novecento, e sei richieste HTTP
su rete mobile sono sei modi diversi di fallire a meta'. Con un indice, la serie
di un titolo si legge in meno di un millisecondo.

**Perche' 29 MB e non 62.** Lo storico e' scritto come tabella `WITHOUT ROWID`
con chiave (ticker, data) — la tabella e' il proprio indice invece di essere una
copia piu' un indice che ne duplica le colonne — e le date sono interi
`YYYYMMDD`, che si ordinano come il testo ISO ma stanno in quattro byte.

**Perche' i rapporti derivati stanno in `derived_metrics.py`.** ROIC,
conversione in cassa, payout ed earnings yield non sono nel dataset: erano
calcolati dentro `app.py`. Con due lettori — dashboard e app — quel codice deve
essere uno solo, altrimenti il ROIC di Apple sarebbe due numeri diversi nei due
posti. Ora e' una funzione sola, importata da entrambi, e i valori finiscono
nella tabella `fundamentals` del database mobile perche' le mediane di industria
si costruiscono sulle colonne.

**Perche' le spiegazioni delle metriche sono generate.** `tools/export_metrics.py`
legge il dizionario `METRICS` di `app.py` — sessanta voci, ventunomila caratteri
— e ne fa `Design/Metrics.swift`. Ricopiarle a mano avrebbe prodotto due testi
divergenti alla prima correzione. Dopo aver toccato `METRICS`, rilanciare:

```bash
python tools/export_metrics.py
```

**Perche' i profili arrivano dalla pipeline e non dall'app.** Le API di Yahoo
sono protette da un cookie piu' un *crumb* che cambia senza preavviso: yfinance
esiste in buona parte per inseguire quel meccanismo. Reimplementarlo in Swift
significherebbe avere una copia che si aggiorna solo quando l'utente aggiorna
l'app dall'App Store.

**Attenzione al segno di `discount_vs_fv15_pct`.** Nonostante il nome, e' il
prezzo *rispetto* al fair value: `+85%` vuol dire che il titolo costa l'85% in
piu' di quanto vale. Lo screener ordina quindi in senso **crescente**, e nel
codice Swift il campo si chiama `premiumVsFV15` per evitare che qualcuno
riproponga la lista con i titoli piu' cari in cima colorati di verde.
