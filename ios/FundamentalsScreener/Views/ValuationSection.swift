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

    @State private var pe: Double = 15
    @State private var normalized = false
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
            chartPanel
            headlineNumbers
            derivation
            modelsCompared
            normalizedEarnings
        }
        .task(id: stock.ticker) { history = store.history(stock.ticker) }
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
                           label: "Fair value at P/E \(Int(pe))")
                    Spacer()
                }
            }

            Divider().overlay(Palette.separator)

            Picker("Window", selection: $window) {
                ForEach(Window.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)

            Picker("Multiple", selection: $pe) {
                Text("P/E 15").tag(15.0)
                Text("P/E 20").tag(20.0)
                Text("P/E 25").tag(25.0)
            }
            .pickerStyle(.segmented)

            Toggle("Normalized EPS (3-year median)", isOn: $normalized)
                .font(.footnote)
            Toggle("Logarithmic scale", isOn: $logScale)
                .font(.footnote)

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
    private var normalizedEPS: [Int: Double] {
        guard normalized else { return [:] }
        var out: [Int: Double] = [:]
        // Le date sono interi YYYYMMDD: tre anni indietro sono -30000, che e'
        // esatto perche' il formato e' posizionale.
        for (i, point) in history.enumerated() {
            let floor = point.date - 30_000
            var values: [Double] = []
            var j = i
            while j >= 0, history[j].date >= floor {
                if let e = history[j].eps { values.append(e) }
                j -= 1
            }
            guard !values.isEmpty else { continue }
            values.sort()
            let m = values.count / 2
            out[point.date] = values.count % 2 == 1
                ? values[m] : (values[m - 1] + values[m]) / 2
        }
        return out
    }

    private var series: [HistoryPoint] {
        guard let last = history.last?.date else { return [] }
        guard let years = window.years else { return history }
        return history.filter { $0.date >= last - years * 10_000 }
    }

    private func fairValue(_ p: HistoryPoint) -> Double? {
        let base = normalized ? normalizedEPS[p.date] : p.eps
        guard let base, base > 0 else { return nil }
        return base * pe
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
                let useNorm = (stock.lynchEPSBase ?? "current") == "normalized"
                MetricRow(label: useNorm ? "3-year normalized EPS" : "EPS (TTM)",
                          value: Fmt.usd(useNorm ? stock.epsNormalized3y : stock.epsTTM))
                MetricRow(label: "× Fair P/E for the category",
                          value: Fmt.ratio(stock.lynchFairPE))
            } else {
                EmptyNote(text: "This category has no valuation anchor: only "
                          + "the flat P/E 15 line remains.")
            }

            Divider().overlay(Palette.separator)
            MetricRow(label: "= Fair value", value: Fmt.usd(stock.fairValuePEG),
                      tone: Palette.ink)
            MetricRow(label: "Current price", value: Fmt.usd(stock.currentPrice))
            HStack {
                Text("Price vs fair value")
                    .font(.subheadline).foregroundStyle(Palette.inkMuted)
                Spacer()
                DeltaText(value: stock.premiumVsPEG, invert: true)
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
