//
//  FilingsSection.swift — I documenti originali e le operazioni sul capitale.
//
//  ESISTE PER CHIUDERE IL CERCHIO. Tutto quello che l'applicazione mostra e'
//  ricavato da questi documenti; senza un modo di arrivarci, ogni numero
//  resta da prendere per buono. Il collegamento va al deposito sul sito della
//  SEC, non a una copia: cosi' quello che si apre e' la fonte.
//
//  GLI EVENTI SONO LI' PER SPIEGARE I SALTI. Un EPS che dimezza da un
//  trimestre all'altro puo' essere un crollo degli utili o un frazionamento
//  azionario, e sono due letture opposte dello stesso grafico.
//

import SwiftUI

struct FilingsSection: View {
    let stock: Stock
    @EnvironmentObject private var store: DataStore

    @State private var filings: [Filing] = []
    @State private var events: [CorporateEvent] = []
    @State private var formFilter: String? = nil

    private var forms: [String] {
        Array(Set(filings.map(\.form))).sorted()
    }

    private var shown: [Filing] {
        guard let formFilter else { return filings }
        return filings.filter { $0.form == formFilter }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {

            Panel {
                SectionHeader(title: "SEC filings",
                              note: stock.cik.map { "CIK \($0)" })

                if filings.isEmpty {
                    EmptyNote(text: "No filings on record for this ticker.")
                } else {
                    if forms.count > 1 {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(spacing: 6) {
                                chip(label: "All", active: formFilter == nil) {
                                    formFilter = nil
                                }
                                ForEach(forms, id: \.self) { form in
                                    chip(label: form, active: formFilter == form) {
                                        formFilter = formFilter == form ? nil : form
                                    }
                                }
                            }
                        }
                    }

                    ForEach(shown.prefix(40)) { filing in
                        Link(destination: URL(string: filing.url)
                             ?? URL(string: "https://www.sec.gov")!) {
                            HStack(alignment: .firstTextBaseline, spacing: 10) {
                                Text(filing.form)
                                    .font(.system(.caption, design: .monospaced)
                                        .weight(.semibold))
                                    .foregroundStyle(Palette.ink)
                                    .frame(width: 56, alignment: .leading)
                                VStack(alignment: .leading, spacing: 1) {
                                    Text("Filed \(Fmt.date(filing.filingDate))")
                                        .font(.footnote)
                                        .foregroundStyle(Palette.inkMuted)
                                    if let period = filing.periodDate {
                                        Text("Period ended \(Fmt.date(period))")
                                            .font(.caption2)
                                            .foregroundStyle(Palette.inkFaint)
                                    }
                                }
                                Spacer()
                                Image(systemName: "arrow.up.right")
                                    .font(.caption2)
                                    .foregroundStyle(Palette.accent)
                            }
                            .padding(.vertical, 5)
                        }
                        Divider().overlay(Palette.separator.opacity(0.5))
                    }

                    if shown.count > 40 {
                        Caption(text: "Showing the 40 most recent filings of \(shown.count).")
                    }
                }
            }

            Panel {
                SectionHeader(title: "Corporate actions",
                              note: "Splits, buybacks, dilution")
                if events.isEmpty {
                    EmptyNote(text: "No corporate actions on record.")
                } else {
                    ForEach(events.prefix(30)) { event in
                        HStack(alignment: .firstTextBaseline, spacing: 10) {
                            Text(Fmt.date(event.date))
                                .font(.system(.caption, design: .monospaced))
                                .foregroundStyle(Palette.inkFaint)
                                .frame(width: 92, alignment: .leading)
                            Text(label(for: event.type))
                                .font(.caption.weight(.medium))
                                .foregroundStyle(tone(for: event.type))
                                .frame(width: 92, alignment: .leading)
                            Text(event.detail)
                                .font(.caption)
                                .foregroundStyle(Palette.inkMuted)
                            Spacer()
                        }
                        .padding(.vertical, 4)
                        Divider().overlay(Palette.separator.opacity(0.5))
                    }
                    Caption(text: "A split changes EPS without earnings having changed: that is where the sudden jumps in the earnings line come from.")
                }
            }
        }
        .task(id: stock.ticker) {
            filings = store.filings(stock.ticker)
            events = store.events(stock.ticker)
        }
    }

    private func chip(label: String, active: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label)
                .font(.system(size: 11, weight: .medium, design: .monospaced))
                .padding(.horizontal, 9)
                .padding(.vertical, 5)
                .background(active ? Palette.ink : Palette.panel)
                .foregroundStyle(active ? Palette.background : Palette.inkMuted)
                .clipShape(Capsule())
        }
        .buttonStyle(.plain)
    }

    private func label(for type: String) -> String {
        switch type.lowercased() {
        case "split":    return "Split"
        case "buyback":  return "Buyback"
        case "dilution": return "Dilution"
        default:         return type.capitalized
        }
    }

    private func tone(for type: String) -> Color {
        switch type.lowercased() {
        case "buyback":  return Palette.positive
        case "dilution": return Palette.negative
        default:         return Palette.accent
        }
    }
}
