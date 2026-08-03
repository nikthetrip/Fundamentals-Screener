//
//  FinancialsSection.swift — La scheda organizzata come un bilancio.
//
//  L'ORDINE E' QUELLO DI UN BILANCIO, non quello della comodita': conto
//  economico, stato patrimoniale, rendiconto finanziario, e solo dopo i
//  rapporti che se ne ricavano. Chi legge un bilancio cerca le voci dove si
//  aspetta di trovarle.
//
//  OGNI RAPPORTO HA ACCANTO LA MEDIANA DELLA SUA INDUSTRIA. Un ROE del 18%
//  non e' ne' buono ne' cattivo finche' non si sa che le concorrenti stanno al
//  9 o al 30, e un margine netto del 4% e' disastroso nel software e ottimo
//  nella distribuzione alimentare. Il numero da solo non e' un giudizio — ed e'
//  per questo che ogni riquadro qui e' una griglia di KPI e non una lista di
//  cifre: la griglia porta con se' il confronto e la spiegazione.
//

import SwiftUI

struct FinancialsSection: View {
    let stock: Stock
    @EnvironmentObject private var store: DataStore

    @State private var page: Page = .income
    @State private var rows: [AnnualRow] = []

    enum Page: String, CaseIterable, Identifiable {
        case income  = "Income"
        case balance = "Balance sheet"
        case cash    = "Cash flow"
        case ratios  = "Ratios"
        case growth  = "Growth"
        var id: String { rawValue }
    }

    private var group: (medians: [String: Double], label: String?) { store.peers(for: stock) }
    private var peers: [String: Double] { group.medians }

    private func kpi(_ key: String) -> (String, Double?, Double?) {
        (key, stock.value(for: key), peers[key])
    }

    /// Gli ultimi cinque esercizi, DAL PIU' RECENTE.
    ///
    /// Un bilancio stampato va da sinistra a destra dal piu' vecchio al piu'
    /// nuovo, e su un foglio ha senso: si vedono tutte le colonne insieme. Qui
    /// se ne vedono tre, e in ordine cronologico la colonna che quasi sempre
    /// interessa — l'ultimo esercizio — sarebbe l'unica fuori dallo schermo.
    private var years: [AnnualRow] { Array(rows.suffix(5).reversed()) }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            ChipPicker(options: Page.allCases, selection: $page, label: { $0.rawValue })

            switch page {
            case .income:  income
            case .balance: balance
            case .cash:    cash
            case .ratios:  ratios
            case .growth:  growth
            }
        }
        .task(id: stock.ticker) { rows = store.annual(stock.ticker) }
    }

    // -----------------------------------------------------------------------
    // TABELLA PER ESERCIZI
    //
    // Scorre in orizzontale. Cinque esercizi non stanno in 390 punti, e
    // rimpicciolire il testo finche' ci stanno significa produrre una tabella
    // che nessuno riesce a leggere: meglio farne vedere tre e lasciare che le
    // altre si raggiungano con il dito.
    // -----------------------------------------------------------------------

    @ViewBuilder
    private func yearTable(_ specs: [(String, (AnnualRow) -> String, Bool)]) -> some View {
        if years.isEmpty {
            EmptyNote(text: "No annual statements on file for this ticker.")
        } else {
            ScrollView(.horizontal, showsIndicators: true) {
                VStack(alignment: .leading, spacing: 0) {
                    HStack(spacing: 0) {
                        Text("").frame(width: 132, alignment: .leading)
                        ForEach(years) { r in
                            Text(r.year)
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(Palette.inkMuted)
                                .frame(width: 84, alignment: .trailing)
                        }
                    }
                    .padding(.bottom, 4)
                    Divider().overlay(Palette.separator)

                    ForEach(Array(specs.enumerated()), id: \.offset) { _, spec in
                        FinancialRow(label: spec.0,
                                     values: years.map(spec.1),
                                     emphasis: spec.2,
                                     colWidth: 84)
                        Divider().overlay(Palette.separator.opacity(0.5))
                    }
                }
            }
        }
    }

    // -----------------------------------------------------------------------

    private var income: some View {
        VStack(alignment: .leading, spacing: 16) {
            Panel {
                SectionHeader(title: "Trailing twelve months",
                              note: "Tap any figure for what it means")
                KPIGrid(items: [
                    kpi("revenue_ttm"),
                    kpi("ebit_ttm"),
                    kpi("net_income_ttm"),
                    kpi("eps_ttm"),
                    kpi("operating_margin_pct"),
                    kpi("net_margin_pct"),
                    kpi("revenue_growth_yoy_pct"),
                    kpi("eps_growth_yoy"),
                ])
                if let basis = stock.financialsBasis {
                    Caption(text: "Basis: \(basis).")
                }
            }
            Panel {
                SectionHeader(title: "Income statement", note: "Filed fiscal years")
                yearTable([
                    ("Revenue",          { Fmt.money($0.revenue) },   true),
                    ("Operating income", { Fmt.money($0.ebit) },      false),
                    ("Net income",       { Fmt.money($0.netIncome) }, true),
                    ("Diluted EPS",      { Fmt.usd($0.eps) },         false),
                    ("Operating margin", { pctOf($0.ebit, $0.revenue) }, false),
                    ("Net margin",       { pctOf($0.netIncome, $0.revenue) }, false),
                ])
            }
        }
    }

    private var balance: some View {
        VStack(alignment: .leading, spacing: 16) {
            Panel {
                SectionHeader(title: "Capital structure", note: "Latest figures")
                KPIGrid(items: [
                    kpi("equity"),
                    kpi("assets"),
                    kpi("total_debt"),
                    kpi("cash"),
                    kpi("net_debt"),
                    kpi("book_value_per_share"),
                ])
            }
            Panel {
                SectionHeader(title: "Leverage and solidity")
                KPIGrid(items: [
                    kpi("equity_ratio_pct"),
                    kpi("debt_to_equity"),
                    kpi("net_debt_to_equity"),
                    kpi("debt_to_assets_pct"),
                    kpi("net_debt_to_ebit"),
                    kpi("net_debt_to_fcf"),
                    kpi("cash_to_debt_pct"),
                    kpi("asset_turnover"),
                ])
                Caption(text: "Negative net debt means more cash than debt: "
                        + "that is how it reads, not a calculation error.")
            }
            Panel {
                SectionHeader(title: "Balance sheet", note: "Filed fiscal years")
                yearTable([
                    ("Total assets",   { Fmt.money($0.assets) },    true),
                    ("Equity",         { Fmt.money($0.equity) },    true),
                    ("Total debt",     { Fmt.money($0.totalDebt) }, false),
                    ("Cash",           { Fmt.money($0.cash) },      false),
                    ("Equity ratio",   { pctOf($0.equity, $0.assets) }, false),
                ])
            }
        }
    }

    private var cash: some View {
        VStack(alignment: .leading, spacing: 16) {
            Panel {
                SectionHeader(title: "Trailing twelve months")
                KPIGrid(items: [
                    kpi("ocf_ttm"),
                    kpi("capex_ttm"),
                    kpi("fcf_ttm"),
                    kpi("fcf_per_share"),
                    kpi("fcf_margin_pct"),
                    kpi("ocf_margin_pct"),
                    kpi("fcf_conversion_pct"),
                    kpi("capex_to_revenue_pct"),
                    kpi("fcf_yield_pct"),
                    kpi("fcf_growth_yoy_pct"),
                ])
                Caption(text: "Cash conversion is free cash flow over net "
                        + "income: persistently below 100% and the accounting "
                        + "profit is not turning into money.")
            }
            Panel {
                SectionHeader(title: "Cash flow statement", note: "Filed fiscal years")
                yearTable([
                    ("Operating CF",   { Fmt.money($0.ocf) },   true),
                    ("Capex",          { Fmt.money($0.capex) }, false),
                    ("Free cash flow", { Fmt.money($0.fcf) },   true),
                    ("Dividends paid", { Fmt.money($0.dividendsPaid) }, false),
                    ("Cash conversion", { pctOf($0.fcf, $0.netIncome) }, false),
                ])
            }
        }
    }

    private var ratios: some View {
        VStack(alignment: .leading, spacing: 16) {
            Panel {
                SectionHeader(title: "Profitability",
                              note: "Compared with the industry median")
                KPIGrid(items: [
                    kpi("roe_pct"),
                    kpi("roic_pct"),
                    kpi("earning_power_pct"),
                    kpi("operating_margin_pct"),
                    kpi("net_margin_pct"),
                    kpi("fcf_margin_pct"),
                ])
            }
            Panel {
                SectionHeader(title: "Multiples")
                KPIGrid(items: [
                    kpi("pe_ratio"),
                    kpi("forward_pe"),
                    kpi("peg_ratio"),
                    kpi("price_to_book"),
                    kpi("ps_ratio"),
                    kpi("pfcf_ratio"),
                    kpi("ev_to_ebit"),
                    kpi("earnings_yield_pct"),
                ])
            }
            Panel {
                SectionHeader(title: "Per share")
                KPIGrid(items: [
                    kpi("revenue_per_share"),
                    kpi("eps_ttm"),
                    kpi("fcf_per_share"),
                    kpi("book_value_per_share"),
                ])
            }
        }
    }

    private var growth: some View {
        VStack(alignment: .leading, spacing: 16) {
            Panel {
                SectionHeader(title: "The growth that sets the multiple",
                              note: "It is the multiplicand of the fair value, not a detail")
                KPIGrid(items: [
                    kpi("growth_5y_cagr"),
                    kpi("growth_5y_trend"),
                    kpi("growth_5y_cagr_raw"),
                    kpi("earnings_volatility"),
                ])
                if let basis = stock.growthBasis {
                    Caption(text: "Source: \(basis).")
                }
                Caption(text: "A CAGR between two endpoints hands the verdict "
                        + "on the company to the starting quarter. The trend "
                        + "uses every point in the window, which is why it is "
                        + "preferred whenever it exists.")
            }

            Panel {
                SectionHeader(title: "Five-year rates",
                              note: "Compared with the industry median")
                KPIGrid(items: [
                    kpi("cagr_revenue_5y"),
                    kpi("cagr_eps_5y"),
                    kpi("cagr_net_income_5y"),
                    kpi("cagr_fcf_5y"),
                    kpi("cagr_ocf_5y"),
                ])
            }

            Panel {
                SectionHeader(title: "CAGR matrix", note: "By measure and horizon")
                cagrMatrix
            }

            Panel {
                SectionHeader(title: "Quality of the earnings series")
                MetricRow(label: "Loss quarters in 5 years",
                          value: stock.lossPeriods5y.map(String.init) ?? Fmt.dash,
                          tone: (stock.lossPeriods5y ?? 0) > 0 ? Palette.caution : nil)
                MetricRow(label: "Distinct loss episodes",
                          value: stock.lossEpisodes5y.map(String.init) ?? Fmt.dash)
                MetricRow(label: "Years since the last loss",
                          value: Fmt.ratio(stock.yearsSinceLastLoss))
                MetricRow(label: "EPS data points",
                          value: stock.epsPoints.map(String.init) ?? Fmt.dash)
                Caption(text: "The cyclical threshold is 50. It is the median "
                        + "absolute deviation of year-on-year changes, not the "
                        + "standard deviation: with the latter, the 2020 "
                        + "collapse alone made retail chains look more "
                        + "cyclical than carmakers.")
            }
        }
    }

    private var cagrMatrix: some View {
        let metrics: [(String, Double?, Double?, Double?)] = [
            ("EPS",           stock.cagrEPS3y, stock.cagrEPS5y, stock.cagrEPS10y),
            ("Revenue",       stock.cagrRevenue3y, stock.cagrRevenue5y, stock.cagrRevenue10y),
            ("Net income",    stock.cagrNetIncome3y, stock.cagrNetIncome5y, stock.cagrNetIncome10y),
            ("Free cash flow", stock.cagrFCF3y, stock.cagrFCF5y, stock.cagrFCF10y),
            ("Operating CF",  stock.cagrOCF3y, stock.cagrOCF5y, stock.cagrOCF10y),
        ]
        return VStack(spacing: 0) {
            HStack(spacing: 0) {
                Text("").frame(width: 130, alignment: .leading)
                ForEach(["3y", "5y", "10y"], id: \.self) { h in
                    Text(h).font(.caption.weight(.semibold))
                        .foregroundStyle(Palette.inkMuted)
                        .frame(maxWidth: .infinity, alignment: .trailing)
                }
            }
            .padding(.bottom, 4)
            Divider().overlay(Palette.separator)
            ForEach(Array(metrics.enumerated()), id: \.offset) { _, m in
                HStack(spacing: 0) {
                    Text(m.0).font(.subheadline).foregroundStyle(Palette.inkMuted)
                        .frame(width: 130, alignment: .leading)
                    ForEach([m.1, m.2, m.3], id: \.self) { v in
                        Text(Fmt.pct(v))
                            .font(.numberSmall)
                            .foregroundStyle(v == nil ? Palette.inkFaint
                                             : (v! < 0 ? Palette.negative : Palette.ink))
                            .frame(maxWidth: .infinity, alignment: .trailing)
                    }
                }
                .padding(.vertical, 5)
                Divider().overlay(Palette.separator.opacity(0.5))
            }
        }
    }

    // -----------------------------------------------------------------------

    /// Un rapporto fra due voci dello stesso esercizio, in percentuale.
    /// Con denominatore nullo o negativo il risultato non ha senso: torna un
    /// trattino, che e' un'informazione, invece di una percentuale enorme che
    /// sembra un dato.
    private func pctOf(_ num: Double?, _ den: Double?) -> String {
        guard let num, let den, den > 0 else { return Fmt.dash }
        return Fmt.pct(num / den * 100)
    }
}
