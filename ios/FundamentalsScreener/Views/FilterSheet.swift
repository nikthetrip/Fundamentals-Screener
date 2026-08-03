//
//  FilterSheet.swift — I filtri dello screener.
//
//  LE OPZIONI VENGONO DAI DATI, non da un elenco scritto a mano. Le industrie
//  sono centinaia e cambiano con l'universo scelto nella pipeline: un elenco
//  fisso nel codice comincerebbe a offrire categorie vuote e a nascondere
//  quelle nuove il giorno stesso in cui la Action gira su un universo diverso.
//
//  E LE INDUSTRIE SEGUONO I SETTORI: scelto "Technology", il filtro per
//  industria mostra solo le industrie del comparto tecnologico. Altrimenti si
//  scorre una lista di trecento voci per trovarne dodici utili.
//

import SwiftUI

struct Filters {
    var sectors: Set<String> = []
    var industries: Set<String> = []
    var categories: Set<String> = []
    var verdicts: Set<String> = []
    var profitableOnly = false
    var payingDividend = false
    var minMarketCap: Double = 0          // in miliardi
    var minConfidence: String? = nil      // "high" | "medium" | nil

    var isActive: Bool {
        !sectors.isEmpty || !industries.isEmpty || !categories.isEmpty
            || !verdicts.isEmpty || profitableOnly || payingDividend
            || minMarketCap > 0 || minConfidence != nil
    }

    func accepts(_ s: Stock, model: ValuationModel) -> Bool {
        if !sectors.isEmpty, !sectors.contains(s.sector ?? "") { return false }
        if !industries.isEmpty, !industries.contains(s.industry ?? "") { return false }
        if !categories.isEmpty, !categories.contains(s.lynchCategory ?? "") { return false }
        if !verdicts.isEmpty, !verdicts.contains(s.verdict(model: model) ?? "") { return false }
        if profitableOnly, !((s.epsTTM ?? -1) > 0) { return false }
        if payingDividend, !((s.dividendTTMYieldPct ?? s.dividendYield ?? 0) > 0) { return false }
        if minMarketCap > 0, (s.marketCap ?? 0) < minMarketCap * 1e9 { return false }
        if let minConfidence {
            let rank = ["low": 0, "medium": 1, "high": 2]
            let need = rank[minConfidence] ?? 0
            let have = rank[(s.lynchConfidence ?? "").lowercased()] ?? -1
            if have < need { return false }
        }
        return true
    }
}

struct FilterSheet: View {
    @Binding var filters: Filters
    let universe: [Stock]
    @Environment(\.dismiss) private var dismiss

    private var sectors: [String] {
        Set(universe.compactMap(\.sector)).sorted()
    }

    private var industries: [String] {
        let pool = filters.sectors.isEmpty
            ? universe
            : universe.filter { filters.sectors.contains($0.sector ?? "") }
        return Set(pool.compactMap(\.industry)).sorted()
    }

    private var categories: [String] {
        Set(universe.compactMap(\.lynchCategory)).sorted()
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Toggle("Profitable only", isOn: $filters.profitableOnly)
                    Toggle("Pays a dividend", isOn: $filters.payingDividend)
                } footer: {
                    Text("«Profitable» looks at trailing EPS. A loss-making company has no P/E, so its fair value rests on normalized earnings — or on nothing at all.")
                }

                Section("Minimum market cap") {
                    Picker("At least", selection: $filters.minMarketCap) {
                        Text("No minimum").tag(0.0)
                        Text("$1 billion").tag(1.0)
                        Text("$10 billion").tag(10.0)
                        Text("$50 billion").tag(50.0)
                        Text("$200 billion").tag(200.0)
                    }
                }

                Section("Classification confidence") {
                    Picker("At least", selection: Binding(
                        get: { filters.minConfidence ?? "" },
                        set: { filters.minConfidence = $0.isEmpty ? nil : $0 })) {
                        Text("Any").tag("")
                        Text("Medium or high").tag("medium")
                        Text("High only").tag("high")
                    }
                }

                Section("Verdict") {
                    multiToggle(options: ["Undervalued", "Fair", "Overvalued", "N/A"],
                                selection: $filters.verdicts,
                                label: { verdictLabel($0) })
                }

                Section("Lynch category") {
                    multiToggle(options: categories, selection: $filters.categories,
                                label: { $0 })
                }

                Section("Sector") {
                    multiToggle(options: sectors, selection: $filters.sectors,
                                label: { $0 })
                }

                if !industries.isEmpty {
                    Section("Industry") {
                        multiToggle(options: industries,
                                    selection: $filters.industries, label: { $0 })
                    }
                }
            }
            .navigationTitle("Filters")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Reset") { filters = Filters() }
                        .disabled(!filters.isActive)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }.fontWeight(.semibold)
                }
            }
            // Cambiando settore, un'industria selezionata che non gli
            // appartiene piu' resterebbe attiva e invisibile: il risultato e'
            // una lista vuota senza un filtro visibile che la spieghi.
            .onChange(of: filters.sectors) { _, _ in
                filters.industries.formIntersection(Set(industries))
            }
        }
        .presentationDetents([.medium, .large])
    }

    private func verdictLabel(_ raw: String) -> String {
        switch raw {
        case "Undervalued": return "Undervalued"
        case "Overvalued":  return "Overvalued"
        case "Fair":        return "Fairly valued"
        default:            return "Not valuable"
        }
    }

    @ViewBuilder
    private func multiToggle(options: [String],
                             selection: Binding<Set<String>>,
                             label: @escaping (String) -> String) -> some View {
        ForEach(options, id: \.self) { option in
            Button {
                if selection.wrappedValue.contains(option) {
                    selection.wrappedValue.remove(option)
                } else {
                    selection.wrappedValue.insert(option)
                }
            } label: {
                HStack {
                    Text(label(option))
                        .foregroundStyle(Palette.ink)
                    Spacer()
                    if selection.wrappedValue.contains(option) {
                        Image(systemName: "checkmark")
                            .foregroundStyle(Palette.accent)
                            .font(.footnote.weight(.semibold))
                    }
                }
            }
        }
    }
}
