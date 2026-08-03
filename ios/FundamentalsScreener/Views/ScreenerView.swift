//
//  ScreenerView.swift — La schermata che si apre.
//
//  DUE ERRORI GIA' FATTI, tenuti scritti qui perche' sono facili da rifare.
//
//  1. LE SCHEDE SENZA ETICHETTE. La prima versione scriveva `$84.45 / $338.10`
//     e nessuno poteva sapere quale fosse il prezzo e quale il fair value.
//  2. LA TABELLA A SEDICI COLONNE. La seconda le intestava, ma su 390 punti si
//     leggeva scorrendo avanti e indietro una griglia di numeri: corretta e
//     inutilizzabile. Una tabella e' lo strumento giusto su uno schermo largo,
//     dove si vedono tutte le colonne insieme; qui non se ne vedono tre.
//
//  QUINDI: righe, non celle. Ogni titolo occupa due righe con l'informazione
//  gerarchizzata — chi e', come e' giudicato, quanto e' lo scarto, da quale
//  prezzo a quale fair value — e la freccia dice da sola cosa sono i due
//  numeri. Le altre dodici colonne vivono nella scheda, che e' fatta per
//  quello.
//
//  E IL SELETTORE DEL MODELLO NON C'E' PIU' IN CIMA. Due linguette larghe mezzo
//  schermo facevano sembrare che ci fossero due liste di titoli diverse. Sono
//  due METRI sugli stessi novecento titoli: la scelta di un'unita' di misura
//  sta accanto all'ordinamento, non al posto d'onore. Il metro predefinito e'
//  l'ancora della categoria, l'unico che tiene conto di che azienda sia.
//

import SwiftUI

struct ScreenerView: View {
    @EnvironmentObject private var store: DataStore

    @State private var model: ValuationModel = .peg
    /// La base degli utili vale per tutta l'applicazione, non per un grafico:
    /// se si sceglie di ragionare su utili normalizzati, la scheda di ogni
    /// titolo deve seguirla.
    @State private var epsBasis: EPSBasis = .current
    @State private var search = ""
    @State private var sort: SortKey = .discount
    @State private var filters = Filters()
    @State private var showFilters = false
    @State private var showModelHelp = false
    @State private var path = NavigationPath()

    var body: some View {
        NavigationStack(path: $path) {
            ZStack {
                Palette.background.ignoresSafeArea()
                VStack(spacing: 0) {
                    summaryStrip
                    Divider().overlay(Palette.separator)
                    list
                }
            }
            .navigationDestination(for: Stock.self) {
                StockDetailView(stock: $0, epsBasis: $epsBasis)
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
                FilterSheet(filters: $filters, model: $model,
                            epsBasis: $epsBasis, universe: store.stocks)
            }
            .sheet(isPresented: $showModelHelp) { ModelExplanation() }
            .refreshable { await store.refresh(force: true) }
            .onAppear(perform: openTickerFromLaunchArguments)
        }
    }

    // -----------------------------------------------------------------------
    // LA STRISCIA IN CIMA
    //
    // Quanti titoli, quanti sotto il fair value, con quale metro e di quando
    // sono i dati. Quattro fatti su una riga e mezza: e' quello che serve
    // sapere prima di cominciare a scorrere.
    // -----------------------------------------------------------------------

    private var summaryStrip: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Text("\(rows.count)")
                    .font(.system(.subheadline, design: .rounded).weight(.semibold))
                    .foregroundStyle(Palette.ink)
                Text(rows.count == 1 ? "stock" : "stocks")
                    .font(.caption).foregroundStyle(Palette.inkMuted)
                if undervaluedCount > 0 {
                    Text("·").font(.caption).foregroundStyle(Palette.inkFaint)
                    Text("\(undervaluedCount) below fair value")
                        .font(.caption).foregroundStyle(Palette.positive)
                }
                Spacer()
                if store.isOffline {
                    Text("cached").font(.caption2).foregroundStyle(Palette.caution)
                } else if let generated = store.dataGeneratedAt {
                    Text(Fmt.date(generated))
                        .font(.caption2).foregroundStyle(Palette.inkFaint)
                }
            }

            Button { showModelHelp = true } label: {
                HStack(spacing: 4) {
                    Text("Measured against \(model.shortName)")
                        .font(.caption2)
                        .foregroundStyle(Palette.inkFaint)
                    Image(systemName: "questionmark.circle")
                        .font(.system(size: 10))
                        .foregroundStyle(Palette.accent)
                }
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 9)
        .background(Palette.surface)
    }

    private var undervaluedCount: Int {
        rows.filter { ($0.premium(model: model) ?? 1) < 0 }.count
    }

    // -----------------------------------------------------------------------

    private var sortMenu: some View {
        Menu {
            Picker("Sort by", selection: $sort) {
                ForEach(SortKey.allCases) { Text($0.label).tag($0) }
            }
            Divider()
            // IL MODELLO STA QUI. E' un'unita' di misura, esattamente come la
            // chiave di ordinamento: appartiene allo stesso menu, non a un
            // selettore che occupa mezzo schermo e sembra un cambio di
            // contenuto.
            Picker("Measure against", selection: $model) {
                ForEach(ValuationModel.allCases) { Text($0.shortName).tag($0) }
            }
            Divider()
            Button { showModelHelp = true } label: {
                Label("What is the difference?", systemImage: "questionmark.circle")
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

    // -----------------------------------------------------------------------
    // LA LISTA, DIVISA IN FASCE
    //
    // Novecento righe di seguito sono un rotolo: si scorre senza sapere se si
    // e' a un terzo o alla fine, e senza capire se il titolo che si sta
    // guardando sia un'occasione o uno dei tanti in mezzo. Le fasce danno una
    // struttura allo scorrimento e rispondono da sole alla domanda «quanti ce
    // ne sono davvero a forte sconto».
    // -----------------------------------------------------------------------

    @ViewBuilder
    private var list: some View {
        if rows.isEmpty {
            VStack(spacing: 10) {
                Spacer()
                Text("No stocks match these filters")
                    .font(.subheadline).foregroundStyle(Palette.inkMuted)
                if filters.isActive {
                    Button("Clear filters") { filters = Filters() }.font(.footnote)
                }
                Spacer()
            }
            .frame(maxWidth: .infinity)
        } else {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0,
                           pinnedViews: [.sectionHeaders]) {
                    ForEach(orderedBands, id: \.self) { band in
                        Section {
                            ForEach(grouped[band] ?? []) { stock in
                                Button { path.append(stock) } label: {
                                    ScreenerRow(stock: stock, model: model)
                                }
                                .buttonStyle(.plain)
                                Divider().overlay(Palette.separator.opacity(0.5))
                                    .padding(.leading, 16)
                            }
                        } header: {
                            bandHeader(band, count: (grouped[band] ?? []).count)
                        }
                    }
                }
            }
        }
    }

    /// Le fasce presenti, nell'ordine. Quando si ordina per qualcosa che non e'
    /// lo scarto — la capitalizzazione, il dividendo — raggrupparle non ha piu'
    /// senso: la lista viene mostrata di seguito, in un'unica sezione.
    private var orderedBands: [Band] {
        guard sort == .discount else { return [.all] }
        return Band.allCases.filter { !(grouped[$0] ?? []).isEmpty }
    }

    private func bandHeader(_ band: Band, count: Int) -> some View {
        HStack(spacing: 8) {
            RoundedRectangle(cornerRadius: 1)
                .fill(band.color).frame(width: 3, height: 12)
            Text(band.title.uppercased())
                .font(.system(size: 10, weight: .semibold)).tracking(0.7)
                .foregroundStyle(Palette.inkMuted)
            Text("\(count)")
                .font(.system(size: 10, design: .monospaced))
                .foregroundStyle(Palette.inkFaint)
            Spacer()
            Text(band.detail)
                .font(.system(size: 9))
                .foregroundStyle(Palette.inkFaint)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 7)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.panel)
    }

    /// Le fasce di scarto. I tagli non sono arbitrari: sotto il 10% la
    /// differenza fra prezzo e fair value sta dentro l'incertezza della stima,
    /// e chiamarla sconto sarebbe una precisione che il modello non ha.
    enum Band: String, CaseIterable, Identifiable, Hashable {
        case deep, discount, fair, premium
        /// Sezione unica, usata quando l'ordinamento non e' lo scarto.
        case all
        var id: String { rawValue }

        static var allCases: [Band] { [.deep, .discount, .fair, .premium] }

        var title: String {
            switch self {
            case .deep:     return "Deep discount"
            case .discount: return "Discount"
            case .fair:     return "Around fair value"
            case .premium:  return "Above fair value"
            case .all:      return "All stocks"
            }
        }
        var detail: String {
            switch self {
            case .deep:     return "more than 40% below"
            case .discount: return "10% to 40% below"
            case .fair:     return "within 10% either way"
            case .premium:  return "priced above the model"
            case .all:      return "sorted by your choice"
            }
        }
        var color: Color {
            switch self {
            case .deep:     return Palette.positive
            case .discount: return Palette.positive.opacity(0.55)
            case .fair:     return Palette.inkFaint
            case .premium:  return Palette.negative
            case .all:      return Palette.accent
            }
        }

        static func of(_ premium: Double?) -> Band {
            guard let p = premium else { return .fair }
            if p <= -40 { return .deep }
            if p <= -10 { return .discount }
            if p <   10 { return .fair }
            return .premium
        }
    }

    private var grouped: [Band: [Stock]] {
        guard sort == .discount else { return [.all: rows] }
        return Dictionary(grouping: rows) { Band.of($0.premium(model: model)) }
    }

    // -----------------------------------------------------------------------

    private func openTickerFromLaunchArguments() {
        #if DEBUG
        guard path.isEmpty,
              let symbol = ProcessInfo.processInfo.environment["OPEN_TICKER"],
              let stock = store.stocks.first(where: { $0.ticker == symbol })
        else { return }
        path.append(stock)
        #endif
    }

    private var rows: [Stock] {
        var out = store.stocks.filter { filters.accepts($0, model: model) }

        let q = search.trimmingCharacters(in: .whitespaces).uppercased()
        if !q.isEmpty {
            out = out.filter {
                $0.ticker.hasPrefix(q) || $0.company.uppercased().contains(q)
            }
        }

        return out.sorted { a, b in
            switch sort {
            case .discount:
                // ATTENZIONE AL SEGNO. La colonna del dataset e' il PREZZO
                // rispetto al fair value: +85% vuol dire che costa l'85% in
                // piu' di quanto vale. Si ordina crescente — prima i piu' a
                // buon mercato.
                return sortAsc(a.premium(model: model), b.premium(model: model))
            case .marketCap: return sortDesc(a.marketCap, b.marketCap)
            case .pe:        return sortAsc(a.peRatio, b.peRatio)
            case .growth:    return sortDesc(a.growth5yCAGR, b.growth5yCAGR)
            case .roe:       return sortDesc(a.roePct, b.roePct)
            case .dividend:
                return sortDesc(a.dividendTTMYieldPct ?? a.dividendYield,
                                b.dividendTTMYieldPct ?? b.dividendYield)
            case .ticker:    return a.ticker < b.ticker
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
            case .discount:  return "Discount to fair value"
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
// Due righe di testo, gerarchizzate in ordine di quello che si cerca: di chi si
// tratta, come e' giudicato, quanto e' lo scarto, da quale prezzo a quale fair
// value. La freccia fra i due numeri dice da sola cosa sono — ed e' esattamente
// cio' che mancava alla prima versione.
// ---------------------------------------------------------------------------
struct ScreenerRow: View {
    let stock: Stock
    let model: ValuationModel

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 7) {
                    Text(stock.ticker)
                        .font(.system(.subheadline, design: .monospaced).weight(.semibold))
                        .foregroundStyle(Palette.ink)
                    Text(stock.company)
                        .font(.caption)
                        .foregroundStyle(Palette.inkMuted)
                        .lineLimit(1)
                }
                HStack(spacing: 8) {
                    CategoryTag(category: stock.lynchCategory, compact: true)
                    ValuationTag(verdict: stock.verdict(model: model))
                }
            }

            Spacer(minLength: 4)

            VStack(alignment: .trailing, spacing: 3) {
                DeltaText(value: stock.premium(model: model),
                          font: .system(.title3, design: .rounded)
                              .weight(.semibold).monospacedDigit(),
                          invert: true)
                HStack(spacing: 4) {
                    Text(Fmt.usd(stock.currentPrice))
                        .foregroundStyle(Palette.inkMuted)
                    Image(systemName: "arrow.right")
                        .font(.system(size: 7))
                        .foregroundStyle(Palette.inkFaint)
                    Text(Fmt.usd(stock.fairValue(model: model)))
                        .foregroundStyle(Palette.accent)
                }
                .font(.system(size: 11, design: .monospaced))
                Text("price → fair value")
                    .font(.system(size: 8))
                    .foregroundStyle(Palette.inkFaint)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .contentShape(Rectangle())
    }
}
