//
//  StockDetailView.swift — La scheda di un titolo.
//
//  L'ORDINE E' UN PERCORSO, lo stesso della dashboard: che azienda e', quanto
//  vale, com'e' fatta, da dove vengono i documenti, quanto fidarsi dei dati.
//  Overview apre perche' un multiplo giudicato senza sapere che mestiere fa
//  l'azienda e' un numero senza contesto.
//
//  SU UN TELEFONO LE SCHEDE SONO UN MENU, non delle linguette. Cinque
//  linguette in 390 punti diventano cinque etichette tagliate a meta'; e
//  soprattutto, per passare dalla prima all'ultima si scorre orizzontalmente
//  una fila che non si vede tutta. Il menu dice sempre dove si e'.
//

import SwiftUI

struct StockDetailView: View {
    let stock: Stock
    /// UN LEGAME, NON UNA COPIA. La base si puo' cambiare da due posti — i
    /// filtri e la scheda Valuation — e devono essere la stessa impostazione:
    /// due stati separati vorrebbero dire un interruttore che dice «corrente»
    /// mentre l'altro dice «normalizzato».
    @Binding var epsBasis: EPSBasis

    /// LA SCHEDA E' SEMPRE ANCORATA ALLA CATEGORIA, qualunque metro si stia
    /// usando nella lista. Il P/E 15 fisso serve a scorrere novecento titoli
    /// con un solo righello; quando se ne apre uno, la domanda cambia — non
    /// piu' «come si colloca fra gli altri» ma «quanto vale QUESTA azienda» —
    /// e a quella risponde l'ancora della sua categoria. Il confronto fra i due
    /// modelli resta, esplicito, dentro la scheda Valuation.
    private let model: ValuationModel = .peg

    @EnvironmentObject private var store: DataStore
    @State private var section: Section = .overview

    enum Section: String, CaseIterable, Identifiable {
        case overview   = "Overview"
        case valuation  = "Valuation"
        case financials = "Financials"
        case filings    = "Filings"
        case quality    = "Data quality"
        var id: String { rawValue }

        var icon: String {
            switch self {
            case .overview:   return "building.columns"
            case .valuation:  return "chart.xyaxis.line"
            case .financials: return "tablecells"
            case .filings:    return "doc.text"
            case .quality:    return "checkmark.seal"
            }
        }
    }

    var body: some View {
        ZStack {
            Palette.background.ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    header
                    sectionPicker

                    switch section {
                    case .overview:   OverviewSection(stock: stock, model: model)
                    case .valuation:  ValuationSection(stock: stock, model: model,
                                                       epsBasis: $epsBasis)
                    case .financials: FinancialsSection(stock: stock)
                    case .filings:    FilingsSection(stock: stock)
                    case .quality:    QualitySection(stock: stock)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 32)
            }
        }
        .navigationTitle(stock.ticker)
        .navigationBarTitleDisplayMode(.inline)
        .onAppear {
            // Compagna di OPEN_TICKER: apre direttamente una delle cinque
            // sezioni. Solo in Debug — vedi la nota in ScreenerView.
            #if DEBUG
            if let wanted = ProcessInfo.processInfo.environment["OPEN_SECTION"],
               let match = Section.allCases.first(where: {
                   $0.rawValue.lowercased().hasPrefix(wanted.lowercased())
               }) {
                section = match
            }
            #endif
        }
    }

    // -----------------------------------------------------------------------

    private var header: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(stock.company)
                .font(.displayMedium)
                .foregroundStyle(Palette.ink)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: 8) {
                Text(stock.ticker)
                    .font(.system(.caption, design: .monospaced).weight(.semibold))
                    .foregroundStyle(Palette.inkMuted)
                if let ex = stock.exchange {
                    Text(ex).font(.caption).foregroundStyle(Palette.inkFaint)
                }
                if let sector = stock.sector {
                    Text("·").foregroundStyle(Palette.inkFaint)
                    Text(sector).font(.caption).foregroundStyle(Palette.inkFaint)
                }
            }

            HStack(alignment: .firstTextBaseline, spacing: 14) {
                // fixedSize: senza, un prezzo a quattro cifre viene mandato a
                // capo dopo la penultima e la scheda si apre con "$308.9" e
                // sotto "1". Il prezzo prende lo spazio che gli serve; e' il
                // resto della riga a cedere.
                Text(Fmt.usd(stock.currentPrice))
                    .font(.system(.title, design: .rounded).weight(.medium).monospacedDigit())
                    .foregroundStyle(Palette.ink)
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)
                VStack(alignment: .leading, spacing: 2) {
                    DeltaText(value: stock.premium(model: model),
                              font: .system(.subheadline, design: .rounded).weight(.semibold).monospacedDigit(),
                              invert: true)
                    Text("vs \(Fmt.usd(stock.fairValue(model: model))) · \(model.rawValue)")
                        .font(.caption2)
                        .foregroundStyle(Palette.inkFaint)
                }
                Spacer()
                ValuationTag(verdict: stock.verdict(model: model))
            }
        }
        .padding(.top, 4)
    }

    private var sectionPicker: some View {
        ChipPicker(options: Section.allCases, selection: $section,
                   label: { $0.rawValue }, icon: { $0.icon })
    }
}
