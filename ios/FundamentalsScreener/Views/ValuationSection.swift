//
//  ValuationSection.swift — La earnings line di Lynch.
//
//  IL GRAFICO E' L'APPLICAZIONE. Tutto il resto sono numeri che spiegano
//  questa figura: il prezzo reale contro il prezzo che il titolo avrebbe a un
//  multiplo fisso degli utili. Dove la linea del prezzo sta sotto quella degli
//  utili capitalizzati, il mercato paga meno di quel multiplo; dove sta sopra,
//  di piu'. Un solo asse, perche' sono entrambi dollari per azione.
//
//  IL TOOLTIP NON E' UN ORNAMENTO. Nella dashboard si passa il mouse sul
//  grafico e si legge prezzo, fair value e premio a quella data. Senza,
//  restano due curve e l'occhio deve stimare distanze: "quanto era caro nel
//  2021" diventa una domanda senza risposta. Qui si tiene il dito sul grafico.
//
//  LA MEDIANA MOBILE SI CALCOLA SULLA SERIE INTERA e poi si taglia la finestra
//  visibile, non il contrario: altrimenti i primi punti mostrati userebbero una
//  finestra di tre anni che ne contiene tre mesi.
//

import SwiftUI
import Charts

struct ValuationSection: View {
    let stock: Stock
    let model: ValuationModel
    @EnvironmentObject private var store: DataStore

    /// La base vale per tutta l'applicazione e si cambia da qui o dai filtri:
    /// e' la stessa impostazione, non due.
    @Binding var epsBasis: EPSBasis

    /// `nil` significa «il P/E equo della sua categoria», che varia da titolo
    /// a titolo. Gli altri tre sono i multipli fissi del Lynch Chart.
    @State private var pe: Double? = nil
    @State private var window: Window = .tenYears
    /// LOGARITMICA DI PARTENZA. Su dieci o vent'anni di storia una scala
    /// lineare schiaccia tutto il primo decennio contro l'asse: la earnings
    /// line di Apple fra il 2009 e il 2015 diventa una riga piatta, e il
    /// confronto fra prezzo e utili capitalizzati — che e' la ragione per cui
    /// esiste questo grafico — non si legge piu'. In scala logaritmica la
    /// stessa distanza verticale e' la stessa variazione percentuale ovunque.
    @State private var logScale = true
    @State private var history: [HistoryPoint] = []
    @State private var selectedDate: Date?
    /// La mediana mobile, calcolata una volta sola quando cambia il titolo.
    ///
    /// ERA UNA PROPRIETA' CALCOLATA, e con 879 punti settimanali significava
    /// centomila operazioni a ogni ridisegno — cioe' a ogni movimento del dito
    /// sul grafico. Un valore che dipende solo dallo storico non ha ragione di
    /// essere ricalcolato quando cambia la selezione.
    @State private var normalizedEPS: [Int: Double] = [:]

    /// LA BASE EFFETTIVA, non quella chiesta. Se la categoria impone gli utili
    /// normalizzati — una ciclica, un turnaround — il grafico li usa anche
    /// quando nei filtri c'e' scritto "corrente": diversamente la stessa
    /// scheda mostrerebbe un fair value di 24 dollari in cima e una linea da
    /// 72 sotto, e sarebbero entrambi giusti per basi diverse. Chi vuole
    /// vedere gli utili correnti su una ciclica ha comunque il grafico a P/E
    /// fisso, che e' l'altro modello.
    private var normalized: Bool { usingNormalized }

    enum Window: String, CaseIterable, Identifiable {
        case fiveYears = "5 years"
        case tenYears  = "10 years"
        case all       = "All"
        var id: String { rawValue }
        var years: Int? {
            switch self {
            case .fiveYears: return 5
            case .tenYears:  return 10
            case .all:       return nil
            }
        }
    }

    private var peers: [String: Double] { store.peers(for: stock).medians }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            classification
            chartPanel
            headlineNumbers
            derivation
            modelsCompared
            normalizedEarnings
        }
        .task(id: stock.ticker) {
            history = store.history(stock.ticker)
            normalizedEPS = Self.rollingMedian(history)
        }
    }

    /// La categoria e il perche'.
    ///
    /// STA QUI E NON IN OVERVIEW. La classificazione di Lynch non dice che
    /// azienda sia — dice con quale metro va valutata, ed e' l'ancora da cui
    /// esce il fair value disegnato due riquadri piu' sotto. Metterla fra
    /// l'attivita' e i segmenti, come avevo fatto, la faceva sembrare
    /// un'anagrafica.
    private var classification: some View {
        Panel {
            SectionHeader(title: "Lynch category")
            HStack {
                CategoryTag(category: stock.lynchCategory)
                Spacer()
                HStack(spacing: 5) {
                    Circle()
                        .fill(LynchCategory.confidenceColor(stock.lynchConfidence))
                        .frame(width: 7, height: 7)
                    Text(LynchCategory.confidenceLabel(stock.lynchConfidence))
                        .font(.caption2)
                        .foregroundStyle(Palette.inkMuted)
                }
            }
            if let note = stock.lynchNote { Caption(text: note) }
            Divider().overlay(Palette.separator)
            KPIGrid(items: [
                ("lynch_fair_pe", stock.lynchFairPE, nil),
                ("growth_5y_cagr", stock.growth5yCAGR, peers["growth_5y_cagr"]),
            ])
            if let basis = stock.lynchPEBasis { Caption(text: "Anchor: \(basis)") }
            if let reason = stock.lynchConfidenceNote {
                Caption(text: "Confidence: \(reason)")
            }
        }
    }

    // -----------------------------------------------------------------------
    // IL GRAFICO
    // -----------------------------------------------------------------------

    private var chartPanel: some View {
        Panel {
            SectionHeader(title: "Price vs earnings line",
                          note: normalized
                            ? "Normalized EPS — 3-year rolling median"
                            : "Trailing twelve-month EPS as filed")

            if series.isEmpty {
                EmptyNote(text: "No price history on file for this ticker.")
            } else {
                readout
                chart
                    .frame(height: 240)
                    .padding(.top, 2)

                HStack(spacing: 14) {
                    legend(color: Palette.ink, label: "Price")
                    legend(color: Palette.accent,
                           label: pe == nil
                               ? "Fair value at its category P/E \(Fmt.ratio(effectivePE))"
                               : "Fair value at a flat P/E \(Fmt.ratio(effectivePE, digits: 0))")
                    Spacer()
                }
            }

            Divider().overlay(Palette.separator)

            Picker("Window", selection: $window) {
                ForEach(Window.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)

            Picker("Multiple", selection: $pe) {
                if stock.lynchFairPE != nil {
                    Text("Category").tag(Double?.none)
                }
                Text("15").tag(Double?.some(15))
                Text("20").tag(Double?.some(20))
                Text("25").tag(Double?.some(25))
            }
            .pickerStyle(.segmented)

            Toggle("Logarithmic scale", isOn: $logScale)
                .font(.footnote)

            // L'interruttore e' qui perche' e' qui che si guarda la linea, ma
            // scrive la stessa impostazione dei filtri: cambiarlo qui la cambia
            // per tutta l'applicazione.
            Toggle(isOn: Binding(
                get: { epsBasis == .normalized },
                set: { epsBasis = $0 ? .normalized : .current })) {
                VStack(alignment: .leading, spacing: 1) {
                    Text("Normalized EPS (3-year median)").font(.footnote)
                    if usingNormalized && epsBasis == .current {
                        Text("required by this category — the multiple is "
                             + "applied to mid-cycle earnings")
                            .font(.caption2).foregroundStyle(Palette.caution)
                    } else {
                        Text("also changes the fair value below · shared with Filters")
                            .font(.caption2).foregroundStyle(Palette.inkFaint)
                    }
                }
            }
            .disabled(usingNormalized && epsBasis == .current)

            if normalized {
                Caption(text: "The three-year median takes the single "
                        + "exceptional quarter — good or bad — out of the "
                        + "multiplicand. On a cyclical company it is the only "
                        + "way not to capitalise the top of the cycle.")
            }
        }
    }

    /// La riga sopra il grafico: quello che il dito sta indicando, oppure
    /// l'ultimo punto quando non si sta toccando niente.
    private var readout: some View {
        let point = selectedPoint ?? series.last
        let fv = point.flatMap(fairValue)
        return HStack(alignment: .firstTextBaseline, spacing: 16) {
            VStack(alignment: .leading, spacing: 1) {
                Text(point.map { Fmt.date($0.date) } ?? Fmt.dash)
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(Palette.inkFaint)
                Text(Fmt.usd(point?.price))
                    .font(.numberBody.weight(.semibold))
                    .foregroundStyle(Palette.ink)
                Text("price").font(.system(size: 9))
                    .foregroundStyle(Palette.inkFaint)
            }
            VStack(alignment: .leading, spacing: 1) {
                Text(" ").font(.system(size: 10))
                Text(Fmt.usd(fv))
                    .font(.numberBody.weight(.semibold))
                    .foregroundStyle(Palette.accent)
                Text("fair value").font(.system(size: 9))
                    .foregroundStyle(Palette.inkFaint)
            }
            VStack(alignment: .leading, spacing: 1) {
                Text(" ").font(.system(size: 10))
                if let price = point?.price, let fv, fv > 0 {
                    DeltaText(value: (price / fv - 1) * 100,
                              font: .numberBody.weight(.semibold), invert: true)
                } else {
                    Text(Fmt.dash).font(.numberBody)
                        .foregroundStyle(Palette.inkFaint)
                }
                Text("premium").font(.system(size: 9))
                    .foregroundStyle(Palette.inkFaint)
            }
            Spacer()
        }
        .padding(.vertical, 2)
    }

    private var chart: some View {
        Chart {
            ForEach(series) { p in
                if let price = p.price {
                    LineMark(x: .value("Date", p.day),
                             y: .value("Dollars", price),
                             series: .value("Series", "Price"))
                        .foregroundStyle(Palette.ink)
                        .lineStyle(StrokeStyle(lineWidth: 1.6))
                }
            }
            ForEach(series) { p in
                if let fv = fairValue(p) {
                    LineMark(x: .value("Date", p.day),
                             y: .value("Dollars", fv),
                             series: .value("Series", "Fair value"))
                        .foregroundStyle(Palette.accent)
                        .lineStyle(StrokeStyle(lineWidth: 1.6))
                }
            }
            if let point = selectedPoint {
                RuleMark(x: .value("Date", point.day))
                    .foregroundStyle(Palette.inkFaint.opacity(0.6))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
                if let price = point.price {
                    PointMark(x: .value("Date", point.day),
                              y: .value("Dollars", price))
                        .foregroundStyle(Palette.ink)
                        .symbolSize(60)
                }
                if let fv = fairValue(point) {
                    PointMark(x: .value("Date", point.day),
                              y: .value("Dollars", fv))
                        .foregroundStyle(Palette.accent)
                        .symbolSize(60)
                }
            }
        }
        .chartXSelection(value: $selectedDate)
        .chartYScale(type: logScale ? .log : .linear)
        .chartYAxis {
            AxisMarks(position: .leading) { value in
                AxisGridLine().foregroundStyle(Palette.separator)
                AxisValueLabel {
                    if let v = value.as(Double.self) {
                        Text(Fmt.usd(v, digits: v >= 100 ? 0 : 1))
                            .font(.system(size: 9, design: .monospaced))
                    }
                }
            }
        }
        .chartXAxis {
            AxisMarks(values: .automatic(desiredCount: 4)) { _ in
                AxisGridLine().foregroundStyle(Palette.separator)
                AxisValueLabel(format: .dateTime.year(), centered: false)
            }
        }
        .chartLegend(.hidden)
    }

    private func legend(color: Color, label: String) -> some View {
        HStack(spacing: 5) {
            Rectangle().fill(color).frame(width: 14, height: 2)
            Text(label).font(.caption2).foregroundStyle(Palette.inkMuted)
        }
    }

    // -----------------------------------------------------------------------
    // LA SERIE
    // -----------------------------------------------------------------------

    /// Il punto piu' vicino alla data toccata. Swift Charts restituisce una
    /// data continua; la serie e' settimanale, quindi serve il piu' vicino e
    /// non quello esatto, che non esiste quasi mai.
    private var selectedPoint: HistoryPoint? {
        guard let selectedDate, !series.isEmpty else { return nil }
        return series.min {
            abs($0.day.timeIntervalSince(selectedDate))
                < abs($1.day.timeIntervalSince(selectedDate))
        }
    }

    /// La mediana mobile a tre anni dell'EPS, indicizzata per data.
    ///
    /// SI CALCOLA SUI DEPOSITI DISTINTI, non sulle righe della serie. La serie
    /// e' settimanale e ogni EPS vi compare ripetuto per tutte le settimane in
    /// cui resta valido: farne la mediana significa pesare ogni valore per
    /// QUANTO E' DURATO invece che contarlo una volta. Su Alcoa la differenza
    /// era fra 0,26 e 2,00 dollari — cioe' fra un fair value di 3 dollari e uno
    /// di 24 — e il secondo e' quello che la pipeline scrive nel dataset.
    ///
    /// La dashboard Streamlit ha lo stesso difetto: usa una `rolling("1095D")`
    /// sulla serie settimanale, e il suo grafico normalizzato non torna con il
    /// proprio `eps_normalized_3y`.
    ///
    /// Sulla finestra intera, non su quella visibile: altrimenti i primi punti
    /// mostrati userebbero tre anni che ne contengono tre mesi.
    static func rollingMedian(_ history: [HistoryPoint]) -> [Int: Double] {
        // Un'osservazione per deposito, nell'ordine in cui sono stati fatti.
        var filings: [(date: Int, eps: Double)] = []
        var seen = Set<Int>()
        for point in history {
            guard let eps = point.eps else { continue }
            let key = point.epsDate ?? point.date
            if seen.insert(key).inserted { filings.append((key, eps)) }
        }
        filings.sort { $0.date < $1.date }
        guard !filings.isEmpty else { return [:] }

        var out: [Int: Double] = [:]
        var first = 0
        for point in history {
            // Le date sono interi YYYYMMDD: tre anni indietro sono -30000, che
            // e' esatto perche' il formato e' posizionale.
            let floor = point.date - 30_000
            while first < filings.count, filings[first].date < floor { first += 1 }
            var last = first
            while last < filings.count, filings[last].date <= point.date { last += 1 }
            guard last > first else { continue }

            var window = filings[first..<last].map(\.eps)
            window.sort()
            let m = window.count / 2
            out[point.date] = window.count % 2 == 1
                ? window[m] : (window[m - 1] + window[m]) / 2
        }
        return out
    }

    private var series: [HistoryPoint] {
        guard let last = history.last?.date else { return [] }
        guard let years = window.years else { return history }
        return history.filter { $0.date >= last - years * 10_000 }
    }

    // -----------------------------------------------------------------------
    // IL CONTO DEL FAIR VALUE
    //
    // LA BASE SCELTA DAL LETTORE VINCE SU QUELLA DEL MODELLO. E' la stessa
    // regola di `fair_value_derivation` in app.py: chiedere di vedere tutto
    // normalizzato e poi trovare un fair value costruito sugli utili correnti
    // vorrebbe dire avere sotto gli occhi una linea e un numero che poggiano
    // su due basi diverse.
    // -----------------------------------------------------------------------

    private var usingNormalized: Bool {
        epsBasis == .normalized || (stock.lynchEPSBase ?? "current") == "normalized"
    }

    private var anchorBase: Double? {
        usingNormalized ? stock.epsNormalized3y : stock.epsTTM
    }

    private var derivedFairValue: Double? {
        switch stock.lynchAnchor ?? "earnings" {
        case "book":
            guard let bvps = stock.bookValuePerShare, let pb = stock.lynchFairPB,
                  bvps > 0 else { return nil }
            return bvps * pb
        case "earnings":
            guard let base = anchorBase, base > 0,
                  let fairPE = stock.lynchFairPE else { return nil }
            return base * fairPE
        default:
            return nil
        }
    }

    private var derivedPremium: Double? {
        guard let fv = derivedFairValue, fv > 0,
              let price = stock.currentPrice else { return nil }
        return (price / fv - 1) * 100
    }

    /// Il multiplo in uso: quello scelto, oppure il P/E equo della categoria.
    private var effectivePE: Double? { pe ?? stock.lynchFairPE }

    private func fairValue(_ p: HistoryPoint) -> Double? {
        let base = normalized ? normalizedEPS[p.date] : p.eps
        guard let base, base > 0, let multiple = effectivePE else { return nil }
        return base * multiple
    }

    // -----------------------------------------------------------------------
    // I NUMERI SOTTO IL GRAFICO
    // -----------------------------------------------------------------------

    private var headlineNumbers: some View {
        Panel {
            SectionHeader(title: "Where it trades today")
            KPIGrid(items: [
                ("current_price", stock.currentPrice, nil),
                ("pe_ratio", stock.peRatio, peers["pe_ratio"]),
                ("fair_value_pe15", stock.fairValuePE15, nil),
                ("fair_value_peg", stock.fairValuePEG, nil),
                ("lynch_ratio", stock.lynchRatio, nil),
                ("earnings_yield_pct", stock.earningsYieldPct,
                 peers["earnings_yield_pct"]),
            ])
            Caption(text: "The Lynch ratio is price divided by fair value: "
                    + "below 1 the stock trades at a discount, above 1 at a "
                    + "premium.")
        }
    }

    /// Il conto del fair value di categoria, passaggio per passaggio.
    ///
    /// Un fair value che non si puo' rifare a mano non e' verificabile, ed e'
    /// esattamente il caso in cui un numero assurdo passa inosservato.
    private var derivation: some View {
        Panel {
            SectionHeader(title: "How the category fair value is built")

            let anchor = stock.lynchAnchor ?? "earnings"
            if anchor == "book" {
                MetricRow(label: "Book value per share",
                          value: Fmt.usd(stock.bookValuePerShare))
                MetricRow(label: "× Fair price-to-book",
                          value: Fmt.ratio(stock.lynchFairPB, digits: 2))
            } else if anchor == "earnings" {
                MetricRow(label: usingNormalized ? "3-year normalized EPS" : "EPS (TTM)",
                          value: Fmt.usd(anchorBase))
                MetricRow(label: "× Fair P/E for the category",
                          value: Fmt.ratio(stock.lynchFairPE))
            } else {
                EmptyNote(text: "This category has no valuation anchor: only "
                          + "the flat P/E 15 line remains.")
            }

            Divider().overlay(Palette.separator)
            MetricRow(label: "= Fair value", value: Fmt.usd(derivedFairValue),
                      tone: Palette.ink)
            MetricRow(label: "Current price", value: Fmt.usd(stock.currentPrice))
            HStack {
                Text("Price vs fair value")
                    .font(.subheadline).foregroundStyle(Palette.inkMuted)
                Spacer()
                DeltaText(value: derivedPremium, invert: true)
            }
            if usingNormalized, (stock.lynchEPSBase ?? "current") != "normalized" {
                Caption(text: "You asked to see everything on normalized "
                        + "earnings, so this fair value is rebuilt on them too "
                        + "— the model's own choice for this company was the "
                        + "trailing figure.")
            }

            if let basis = stock.lynchPEBasis { Caption(text: basis) }
            if let note = stock.fairValueNote, note != "ok" { Caption(text: note) }
        }
    }

    /// Quando i due modelli divergono molto, e' un'informazione, non un errore.
    private var modelsCompared: some View {
        Panel {
            SectionHeader(title: "The two models side by side")
            MetricRow(label: "Lynch Chart — flat P/E 15",
                      value: Fmt.usd(stock.fairValuePE15))
            MetricRow(label: "Lynch Fair Value — category anchor",
                      value: Fmt.usd(stock.fairValuePEG))

            if let a = stock.fairValuePE15, let b = stock.fairValuePEG,
               a > 0, b > 0 {
                let divergence = abs(a / b - 1) * 100
                Divider().overlay(Palette.separator)
                MetricRow(label: "Divergence", value: Fmt.pct(divergence),
                          tone: divergence > 35 ? Palette.caution : nil)
                if divergence > 35 {
                    Caption(text: "Above 35% it usually means the stock sits in "
                            + "a category where a flat P/E 15 makes no sense — "
                            + "a fast grower at 30% or a slow grower at 3% — or "
                            + "that earnings contain one-off items. When the "
                            + "two models agree, the signal is robust.")
                }
            }
        }
    }

    /// Utili correnti contro utili normalizzati: e' qui che si spiega perche'
    /// un altro sito puo' dare un fair value diverso per lo stesso titolo.
    @ViewBuilder
    private var normalizedEarnings: some View {
        if stock.epsNormalized3y != nil {
            Panel {
                SectionHeader(title: "Current vs normalized earnings")
                KPIGrid(items: [
                    ("eps_ttm", stock.epsTTM, nil),
                    ("eps_normalized_3y", stock.epsNormalized3y, nil),
                    ("eps_vs_normalized_pct", stock.epsVsNormalizedPct, nil),
                    ("earnings_volatility", stock.earningsVolatility, nil),
                ])
                MetricRow(label: "Fair value on normalized EPS × 15",
                          value: Fmt.usd(stock.fairValueNormPE15))
                Caption(text: "The normalized figure is the three-year median "
                        + "of trailing EPS. Where the two are far apart, the "
                        + "last twelve months are not representative — and the "
                        + "fair value built on them is not either.")
            }
        }
    }
}
