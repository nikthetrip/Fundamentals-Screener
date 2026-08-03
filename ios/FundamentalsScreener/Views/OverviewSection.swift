//
//  OverviewSection.swift — Che azienda e'.
//
//  STA PER PRIMA per la stessa ragione per cui sta per prima nella dashboard:
//  si puo' leggere il margine operativo di una societa', il suo ROIC e il suo
//  debito senza mai incontrare la parola che dice che mestiere fa. Un multiplo
//  giudicato senza quel contesto e' un numero, non un giudizio.
//
//  DESCRIZIONE, ANALISTI E SEGMENTI ARRIVANO DA build_extras.py, che gira
//  dentro la build notturna. Non possono arrivare dal telefono: Yahoo protegge
//  quelle API con un cookie piu' un crumb che cambia senza preavviso, e la
//  ripartizione dei ricavi sta nell'XBRL del bilancio, un documento da diversi
//  megabyte. Se quel passaggio non ha ancora prodotto nulla, i riquadri
//  relativi lo dicono invece di sparire.
//

import SwiftUI

struct OverviewSection: View {
    let stock: Stock
    let model: ValuationModel
    @EnvironmentObject private var store: DataStore

    @State private var profile: Profile?
    @State private var segments: [String: [SegmentRow]] = [:]

    private var group: (medians: [String: Double], label: String?) { store.peers(for: stock) }
    private var peers: [String: Double] { group.medians }

    private func kpi(_ key: String) -> (String, Double?, Double?) {
        (key, stock.value(for: key), peers[key])
    }

    /// Contro chi si sta confrontando questa societa'. Va detto: uno scarto
    /// «+8 pp vs peers» significa una cosa diversa se i pari sono le dodici
    /// concorrenti dirette o l'intero settore tecnologico.
    private var peerNote: String {
        guard let label = group.label else {
            return "No comparable peer group — figures shown without a benchmark"
        }
        return "Compared with the \(label) · tap any figure for what it means"
    }

    var body: some View {
        content
            .task(id: stock.ticker) {
                profile = store.profile(stock.ticker)
                segments = store.segments(stock.ticker)
            }
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: 16) {
            businessDescription
            classification
            marketPanel
            dividendPanel
            incomePanel
            returnsPanel
            analystTargets
            revenueBreakdown
        }
    }

    // -----------------------------------------------------------------------

    @ViewBuilder
    private var businessDescription: some View {
        Panel {
            SectionHeader(title: "What it does")
            if let profile, let summary = profile.summary {
                Text(summary)
                    .font(.subheadline)
                    .foregroundStyle(Palette.ink)
                    .fixedSize(horizontal: false, vertical: true)
                Divider().overlay(Palette.separator)
            } else {
                EmptyNote(text: "No business description on file. It comes from "
                          + "the «Profiles» step of the nightly build, which "
                          + "either has not run or could not reach the provider.")
            }

            MetricRow(label: "Sector", value: stock.sector ?? Fmt.dash)
            MetricRow(label: "Industry", value: stock.industry ?? Fmt.dash)
            if let sic = stock.sicDescription {
                MetricRow(label: "SIC classification", value: sic)
            }
            MetricRow(label: "Exchange", value: stock.exchange ?? Fmt.dash)
            if let location = profile?.location {
                MetricRow(label: "Headquarters", value: location)
            }
            if let employees = profile?.employees {
                MetricRow(label: "Employees", value: Fmt.count(employees))
            }
            if let site = profile?.website, let url = URL(string: site) {
                Link(destination: url) {
                    HStack {
                        Text("Website").font(.subheadline)
                            .foregroundStyle(Palette.inkMuted)
                        Spacer()
                        Text(url.host ?? site).font(.footnote)
                            .foregroundStyle(Palette.accent)
                        Image(systemName: "arrow.up.right")
                            .font(.caption2).foregroundStyle(Palette.accent)
                    }
                }
            }
            if stock.staleSymbol {
                Caption(text: "The price provider no longer lists this ticker "
                        + "as active: the market data may belong to a "
                        + "different company.")
            }
        }
    }

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
                kpi("lynch_fair_pe"),
                kpi("growth_5y_cagr"),
                kpi("fair_value_peg"),
                kpi("lynch_ratio"),
            ])
            if let basis = stock.lynchPEBasis { Caption(text: "Anchor: \(basis)") }
            if let reason = stock.lynchConfidenceNote {
                Caption(text: "Confidence: \(reason)")
            }
        }
    }

    private var marketPanel: some View {
        Panel {
            SectionHeader(title: "Market", note: peerNote)
            KPIGrid(items: [
                kpi("market_cap"),
                kpi("enterprise_value"),
                kpi("pe_ratio"),
                kpi("forward_pe"),
                kpi("earnings_yield_pct"),
                kpi("peg_ratio"),
                kpi("price_to_book"),
                kpi("ps_ratio"),
                kpi("ev_to_ebit"),
                kpi("beta"),
            ])
            Divider().overlay(Palette.separator)
            KPIRow(key: "eps_ttm", value: stock.epsTTM)
            KPIRow(key: "eps_growth_yoy", value: stock.epsGrowthYoY,
                   peer: peers["eps_growth_yoy"])
            KPIRow(key: "shares_outstanding", value: stock.sharesOutstanding)
            if let next = stock.nextEarningsDate {
                MetricRow(label: "Next earnings", value: Fmt.date(next))
            }
        }
    }

    @ViewBuilder
    private var dividendPanel: some View {
        if (stock.dividendTTMYieldPct ?? stock.dividendYield ?? 0) > 0 {
            Panel {
                SectionHeader(title: "Dividend")
                KPIGrid(items: [
                    kpi("dividend_yield"),
                    kpi("dividend_ttm_yield_pct"),
                    kpi("dividend_rate"),
                    kpi("payout_ratio_pct"),
                ])
                Caption(text: "The headline yield annualises the last regular "
                        + "payment; the trailing figure adds up what was "
                        + "actually paid over twelve months, specials "
                        + "included. They diverge after a cut, a raise or a "
                        + "one-off distribution.")
            }
        }
    }

    private var incomePanel: some View {
        Panel {
            SectionHeader(title: "Income statement", note: "Trailing twelve months")
            KPIGrid(items: [
                kpi("revenue_ttm"),
                kpi("net_income_ttm"),
                kpi("ebit_ttm"),
                kpi("fcf_ttm"),
                kpi("operating_margin_pct"),
                kpi("net_margin_pct"),
                kpi("fcf_margin_pct"),
                kpi("revenue_growth_yoy_pct"),
            ])
        }
    }

    private var returnsPanel: some View {
        Panel {
            SectionHeader(title: "Returns on capital")
            KPIGrid(items: [
                kpi("roe_pct"),
                kpi("roic_pct"),
                kpi("earning_power_pct"),
                kpi("fcf_yield_pct"),
            ])
            Caption(text: "ROIC uses a flat 21% tax rate for every company. An "
                    + "exact effective rate would need the filed tax line for "
                    + "each one, and a comparable ROIC is worth more than an "
                    + "exact figure that cannot be compared with anything.")
        }
    }

    // -----------------------------------------------------------------------
    // I RIQUADRI CHE DIPENDONO DA build_extras.py
    // -----------------------------------------------------------------------

    /// Gli obiettivi degli analisti su una scala sola, non a barre.
    ///
    /// La domanda e' "dove sta il prezzo di adesso rispetto a quello che si
    /// aspettano", e la risposta si legge su una riga. Con delle barre
    /// affiancate quella distanza andrebbe ricostruita a mente confrontando
    /// altezze.
    @ViewBuilder
    private var analystTargets: some View {
        if let profile, let low = profile.targetLow, let high = profile.targetHigh,
           let price = stock.currentPrice, high > low {
            Panel {
                SectionHeader(title: "Analyst price targets",
                              note: profile.analystCount.map { "\($0) opinions" })

                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        Capsule().fill(Palette.panel).frame(height: 8)
                            .frame(maxHeight: .infinity, alignment: .center)
                        if let mean = profile.targetMean {
                            marker(at: geo.size.width * position(mean, low, high),
                                   color: Palette.accent, width: 3)
                        }
                        marker(at: geo.size.width * position(price, low, high),
                               color: Palette.ink, width: 4)
                    }
                }
                .frame(height: 34)

                HStack {
                    label("Lowest", Fmt.usd(low, digits: 0), .leading)
                    Spacer()
                    label("Mean", Fmt.usd(profile.targetMean, digits: 0), .center)
                    Spacer()
                    label("Highest", Fmt.usd(high, digits: 0), .trailing)
                }

                Divider().overlay(Palette.separator)
                MetricRow(label: "Price today", value: Fmt.usd(price))
                if let mean = profile.targetMean {
                    HStack {
                        Text("Gap to the mean target")
                            .font(.subheadline).foregroundStyle(Palette.inkMuted)
                        Spacer()
                        DeltaText(value: (mean / price - 1) * 100)
                    }
                }
                if let judgement = profile.recommendationLabel {
                    MetricRow(label: "Consensus", value: judgement)
                }
                Caption(text: "Analyst targets feed into nothing on this page: "
                        + "the fair value comes from filed earnings. They are "
                        + "here as a point of comparison, not as a competing "
                        + "estimate.")
            }
        }
    }

    /// Dove cade un valore sulla scala minimo–massimo, da 0 a 1. Ritagliato
    /// agli estremi: il prezzo di oggi puo' stare fuori dall'intervallo degli
    /// obiettivi, e senza il ritaglio il suo indicatore uscirebbe dal riquadro.
    private func position(_ value: Double, _ low: Double, _ high: Double) -> CGFloat {
        CGFloat((min(max(value, low), high) - low) / (high - low))
    }

    private func marker(at position: CGFloat, color: Color, width: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: width / 2)
            .fill(color)
            .frame(width: width, height: 24)
            .offset(x: max(0, position - width / 2))
            .frame(maxHeight: .infinity, alignment: .center)
    }

    private func label(_ caption: String, _ value: String,
                       _ alignment: HorizontalAlignment) -> some View {
        VStack(alignment: alignment, spacing: 1) {
            Text(caption).font(.system(size: 9)).foregroundStyle(Palette.inkFaint)
            Text(value).font(.numberSmall).foregroundStyle(Palette.inkMuted)
        }
    }

    /// La ripartizione dei ricavi come la societa' la comunica alla SEC.
    @ViewBuilder
    private var revenueBreakdown: some View {
        ForEach(segments.keys.sorted(), id: \.self) { axis in
            segmentPanel(rows: segments[axis] ?? [])
        }
    }

    @ViewBuilder
    private func segmentPanel(rows: [SegmentRow]) -> some View {
        // Il totale si calcola qui e non dentro il Panel: un `let` dentro un
        // result builder non compila, ed e' il genere di vincolo che porta a
        // ricalcolare la stessa somma dentro ogni riga.
        let sum = rows.reduce(0) { $0 + $1.value }
        if let first = rows.first, sum > 0 {
            Panel {
                SectionHeader(title: first.title ?? "Revenue breakdown",
                              note: "Fiscal year ended \(Fmt.date(first.periodEnd))")
                ForEach(rows) { row in
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(alignment: .firstTextBaseline) {
                            Text(row.member)
                                .font(.footnote)
                                .foregroundStyle(Palette.ink)
                                .lineLimit(2)
                            Spacer(minLength: 8)
                            Text(Fmt.money(row.value))
                                .font(.numberSmall)
                                .foregroundStyle(Palette.inkMuted)
                            Text(Fmt.pct(row.value / sum * 100, digits: 0))
                                .font(.numberSmall)
                                .foregroundStyle(Palette.ink)
                                .frame(width: 44, alignment: .trailing)
                        }
                        GeometryReader { geo in
                            Capsule().fill(Palette.accent.opacity(0.75))
                                .frame(width: geo.size.width * CGFloat(row.value / sum))
                        }
                        .frame(height: 4)
                    }
                    .padding(.vertical, 4)
                }
                if let note = first.note { Caption(text: note) }
                Caption(text: "Revenue net of inter-segment sales, checked "
                        + "against the consolidated revenue of the same year: "
                        + "where the parts did not add up to the whole, the "
                        + "offending line is not shown.")
            }
        }
    }
}
