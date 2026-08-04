//
//  PriceHistory.swift — Le candele giornaliere, scaricate al momento.
//
//  L'UNICA PARTE DELL'APPLICAZIONE CHE PARLA CON LA RETE MENTRE E' IN USO, e
//  vale la pena dire perche' l'eccezione e' giustificata. Il dataset porta una
//  chiusura a settimana: con quella le candele non si disegnano, perche' una
//  candela ha bisogno di apertura, massimo, minimo e chiusura. Le serie
//  giornaliere complete di novecento societa' sarebbero mezzo milione di righe
//  in piu' nel database — sei megabyte scaricati da tutti per un grafico che
//  si guarda su un titolo alla volta.
//
//  PERCHE' QUESTO ENDPOINT E NON STOOQ. Stooq esponeva un CSV senza
//  autenticazione ed era la scelta naturale; da qualche tempo risponde con una
//  verifica JavaScript a proof-of-work, che dal telefono si potrebbe pure
//  risolvere ma e' esattamente la rincorsa che si voleva evitare. L'endpoint
//  `chart` di Yahoo, a differenza di `quoteSummary` che serve i profili, non
//  chiede ne' cookie ne' crumb: e' quello che alimenta i grafici pubblici.
//
//  QUANDO NON RISPONDE, IL GRAFICO LO DICE e la scheda resta utilizzabile: le
//  candele sono un di piu' rispetto alla earnings line, che vive nel dataset.
//

import Foundation

struct Candle: Identifiable, Hashable {
    let date: Date
    let open: Double
    let high: Double
    let low: Double
    let close: Double
    let volume: Double
    var id: Date { date }

    var isUp: Bool { close >= open }
}

enum PriceHistory {

    enum Range: String, CaseIterable, Identifiable {
        case oneMonth  = "1M"
        case sixMonths = "6M"
        case oneYear   = "1Y"
        case fiveYears = "5Y"
        case max       = "Max"
        var id: String { rawValue }

        /// La risoluzione che ha senso per quella finestra. Su cinque anni le
        /// candele orarie sarebbero diecimila bastoncini larghi un decimo di
        /// pixel; su un mese quelle giornaliere sono venti candele e il grafico
        /// non dice piu' niente di come si e' mossa la giornata.
        var interval: String {
            switch self {
            case .oneMonth:  return "1h"
            case .sixMonths: return "4h"
            case .oneYear:   return "1d"
            case .fiveYears, .max: return "1wk"
            }
        }

        /// Quanto indietro si CHIEDE, che e' piu' di quanto si mostra.
        ///
        /// LE MEDIE MOBILI HANNO BISOGNO DI RINCORSA. Una SMA a 200 periodi non
        /// esiste per i primi 199, quindi chiedendo esattamente la finestra
        /// visibile la media comparirebbe a due terzi del grafico. Si scarica
        /// il doppio e si mostra la parte richiesta: la media parte dal primo
        /// giorno visibile perche' i periodi che le servono stanno prima.
        var fetchRange: String {
            switch self {
            case .oneMonth:  return "3mo"
            case .sixMonths: return "1y"
            case .oneYear:   return "2y"
            case .fiveYears: return "10y"
            // `max` viene ridotto da Yahoo a 168 punti qualunque intervallo si
            // chieda, quindi non da' candele settimanali. Trent'anni sono la
            // storia intera per praticamente ogni societa' di questo listino.
            case .max:       return "30y"
            }
        }

        /// Da quando si mostra. `nil` per Max: si mostra tutto.
        var visibleFrom: Date? {
            let now = Date()
            switch self {
            case .oneMonth:  return now.addingTimeInterval(-30 * 86_400)
            case .sixMonths: return now.addingTimeInterval(-182 * 86_400)
            case .oneYear:   return now.addingTimeInterval(-365 * 86_400)
            case .fiveYears: return now.addingTimeInterval(-5 * 365 * 86_400)
            case .max:       return nil
            }
        }
    }

    enum Failure: LocalizedError {
        case unreachable
        case empty

        var errorDescription: String? {
            switch self {
            case .unreachable: return "Could not reach the price provider."
            case .empty:       return "No price history returned for this ticker."
            }
        }
    }

    /// Le candele di un titolo, dalla rincorsa fino a oggi.
    static func fetch(_ ticker: String, range: Range) async throws -> [Candle] {
        var components = URLComponents(
            string: "https://query1.finance.yahoo.com/v8/finance/chart/\(ticker)")!
        components.queryItems = [
            URLQueryItem(name: "range", value: range.fetchRange),
            URLQueryItem(name: "interval", value: range.interval),
        ]
        var request = URLRequest(url: components.url!)
        // Senza un User-Agent da browser l'endpoint risponde 403. Non e'
        // un aggiramento: e' quello che manda qualunque pagina che disegni
        // lo stesso grafico.
        request.setValue("Mozilla/5.0", forHTTPHeaderField: "User-Agent")
        request.timeoutInterval = 15

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw Failure.unreachable
        }

        let decoded = try JSONDecoder().decode(ChartResponse.self, from: data)
        guard let result = decoded.chart.result?.first,
              let quote = result.indicators.quote.first else { throw Failure.empty }

        var candles: [Candle] = []
        for (i, stamp) in result.timestamp.enumerated() {
            // Una giornata senza scambi arriva con i campi a null. Saltarla e'
            // corretto: disegnarla come zero produrrebbe una candela che tocca
            // l'asse e schiaccia tutto il resto del grafico.
            guard i < quote.open.count,
                  let o = quote.open[i], let h = quote.high[i],
                  let l = quote.low[i], let c = quote.close[i] else { continue }
            candles.append(Candle(date: Date(timeIntervalSince1970: TimeInterval(stamp)),
                                  open: o, high: h, low: l, close: c,
                                  volume: i < quote.volume.count
                                      ? (quote.volume[i].map(Double.init) ?? 0) : 0))
        }
        guard !candles.isEmpty else { throw Failure.empty }
        return candles
    }

    // -----------------------------------------------------------------------

    private struct ChartResponse: Decodable {
        let chart: Chart
        struct Chart: Decodable { let result: [Result]? }
        struct Result: Decodable {
            let timestamp: [Int]
            let indicators: Indicators
        }
        struct Indicators: Decodable { let quote: [Quote] }
        struct Quote: Decodable {
            let open: [Double?]
            let high: [Double?]
            let low: [Double?]
            let close: [Double?]
            let volume: [Int?]
        }
    }
}
