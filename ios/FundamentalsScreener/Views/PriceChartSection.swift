//
//  PriceChartSection.swift — Il grafico del prezzo: candele e volumi.
//
//  DUE GRAFICI, DUE DOMANDE DIVERSE. Questo risponde a «come si e' mosso il
//  titolo»: candele, volumi, la lettura che si fa su una piattaforma di
//  mercato. Il grafico di VALUTAZIONE — quello con la earnings line — risponde
//  a «quanto vale rispetto a quello che guadagna», ed e' un'altra cosa. Tenerli
//  separati e' la stessa scelta fatta in price_chart.py.
//
//  IL VOLUME STA SOTTO, non sovrapposto. Su un asse solo le barre del volume
//  vanno schiacciate a un decimo per non coprire le candele, e a quel punto non
//  se ne legge piu' la variazione — che e' l'unica cosa che il volume dice.
//

import SwiftUI
import Charts

struct PriceChartSection: View {
    let ticker: String

    @State private var range: PriceHistory.Range = .oneYear
    @State private var candles: [Candle] = []
    @State private var loading = false
    @State private var failure: String?
    @State private var selected: Date?
    @State private var showSMA50 = false
    @State private var showSMA200 = false

    var body: some View {
        Panel {
            // Il sottotitolo dice la risoluzione vera. Lasciato fisso a «daily»
            // mentiva su quattro finestre su cinque.
            SectionHeader(title: "Price", note: resolutionNote)

            if loading && candles.isEmpty {
                HStack { Spacer(); ProgressView(); Spacer() }
                    .frame(height: 200)
            } else if let failure, candles.isEmpty {
                EmptyNote(text: failure + " The valuation chart above does not "
                          + "depend on it: that one comes from the dataset.")
                Button("Try again") { Task { await load() } }
                    .font(.footnote)
            } else if !candles.isEmpty {
                readout
                candleChart.frame(height: 210)
                volumeChart.frame(height: 54)
            }

            Divider().overlay(Palette.separator)
            Picker("Range", selection: $range) {
                ForEach(PriceHistory.Range.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)

            // LE MEDIE SONO IN PERIODI, NON IN GIORNI, e vale la pena che si
            // veda: su una finestra a candele orarie una SMA 50 sono cinquanta
            // ore, non cinquanta sedute. Chiamarla «SMA 50 giorni» su un
            // grafico orario sarebbe semplicemente falso.
            HStack(spacing: 8) {
                smaToggle("SMA 50", isOn: $showSMA50, color: Palette.accent)
                smaToggle("SMA 200", isOn: $showSMA200, color: Palette.caution)
                Spacer()
                Text("\(range.interval) bars")
                    .font(.system(size: 9, design: .monospaced))
                    .foregroundStyle(Palette.inkFaint)
            }
        }
        .task(id: ticker) { await load() }
        .onChange(of: range) { _, _ in Task { await load() } }
    }

    /// Un interruttore che sembra un'etichetta: acceso porta il colore della
    /// linea che accende, cosi' non serve una legenda separata.
    private func smaToggle(_ label: String, isOn: Binding<Bool>,
                           color: Color) -> some View {
        Button { isOn.wrappedValue.toggle() } label: {
            HStack(spacing: 5) {
                Rectangle().fill(isOn.wrappedValue ? color : Palette.inkFaint.opacity(0.4))
                    .frame(width: 12, height: 2)
                Text(label)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(isOn.wrappedValue ? Palette.ink : Palette.inkMuted)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(isOn.wrappedValue ? color.opacity(0.14) : Palette.panel)
            .clipShape(Capsule())
        }
        .buttonStyle(.plain)
    }

    // -----------------------------------------------------------------------
    // LE MEDIE MOBILI
    //
    // Si calcolano su TUTTE le candele scaricate, comprese quelle di rincorsa
    // che non si vedono, e poi si ritagliano insieme al resto. E' il motivo per
    // cui la linea entra da sinistra invece di cominciare a due terzi del
    // grafico.
    // -----------------------------------------------------------------------

    private func sma(_ period: Int) -> [(date: Date, value: Double)] {
        guard candles.count >= period else { return [] }
        var out: [(Date, Double)] = []
        var running = 0.0
        for (i, candle) in candles.enumerated() {
            running += candle.close
            if i >= period { running -= candles[i - period].close }
            if i >= period - 1 {
                out.append((candle.date, running / Double(period)))
            }
        }
        let from = range.visibleFrom
        return out.filter { from == nil || $0.0 >= from! }
    }

    /// Le candele mostrate: la rincorsa serve alle medie, non all'occhio.
    private var visible: [Candle] {
        guard let from = range.visibleFrom else { return candles }
        return candles.filter { $0.date >= from }
    }

    private func load() async {
        loading = true
        failure = nil
        do {
            candles = try await PriceHistory.fetch(ticker, range: range)
        } catch {
            candles = []
            failure = error.localizedDescription
        }
        loading = false
    }

    // -----------------------------------------------------------------------

    private var resolutionNote: String {
        switch range {
        case .oneMonth:  return "Hourly candles and volume"
        case .sixMonths: return "Four-hour candles and volume"
        case .oneYear:   return "Daily candles and volume"
        case .fiveYears: return "Weekly candles and volume"
        case .max:       return "Weekly candles — up to 30 years"
        }
    }

    private var shown: Candle? {
        guard let selected, !visible.isEmpty else { return visible.last }
        return visible.min {
            abs($0.date.timeIntervalSince(selected)) < abs($1.date.timeIntervalSince(selected))
        }
    }

    private var readout: some View {
        let c = shown
        let change = c.map { ($0.close / $0.open - 1) * 100 }
        // DUE RIGHE, NON UNA. Cinque valori affiancati su 390 punti mandavano
        // a capo i prezzi a tre cifre in mezzo al numero — "$304.8" e sotto
        // "1" — che e' peggio di non mostrarli. La chiusura sta sopra da sola,
        // apertura/massimo/minimo sotto, piu' piccoli.
        return VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(c.map { $0.date.formatted(.dateTime.day().month(.abbreviated).year()) }
                     ?? Fmt.dash)
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(Palette.inkFaint)
                Text(Fmt.usd(c?.close))
                    .font(.numberBody.weight(.semibold))
                    .foregroundStyle(Palette.ink)
                DeltaText(value: change, font: .numberSmall.weight(.semibold))
                Spacer()
            }
            .lineLimit(1)

            HStack(spacing: 14) {
                column("open", Fmt.usd(c?.open))
                column("high", Fmt.usd(c?.high))
                column("low", Fmt.usd(c?.low))
                column("volume", Fmt.money(c?.volume, digits: 1)
                    .replacingOccurrences(of: "$", with: ""))
                Spacer()
            }
        }
    }

    private func column(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(value)
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(Palette.inkMuted)
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)
            Text(label).font(.system(size: 8)).foregroundStyle(Palette.inkFaint)
        }
    }

    // -----------------------------------------------------------------------

    private var candleChart: some View {
        Chart {
            ForEach(visible) { candle in
                // Lo stoppino: dal minimo al massimo della seduta.
                RuleMark(x: .value("Date", candle.date),
                         yStart: .value("Low", candle.low),
                         yEnd: .value("High", candle.high))
                    .foregroundStyle(candle.isUp ? Palette.positive : Palette.negative)
                    .lineStyle(StrokeStyle(lineWidth: 1))
                // Il corpo: da apertura a chiusura. Con un solo punto di
                // differenza resterebbe invisibile, quindi ha un'altezza
                // minima — una seduta piatta e' comunque una seduta.
                RectangleMark(x: .value("Date", candle.date),
                              yStart: .value("Open", min(candle.open, candle.close)),
                              yEnd: .value("Close", max(candle.open, candle.close)),
                              width: .fixed(bodyWidth))
                    .foregroundStyle(candle.isUp ? Palette.positive : Palette.negative)
            }
            if showSMA50 {
                ForEach(sma(50), id: \.date) { point in
                    LineMark(x: .value("Date", point.date),
                             y: .value("SMA 50", point.value),
                             series: .value("Series", "SMA 50"))
                        .foregroundStyle(Palette.accent)
                        .lineStyle(StrokeStyle(lineWidth: 1.3))
                }
            }
            if showSMA200 {
                ForEach(sma(200), id: \.date) { point in
                    LineMark(x: .value("Date", point.date),
                             y: .value("SMA 200", point.value),
                             series: .value("Series", "SMA 200"))
                        .foregroundStyle(Palette.caution)
                        .lineStyle(StrokeStyle(lineWidth: 1.3))
                }
            }
            if let shown, selected != nil {
                RuleMark(x: .value("Date", shown.date))
                    .foregroundStyle(Palette.inkFaint.opacity(0.5))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
            }
        }
        .chartXSelection(value: $selected)
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
                AxisValueLabel(format: axisFormat, centered: false)
            }
        }
    }

    private var volumeChart: some View {
        Chart {
            ForEach(visible) { candle in
                BarMark(x: .value("Date", candle.date),
                        y: .value("Volume", candle.volume),
                        width: .fixed(bodyWidth))
                    .foregroundStyle((candle.isUp ? Palette.positive : Palette.negative)
                        .opacity(0.45))
            }
        }
        .chartYAxis {
            AxisMarks(position: .leading, values: .automatic(desiredCount: 2)) { value in
                AxisValueLabel {
                    if let v = value.as(Double.self) {
                        Text(Fmt.money(v, digits: 0).replacingOccurrences(of: "$", with: ""))
                            .font(.system(size: 8, design: .monospaced))
                    }
                }
            }
        }
        .chartXAxis(.hidden)
    }

    /// Larghezza del corpo delle candele: si stringe quando ce ne sono tante,
    /// perche' su un anno di sedute il corpo pieno diventa una campitura unica.
    private var bodyWidth: CGFloat {
        switch visible.count {
        case ..<40:   return 7
        case ..<90:   return 4
        case ..<200:  return 2.5
        default:      return 1.5
        }
    }

    private var axisFormat: Date.FormatStyle {
        range == .oneMonth ? .dateTime.day().month(.abbreviated)
                           : .dateTime.month(.abbreviated).year(.twoDigits)
    }
}
