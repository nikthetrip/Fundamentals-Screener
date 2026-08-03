//
//  DataStore.swift — Da dove arrivano i dati e dove restano.
//
//  IL TELEFONO NON CALCOLA NIENTE. La pipeline Python gira ogni notte dentro
//  una GitHub Action, produce i CSV, li fa verificare da audit_dataset.py e
//  impacchetta screener.sqlite.gz nel repository. L'app scarica quel file e
//  legge. Non ricostruisce un fair value, non riclassifica un titolo, non
//  ricalcola una mediana di settore: se lo facesse, esisterebbero due
//  implementazioni della stessa logica — quella verificata dai test e quella
//  scritta in Swift — e prima o poi mostrerebbero due numeri diversi per la
//  stessa societa'.
//
//  L'ETAG E' LA RAGIONE PER CUI SI PUO' RIAPRIRE L'APP DIECI VOLTE AL GIORNO.
//  Il dataset cambia una volta ogni notte. Con l'ETag, dieci aperture sono
//  nove richieste che tornano "304, non e' cambiato" in qualche decina di
//  millisecondo e un solo scaricamento vero.
//

import Foundation
import Combine

@MainActor
final class DataStore: ObservableObject {

    enum State: Equatable {
        case idle
        case downloading(Double)     // 0…1, oppure -1 quando la dimensione non e' nota
        case unpacking
        case ready
        case failed(String)
    }

    @Published private(set) var state: State = .idle
    @Published private(set) var stocks: [Stock] = []
    @Published private(set) var dataGeneratedAt: String?
    /// Vero quando i dati mostrati vengono dalla cache e la rete non ha
    /// risposto. Non e' un errore — l'app funziona lo stesso — ma va detto.
    @Published private(set) var isOffline = false

    private var db: Database?

    private static let remote = URL(string:
        "https://raw.githubusercontent.com/nikthetrip/Fundamentals-Screener/main/data/screener.sqlite.gz")!
    private static let etagKey = "screener.db.etag"

    private var cacheURL: URL {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory,
                                           in: .userDomainMask)[0]
        try? FileManager.default.createDirectory(at: dir,
                                                 withIntermediateDirectories: true)
        return dir.appendingPathComponent("screener.sqlite")
    }

    // -----------------------------------------------------------------------
    // AVVIO
    // -----------------------------------------------------------------------

    /// Apre subito la cache se c'e', poi controlla se ne esiste una piu'
    /// recente. In quest'ordine: chi riapre l'app vede la lista mentre la
    /// rete lavora, invece di guardare un indicatore di attesa per dati che
    /// ha gia' sul telefono.
    func start() async {
        if FileManager.default.fileExists(atPath: cacheURL.path) {
            open(cacheURL)
        }
        await refresh(force: false)
    }

    func refresh(force: Bool) async {
        do {
            let updated = try await download(force: force)
            if updated { open(cacheURL) }
            isOffline = false
            if case .failed = state, db != nil { state = .ready }
        } catch {
            isOffline = true
            // Con una copia in cache l'errore di rete non e' una schermata di
            // errore: e' una riga che dice da quando sono fermi i dati.
            if db == nil {
                state = .failed(error.localizedDescription)
            } else if case .ready = state {
                // niente da fare: si continua con quello che c'e'
            } else {
                state = .ready
            }
        }
    }

    // -----------------------------------------------------------------------
    // SCARICAMENTO
    // -----------------------------------------------------------------------

    /// Restituisce `true` se il database e' stato sostituito.
    private func download(force: Bool) async throws -> Bool {
        var request = URLRequest(url: Self.remote)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        if !force, db != nil,
           let etag = UserDefaults.standard.string(forKey: Self.etagKey) {
            request.setValue(etag, forHTTPHeaderField: "If-None-Match")
        }

        if db == nil { state = .downloading(-1) }

        let (tempURL, response) = try await URLSession.shared.download(for: request)
        defer { try? FileManager.default.removeItem(at: tempURL) }

        guard let http = response as? HTTPURLResponse else { return false }
        if http.statusCode == 304 { return false }
        guard http.statusCode == 200 else {
            throw NSError(domain: "DataStore", code: http.statusCode, userInfo: [
                NSLocalizedDescriptionKey:
                    "The server answered \(http.statusCode) instead of sending the data."])
        }

        state = .unpacking
        // Si scompatta accanto, e solo a lavoro finito si sostituisce: se
        // l'app venisse chiusa a meta', la cache buona sarebbe ancora li'.
        let staging = cacheURL.appendingPathExtension("new")
        try? FileManager.default.removeItem(at: staging)
        try Gzip.inflate(source: tempURL, to: staging)

        // Il database nuovo va aperto PRIMA di rimpiazzare quello vecchio:
        // se dichiara uno schema che questa versione non sa leggere, si
        // scarta il file scaricato invece di restare senza dati.
        _ = try Database(path: staging.path)

        try? FileManager.default.removeItem(at: cacheURL)
        try FileManager.default.moveItem(at: staging, to: cacheURL)

        if let etag = http.value(forHTTPHeaderField: "Etag") {
            UserDefaults.standard.set(etag, forKey: Self.etagKey)
        }
        return true
    }

    // -----------------------------------------------------------------------
    // APERTURA
    // -----------------------------------------------------------------------

    private func open(_ url: URL) {
        do {
            let database = try Database(path: url.path)
            db = database
            dataGeneratedAt = database.meta("data_generated_at")
            availableTables = Set(database.query(
                "SELECT name FROM sqlite_master WHERE type = 'table'") {
                    $0.string(0) ?? ""
                })
            stocks = database.query(
                "SELECT \(Stock.selectList) FROM fundamentals ORDER BY ticker",
                row: Stock.init)
            state = .ready
        } catch {
            db = nil
            state = .failed(error.localizedDescription)
        }
    }

    // -----------------------------------------------------------------------
    // INTERROGAZIONI DELLA SCHEDA
    //
    // Non stanno in memoria: 658.000 punti di storico caricati all'avvio
    // sarebbero centinaia di megabyte per mostrarne novecento alla volta.
    // Con l'indice, la serie di un titolo si legge in meno di un millisecondo.
    // -----------------------------------------------------------------------

    func history(_ ticker: String) -> [HistoryPoint] {
        db?.query("""
            SELECT date, price, eps FROM history
            WHERE ticker = ? ORDER BY date
            """, [.text(ticker)]) {
            HistoryPoint(date: $0.int(0) ?? 0, price: $0.double(1), eps: $0.double(2))
        } ?? []
    }

    func annual(_ ticker: String) -> [AnnualRow] {
        db?.query("""
            SELECT fy_end, revenue, net_income, ebit, ocf, capex, fcf, assets,
                   equity, total_debt, cash, dividends_paid, eps
            FROM financials_annual WHERE ticker = ? ORDER BY fy_end
            """, [.text(ticker)]) {
            AnnualRow(fyEnd: $0.string(0) ?? "", revenue: $0.double(1),
                      netIncome: $0.double(2), ebit: $0.double(3),
                      ocf: $0.double(4), capex: $0.double(5), fcf: $0.double(6),
                      assets: $0.double(7), equity: $0.double(8),
                      totalDebt: $0.double(9), cash: $0.double(10),
                      dividendsPaid: $0.double(11), eps: $0.double(12))
        } ?? []
    }

    func filings(_ ticker: String) -> [Filing] {
        db?.query("""
            SELECT form, filing_date, period_date, url FROM filings
            WHERE ticker = ? ORDER BY filing_date DESC
            """, [.text(ticker)]) {
            Filing(form: $0.string(0) ?? "?", filingDate: $0.string(1) ?? "",
                   periodDate: $0.string(2), url: $0.string(3) ?? "")
        }.filter { !$0.url.isEmpty } ?? []
    }

    func events(_ ticker: String) -> [CorporateEvent] {
        db?.query("""
            SELECT date, type, detail FROM events
            WHERE ticker = ? ORDER BY date DESC
            """, [.text(ticker)]) {
            CorporateEvent(date: $0.string(0) ?? "", type: $0.string(1) ?? "",
                           detail: $0.string(2) ?? "")
        } ?? []
    }

    /// Le tabelle prodotte da build_extras.py possono non esserci: un database
    /// costruito prima che quel passaggio entrasse nella pipeline non le ha, e
    /// interrogare una tabella assente fa fallire la query. Si controlla una
    /// volta all'apertura invece di far dipendere una scheda dall'ordine in cui
    /// sono stati aggiornati due repository diversi.
    private var availableTables: Set<String> = []

    func profile(_ ticker: String) -> Profile? {
        guard availableTables.contains("profile") else { return nil }
        return db?.queryFirst("""
            SELECT long_name, summary, country, city, state, website, employees,
                   target_low, target_mean, target_median, target_high,
                   n_analysts, recommendation, recommendation_mean
            FROM profile WHERE ticker = ?
            """, [.text(ticker)]) {
            Profile(longName: $0.string(0), summary: $0.string(1),
                    country: $0.string(2), city: $0.string(3),
                    state: $0.string(4), website: $0.string(5),
                    employees: $0.double(6), targetLow: $0.double(7),
                    targetMean: $0.double(8), targetMedian: $0.double(9),
                    targetHigh: $0.double(10), analystCount: $0.int(11),
                    recommendation: $0.string(12),
                    recommendationMean: $0.double(13))
        } ?? nil
    }

    /// I segmenti dell'esercizio piu' recente, per asse.
    func segments(_ ticker: String) -> [String: [SegmentRow]] {
        guard availableTables.contains("segments") else { return [:] }
        let rows = db?.query("""
            SELECT axis, title, note, period_end, member, value, total
            FROM segments WHERE ticker = ?
              AND period_end = (SELECT MAX(period_end) FROM segments WHERE ticker = ?)
            ORDER BY axis, value DESC
            """, [.text(ticker), .text(ticker)]) {
            SegmentRow(axis: $0.string(0) ?? "", title: $0.string(1),
                       note: $0.string(2), periodEnd: $0.string(3) ?? "",
                       member: $0.string(4) ?? "", value: $0.double(5) ?? 0,
                       total: $0.double(6))
        } ?? []
        return Dictionary(grouping: rows, by: \.axis)
    }

    func cagrDetail(_ ticker: String) -> [CagrDetail] {
        db?.query("""
            SELECT metric, horizon_years, series_basis, n_points, start_date,
                   start_value, end_date, end_value, span_years, cagr_pct
            FROM cagr_detail WHERE ticker = ?
            ORDER BY metric, horizon_years
            """, [.text(ticker)]) {
            CagrDetail(metric: $0.string(0) ?? "", horizonYears: $0.int(1) ?? 0,
                       seriesBasis: $0.string(2), nPoints: $0.int(3),
                       startDate: $0.string(4), startValue: $0.double(5),
                       endDate: $0.string(6), endValue: $0.double(7),
                       spanYears: $0.double(8), cagrPct: $0.double(9))
        } ?? []
    }

    // -----------------------------------------------------------------------
    // MEDIANE DI INDUSTRIA
    //
    // Il confronto con i pari e' meta' di ogni voce della scheda finanziaria:
    // un ROE del 18% non e' ne' buono ne' cattivo finche' non si sa a quanto
    // stanno le concorrenti. Le mediane si calcolano una volta sola e restano
    // qui, perche' ricalcolarle a ogni apertura di scheda vorrebbe dire
    // riscorrere novecento righe per mostrarne una.
    // -----------------------------------------------------------------------

    private var peerCache: [String: [String: Double]] = [:]

    /// Il numero minimo di societa' perche' una mediana significhi qualcosa.
    /// E' lo stesso valore della dashboard (`MIN_PEERS` in app.py): sotto,
    /// la "mediana dell'industria" e' la seconda societa' in ordine di valore,
    /// e confrontarsi con una singola concorrente non e' un confronto.
    private static let minPeers = 5

    /// Il gruppo di riferimento di una societa'.
    ///
    /// PRIMA L'INDUSTRIA, POI IL SETTORE. Nel dataset ci sono 132 industrie e
    /// 26 hanno meno di tre societa': Apple, per dire, e' l'unica "Consumer
    /// Electronics" del listino. Senza il ripiego sul settore, le schede di
    /// quelle societa' — spesso le piu' interessanti, perche' sono quelle
    /// senza concorrenti diretti — sarebbero le uniche senza alcun confronto.
    func peers(for stock: Stock) -> (medians: [String: Double], label: String?) {
        if let industry = stock.industry,
           stocks.filter({ $0.industry == industry }).count >= Self.minPeers {
            return (medians(key: "i:" + industry) { $0.industry == industry },
                    "industry «\(industry)»")
        }
        if let sector = stock.sector,
           stocks.filter({ $0.sector == sector }).count >= Self.minPeers {
            return (medians(key: "s:" + sector) { $0.sector == sector },
                    "\(sector) sector — too few companies in this industry")
        }
        return ([:], nil)
    }

    func peerMedians(industry: String?) -> [String: Double] {
        guard let industry, !industry.isEmpty else { return [:] }
        return medians(key: "i:" + industry) { $0.industry == industry }
    }

    private func medians(key cacheKey: String,
                         where match: (Stock) -> Bool) -> [String: Double] {
        if let hit = peerCache[cacheKey] { return hit }

        let peers = stocks.filter(match)
        guard peers.count >= Self.minPeers else { peerCache[cacheKey] = [:]; return [:] }

        func med(_ pick: (Stock) -> Double?) -> Double? {
            let v = peers.compactMap(pick).sorted()
            guard !v.isEmpty else { return nil }
            let m = v.count / 2
            return v.count % 2 == 1 ? v[m] : (v[m - 1] + v[m]) / 2
        }

        // Si calcola la mediana di OGNI metrica descritta in Metrics.swift,
        // non di un elenco scelto a mano: cosi' una voce aggiunta ad app.py
        // arriva nell'app gia' con il suo confronto contro i pari, senza che
        // qualcuno debba ricordarsi di aggiungerla anche qui.
        var out: [String: Double] = [:]
        for key in Metrics.comparableKeys {
            if let m = med({ $0.value(for: key) }) { out[key] = m }
        }
        peerCache[cacheKey] = out
        return out
    }

    func skipped() -> [(String, String)] {
        db?.query("SELECT ticker, motivo FROM skipped ORDER BY ticker") {
            ($0.string(0) ?? "", $0.string(1) ?? "")
        } ?? []
    }
}
