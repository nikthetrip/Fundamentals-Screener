//
//  ScreenerView.swift — La lista: novecento titoli ordinati per sconto.
//
//  E' LA SCHERMATA CHE SI APRE, quindi la prima domanda a cui risponde deve
//  essere quella per cui si apre l'applicazione: che cosa costa meno di quanto
//  vale. Ordinata per sconto, decrescente, senza doverlo chiedere.
//
//  IL MODELLO SI SCEGLIE IN CIMA, non dentro un menu. I due fair value sono
//  due cose diverse — P/E 15 uguale per tutti, oppure l'ancora della categoria
//  — e la stessa societa' puo' essere a sconto in uno e a premio nell'altro.
//  Nascondere quale dei due si sta guardando renderebbe la lista ambigua
//  proprio nella colonna per cui la si legge.
//

import SwiftUI

struct ScreenerView: View {
    @EnvironmentObject private var store: DataStore

    @State private var model: ValuationModel = .pe15
    @State private var search = ""
    @State private var sort: SortKey = .discount
    @State private var filters = Filters()
    @State private var showFilters = false
    @State private var path = NavigationPath()

    var body: some View {
        NavigationStack(path: $path) {
            ZStack {
                Palette.background.ignoresSafeArea()
                VStack(spacing: 0) {
                    modelPicker
                    Divider().overlay(Palette.separator)
                    freshness
                    list
                }
            }
            .navigationDestination(for: Stock.self) { stock in
                StockDetailView(stock: stock, model: model)
            }
            .navigationTitle("Screener")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) { sortMenu }
                ToolbarItem(placement: .topBarTrailing) { filterButton }
            }
            .searchable(text: $search, placement: .navigationBarDrawer(displayMode: .always),
                        prompt: "Ticker or company name")
            .sheet(isPresented: $showFilters) {
                FilterSheet(filters: $filters, universe: store.stocks)
            }
            .refreshable { await store.refresh(force: true) }
            .onAppear(perform: openTickerFromLaunchArguments)
        }
    }

    // -----------------------------------------------------------------------

    private var modelPicker: some View {
        VStack(spacing: 6) {
            Picker("Model", selection: $model) {
                ForEach(ValuationModel.allCases) { m in
                    Text(m.rawValue).tag(m)
                }
            }
            .pickerStyle(.segmented)

            Text(model.subtitle)
                .font(.caption2)
                .foregroundStyle(Palette.inkFaint)
        }
        .padding(.horizontal, 16)
        .padding(.top, 8)
        .padding(.bottom, 10)
        .background(Palette.surface)
    }

    /// Quanti titoli si stanno guardando e di quando sono i dati.
    ///
    /// STA SOTTO IL SELETTORE, non nella barra di navigazione: li' iOS gli
    /// dava una sessantina di punti e la data usciva come "dati in…". Un
    /// riferimento temporale troncato e' peggio di nessun riferimento — fa
    /// credere che ci sia scritto qualcosa di leggibile.
    private var freshness: some View {
        HStack(spacing: 6) {
            Text("\(rows.count) stocks")
                .font(.caption2.weight(.medium))
                .foregroundStyle(Palette.inkMuted)
            Text("·").font(.caption2).foregroundStyle(Palette.inkFaint)
            if store.isOffline {
                Text("cached data, the network did not answer")
                    .font(.caption2)
                    .foregroundStyle(Palette.caution)
            } else if let generated = store.dataGeneratedAt {
                Text("updated \(Fmt.date(generated))")
                    .font(.caption2)
                    .foregroundStyle(Palette.inkFaint)
            }
            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 6)
        .background(Palette.background)
    }

    private var sortMenu: some View {
        Menu {
            Picker("Sort by", selection: $sort) {
                ForEach(SortKey.allCases) { key in
                    Text(key.label).tag(key)
                }
            }
        } label: {
            Image(systemName: "arrow.up.arrow.down")
        }
    }

    private var filterButton: some View {
        Button { showFilters = true } label: {
            Image(systemName: filters.isActive
                  ? "line.3.horizontal.decrease.circle.fill"
                  : "line.3.horizontal.decrease.circle")
        }
    }

    @ViewBuilder
    private var list: some View {
        if rows.isEmpty {
            VStack(spacing: 10) {
                Spacer()
                Text("No stocks match these filters")
                    .font(.subheadline)
                    .foregroundStyle(Palette.inkMuted)
                if filters.isActive {
                    Button("Clear filters") { filters = Filters() }
                        .font(.footnote)
                }
                Spacer()
            }
            .frame(maxWidth: .infinity)
        } else {
            List(rows) { stock in
                NavigationLink(value: stock) {
                    ScreenerRow(stock: stock, model: model)
                }
                .listRowBackground(Palette.background)
                .listRowSeparatorTint(Palette.separator)
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
        }
    }

    /// Apre direttamente la scheda di un titolo se l'app e' stata avviata con
    /// la variabile `OPEN_TICKER`.
    ///
    /// SERVE A VEDERE UNA SCHERMATA SENZA POTERLA TOCCARE. Su questa macchina
    /// il pannello del simulatore e l'automazione dell'interfaccia non sono
    /// disponibili, e una scheda che nessuno ha mai aperto e' una scheda che
    /// nessuno ha verificato: compilare non e' funzionare. Solo in Debug, cosi'
    /// non esiste nella versione che finisce sul telefono.
    private func openTickerFromLaunchArguments() {
        #if DEBUG
        guard path.isEmpty,
              let symbol = ProcessInfo.processInfo.environment["OPEN_TICKER"],
              let stock = store.stocks.first(where: { $0.ticker == symbol })
        else { return }
        path.append(stock)
        #endif
    }

    // -----------------------------------------------------------------------
    // SELEZIONE E ORDINAMENTO
    // -----------------------------------------------------------------------

    private var rows: [Stock] {
        var out = store.stocks.filter { filters.accepts($0, model: model) }

        let q = search.trimmingCharacters(in: .whitespaces).uppercased()
        if !q.isEmpty {
            out = out.filter {
                $0.ticker.hasPrefix(q) || $0.company.uppercased().contains(q)
            }
        }

        // I titoli senza il numero su cui si ordina finiscono in fondo, non in
        // cima: un valore mancante non e' uno sconto del 100%.
        return out.sorted { a, b in
            switch sort {
            case .discount:
                // ATTENZIONE AL SEGNO. La colonna del dataset e' il PREZZO
                // rispetto al fair value: +85% vuol dire che il titolo costa
                // l'85% in piu' di quanto vale, non che e' a sconto dell'85%.
                // Quindi si ordina crescente — prima i piu' negativi, che sono
                // i piu' a buon mercato. Ordinandola decrescente la schermata
                // di apertura proponeva per primi i titoli piu' cari del
                // listino, con il numero scritto in verde.
                return sortAsc(a.premium(model: model), b.premium(model: model))
            case .marketCap:
                return sortDesc(a.marketCap, b.marketCap)
            case .pe:
                return sortAsc(a.peRatio, b.peRatio)
            case .growth:
                return sortDesc(a.growth5yCAGR, b.growth5yCAGR)
            case .roe:
                return sortDesc(a.roePct, b.roePct)
            case .dividend:
                return sortDesc(a.dividendTTMYieldPct ?? a.dividendYield, b.dividendTTMYieldPct ?? b.dividendYield)
            case .ticker:
                return a.ticker < b.ticker
            }
        }
    }

    private func sortDesc(_ a: Double?, _ b: Double?) -> Bool {
        switch (a, b) {
        case let (x?, y?): return x > y
        case (nil, _?):    return false
        case (_?, nil):    return true
        default:           return false
        }
    }

    private func sortAsc(_ a: Double?, _ b: Double?) -> Bool {
        switch (a, b) {
        case let (x?, y?): return x < y
        case (nil, _?):    return false
        case (_?, nil):    return true
        default:           return false
        }
    }

    enum SortKey: String, CaseIterable, Identifiable {
        case discount, marketCap, pe, growth, roe, dividend, ticker
        var id: String { rawValue }

        var label: String {
            switch self {
            case .discount:  return "Price vs fair value"
            case .marketCap: return "Market cap"
            case .pe:        return "P/E, lowest first"
            case .growth:    return "5-year growth"
            case .roe:       return "ROE"
            case .dividend:  return "Dividend"
            case .ticker:    return "Ticker"
            }
        }
    }
}

// ---------------------------------------------------------------------------
// LA RIGA
//
// Tre livelli di lettura: il ticker per trovarlo, lo sconto per giudicarlo, il
// resto per capire perche'. In quest'ordine di peso visivo, cosi' che
// scorrendo si legga la sola colonna che conta.
// ---------------------------------------------------------------------------
struct ScreenerRow: View {
    let stock: Stock
    let model: ValuationModel

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(stock.ticker)
                        .font(.system(.subheadline, design: .monospaced).weight(.semibold))
                        .foregroundStyle(Palette.ink)
                    ValuationTag(verdict: stock.verdict(model: model))
                }
                Text(stock.company)
                    .font(.caption)
                    .foregroundStyle(Palette.inkMuted)
                    .lineLimit(1)
                CategoryTag(category: stock.lynchCategory, compact: true)
            }

            Spacer(minLength: 4)

            VStack(alignment: .trailing, spacing: 3) {
                DeltaText(value: stock.premium(model: model),
                          font: .system(.subheadline, design: .rounded).weight(.semibold).monospacedDigit(),
                          invert: true)
                Text("\(Fmt.usd(stock.currentPrice)) / \(Fmt.usd(stock.fairValue(model: model)))")
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(Palette.inkFaint)
                Text(Fmt.money(stock.marketCap, digits: 1))
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(Palette.inkFaint)
            }
        }
        .padding(.vertical, 5)
    }
}
