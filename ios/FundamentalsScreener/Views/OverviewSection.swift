//
//  OverviewSection.swift — Che azienda e'.
//
//  QUATTRO BLOCCHI, NELL'ORDINE DI overview.py: cosa fa, da dove vengono i
//  ricavi, cosa si aspettano gli analisti. Nient'altro.
//
//  LA VERSIONE PRECEDENTE ERA SBAGLIATA, e vale la pena scriverlo qui perche'
//  l'errore e' facile da rifare. Ci avevo messo dentro la categoria di Lynch,
//  i multipli di mercato, il conto economico e i ritorni sul capitale: roba
//  che appartiene a Valuation e a Financials. Il risultato erano tre schede
//  che dicevano le stesse cose in ordini diversi, e una Overview che aveva
//  perso i suoi blocchi veri — segmenti e giudizi degli analisti — per far
//  posto a quelli degli altri.
//
//  Overview risponde a una domanda sola: CHE AZIENDA E'. Nessun multiplo,
//  nessun giudizio di valutazione. Quelli stanno una scheda piu' in la'.
//

import SwiftUI

struct OverviewSection: View {
    let stock: Stock
    let model: ValuationModel
    @EnvironmentObject private var store: DataStore

    @State private var profile: Profile?
    @State private var segments: [String: [SegmentRow]] = [:]

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            whatItDoes
            whereRevenueComesFrom
            whatAnalystsExpect
        }
        .task(id: stock.ticker) {
            profile = store.profile(stock.ticker)
            segments = store.segments(stock.ticker)
        }
    }

    // -----------------------------------------------------------------------
    // 1. CHE COSA FA
    // -----------------------------------------------------------------------

    private var whatItDoes: some View {
        Panel {
            SectionHeader(title: "What this company does")

            if let summary = profile?.summary {
                let split = Self.splitSummary(summary)
                Text(split.head)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(Palette.ink)
                    .fixedSize(horizontal: false, vertical: true)
                if !split.rest.isEmpty {
                    DisclosureGroup {
                        Text(split.rest)
                            .font(.subheadline)
                            .foregroundStyle(Palette.inkMuted)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.top, 6)
                    } label: {
                        Text("Read the full business description")
                            .font(.caption)
                            .foregroundStyle(Palette.accent)
                    }
                    .tint(Palette.accent)
                }
                Divider().overlay(Palette.separator)
            } else {
                EmptyNote(text: "No business description on file. It comes from "
                          + "the «Profiles» step of the nightly build.")
            }

            MetricRow(label: "Sector", value: stock.sector ?? Fmt.dash)
            MetricRow(label: "Industry", value: stock.industry ?? Fmt.dash)
            if let sic = stock.sicDescription {
                MetricRow(label: "SIC classification", value: sic)
            }
            MetricRow(label: "Listed on", value: stock.exchange ?? Fmt.dash)
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

    // -----------------------------------------------------------------------
    // 2. DA DOVE VENGONO I RICAVI
    //
    // Per divisione, per area geografica e per prodotto, esattamente come la
    // societa' li comunica alla SEC. Non e' un dato che si trova nelle API dei
    // totali consolidati: sta un livello piu' sotto, nelle dimensioni del
    // documento XBRL allegato al bilancio annuale.
    // -----------------------------------------------------------------------

    @ViewBuilder
    private var whereRevenueComesFrom: some View {
        if segments.isEmpty {
            Panel {
                SectionHeader(title: "Where the revenue comes from")
                EmptyNote(text: "No breakdown on file for this company. It is "
                          + "read from the XBRL of the latest annual report by "
                          + "the «Segments» step of the build.")
            }
        } else {
            ForEach(segments.keys.sorted(), id: \.self) { axis in
                segmentPanel(rows: segments[axis] ?? [])
            }
        }
    }

    @ViewBuilder
    private func segmentPanel(rows: [SegmentRow]) -> some View {
        // Il totale si calcola qui e non dentro il Panel: un `let` dentro un
        // result builder non compila.
        let sum = rows.reduce(0) { $0 + $1.value }
        if let first = rows.first, sum > 0 {
            Panel {
                SectionHeader(title: first.title ?? "Where the revenue comes from",
                              note: "Fiscal year ended \(Fmt.date(first.periodEnd))")
                if let coverage = first.coverage, coverage < 0.98 {
                    // Una ripartizione parziale va dichiarata, o le percentuali
                    // si leggono come quote del fatturato totale invece che
                    // della parte ripartita.
                    HStack(spacing: 5) {
                        Image(systemName: "exclamationmark.circle")
                            .font(.system(size: 10))
                        Text("Covers \(Fmt.pct(coverage * 100, digits: 0)) of "
                             + "consolidated revenue — the rest of the business "
                             + "is not broken down this way. Shares below are of "
                             + "the part shown.")
                            .font(.caption2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .foregroundStyle(Palette.caution)
                }
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
                        + "offending line is not shown. A breakdown that does "
                        + "not add up looks like information and is an error.")
            }
        }
    }

    // -----------------------------------------------------------------------
    // 3. COSA SI ASPETTANO GLI ANALISTI
    //
    // Su una scala sola, non a barre. La domanda e' "dove sta il prezzo di
    // adesso rispetto a quello che si aspettano", e la risposta si legge su
    // una riga; con delle barre affiancate quella distanza andrebbe
    // ricostruita a mente confrontando altezze.
    // -----------------------------------------------------------------------

    private var whatAnalystsExpect: some View {
        Panel {
            SectionHeader(title: "What analysts expect",
                          note: profile?.analystCount.map { "\($0) opinions" })

            if let profile, let low = profile.targetLow, let high = profile.targetHigh,
               let price = stock.currentPrice, high > low {
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
                    scaleLabel("Lowest", Fmt.usd(low, digits: 0), .leading)
                    Spacer()
                    scaleLabel("Mean", Fmt.usd(profile.targetMean, digits: 0), .center)
                    Spacer()
                    scaleLabel("Highest", Fmt.usd(high, digits: 0), .trailing)
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
                Caption(text: "Analyst targets feed into nothing else in this "
                        + "app: the fair value comes from filed earnings. They "
                        + "are here as a point of comparison, not as a "
                        + "competing estimate.")
            } else {
                EmptyNote(text: "No analyst coverage on file for this ticker.")
            }
        }
    }

    /// La prima frase da sola, il resto a parte.
    ///
    /// La prima frase di una descrizione depositata dice sempre il mestiere
    /// ("Alcoa Corporation engages in the bauxite mining, alumina refining,
    /// aluminum production…"); le quattordici righe successive elencano
    /// controllate e clientele. Separarle e' la differenza fra una riga che si
    /// legge e un paragrafo che si salta. E' la stessa regola di
    /// `_split_summary` in overview.py.
    static func splitSummary(_ text: String) -> (head: String, rest: String) {
        let full = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !full.isEmpty else { return ("", "") }

        // Il taglio e' dopo un punto seguito da spazio e maiuscola: un punto
        // dentro "Inc." o "U.S." non chiude una frase.
        var index = full.startIndex
        while let dot = full[index...].firstIndex(where: { ".!?".contains($0) }) {
            let after = full.index(after: dot)
            guard after < full.endIndex else { break }
            let rest = full[after...].drop(while: { $0 == " " })
            if full[after] == " ", let first = rest.first, first.isUppercase,
               full.distance(from: full.startIndex, to: dot) > 40 {
                let head = String(full[..<full.index(after: dot)])
                return (head, String(rest))
            }
            index = after
        }
        return (full, "")
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

    private func scaleLabel(_ caption: String, _ value: String,
                            _ alignment: HorizontalAlignment) -> some View {
        VStack(alignment: alignment, spacing: 1) {
            Text(caption).font(.system(size: 9)).foregroundStyle(Palette.inkFaint)
            Text(value).font(.numberSmall).foregroundStyle(Palette.inkMuted)
        }
    }
}
