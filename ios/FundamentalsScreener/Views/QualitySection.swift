//
//  QualitySection.swift — Quanto fidarsi dei numeri delle altre quattro schede.
//
//  ESISTE PERCHE' UN DIFETTO DEI DATI NON SI PRESENTA COME UN ERRORE. Si
//  presenta come un numero plausibile e sbagliato: un EPS preso da una fonte
//  che non ha ancora recepito un frazionamento, un CAGR calcolato su due
//  estremi di cui il primo e' un trimestre disastroso, una serie di utili
//  vecchia di sei mesi. Nessuna di queste cose fa apparire un avviso da sola.
//
//  QUINDI QUI SI DICHIARA LA PROVENIENZA DI OGNI COSA: da dove viene l'EPS,
//  quale fonte ha vinto quando le due divergevano, quali tag XBRL sono stati
//  sommati per ottenere i ricavi, cosa manca del tutto.
//

import SwiftUI

struct QualitySection: View {
    let stock: Stock
    @EnvironmentObject private var store: DataStore

    @State private var cagr: [CagrDetail] = []

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            arbitration
            provenance
            cagrAudit
            concepts
        }
        .task(id: stock.ticker) { cagr = store.cagrDetail(stock.ticker) }
    }

    // -----------------------------------------------------------------------
    // L'ARBITRATO SULL'EPS
    //
    // Due fonti danno lo stesso numero finche' non lo danno. Quando divergono,
    // la scelta va dichiarata: e' quella che regge il P/E, il fair value e
    // quindi il giudizio in cima alla scheda.
    // -----------------------------------------------------------------------

    private var arbitration: some View {
        Panel {
            SectionHeader(title: "Where the EPS comes from")
            MetringRowStyled(label: "EPS used", value: Fmt.usd(stock.epsTTM),
                             emphasis: true)
            MetricRow(label: "From EDGAR (SEC filings)",
                      value: Fmt.usd(stock.epsTTMedgar))
            MetricRow(label: "From the market data provider",
                      value: Fmt.usd(stock.epsTTMyf))

            if let div = stock.epsDivergencePct, abs(div) > 0.01 {
                Divider().overlay(Palette.separator)
                MetricRow(label: "Divergence between the two", value: Fmt.pct(div),
                          tone: abs(div) > 10 ? Palette.caution : nil)
            }

            Divider().overlay(Palette.separator)
            MetricRow(label: "Source chosen", value: stock.epsSource ?? Fmt.dash)
            MetricRow(label: "Method", value: stock.epsMethod ?? Fmt.dash)
            MetricRow(label: "Basis", value: stock.epsBasis ?? Fmt.dash)
            if let age = stock.epsSeriesAgeDays {
                MetricRow(label: "Age of the latest figure",
                          value: "\(age) days",
                          tone: age > 180 ? Palette.caution : nil)
            }
            if let note = stock.epsBasisNote { Caption(text: note) }
            if let flag = stock.epsFlag, flag != "ok" {
                Caption(text: "Flag: \(flag).")
            }
            if let n = stock.nSplits, n > 0 {
                Caption(text: "\(n) splits in the history of this series: EPS figures are restated to the current share base.")
            }
        }
    }

    // -----------------------------------------------------------------------

    private var provenance: some View {
        Panel {
            SectionHeader(title: "Where everything else comes from")
            MetricRow(label: "Prices", value: stock.priceSource ?? Fmt.dash)
            MetricRow(label: "Sector classification",
                      value: stock.classificationSource ?? Fmt.dash)
            MetricRow(label: "Financial statements basis",
                      value: stock.financialsBasis ?? Fmt.dash)
            if let provider = stock.peRatioProvider {
                MetricRow(label: "P/E as reported by the provider",
                          value: Fmt.ratio(provider))
            }
            if let div = stock.peDivergencePct, abs(div) > 0.01 {
                MetricRow(label: "P/E divergence", value: Fmt.pct(div),
                          tone: abs(div) > 10 ? Palette.caution : nil)
            }

            if let missing = stock.missingInputs, !missing.isEmpty {
                Divider().overlay(Palette.separator)
                Text("Missing inputs")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(Palette.caution)
                ForEach(missing.split(separator: ";").map(String.init), id: \.self) { item in
                    Text("· " + item.trimmingCharacters(in: .whitespaces))
                        .font(.caption)
                        .foregroundStyle(Palette.inkMuted)
                }
                Caption(text: "Where an input is missing, everything that depends on it shows a dash. Not a zero: a zero is a value, a dash is the absence of one.")
            }
        }
    }

    // -----------------------------------------------------------------------
    // L'AUDIT DEI CAGR
    //
    // Ogni tasso di crescita nasce da due estremi. Mostrarli e' l'unico modo
    // di accorgersi che un +40% viene da un anno di partenza disastroso.
    // -----------------------------------------------------------------------

    private var cagrAudit: some View {
        Panel {
            SectionHeader(title: "The endpoints behind each CAGR",
                          note: "The rate and the two points it comes from")
            if cagr.isEmpty {
                EmptyNote(text: "No detail available for this ticker.")
            } else {
                ForEach(cagr) { row in
                    VStack(alignment: .leading, spacing: 3) {
                        HStack {
                            Text("\(metricLabel(row.metric)) · \(row.horizonYears)y")
                                .font(.footnote.weight(.medium))
                                .foregroundStyle(Palette.ink)
                            Spacer()
                            Text(Fmt.pct(row.cagrPct))
                                .font(.numberSmall)
                                .foregroundStyle((row.cagrPct ?? 0) < 0
                                                 ? Palette.negative : Palette.ink)
                        }
                        HStack(spacing: 6) {
                            Text("\(Fmt.date(row.startDate)) \(Fmt.ratio(row.startValue, digits: 2))")
                            Image(systemName: "arrow.right").font(.system(size: 8))
                            Text("\(Fmt.date(row.endDate)) \(Fmt.ratio(row.endValue, digits: 2))")
                            if let n = row.nPoints {
                                Text("· \(n) points")
                            }
                        }
                        .font(.caption2)
                        .foregroundStyle(Palette.inkFaint)
                    }
                    .padding(.vertical, 4)
                    Divider().overlay(Palette.separator.opacity(0.5))
                }
            }
        }
    }

    // -----------------------------------------------------------------------

    private var concepts: some View {
        Panel {
            SectionHeader(title: "XBRL tags used",
                          note: "Which filed line items make up each figure")
            if let used = stock.conceptsUsed, !used.isEmpty {
                ForEach(used.split(separator: ";").map(String.init), id: \.self) { entry in
                    let parts = entry.split(separator: "=", maxSplits: 1)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(String(parts.first ?? ""))
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(Palette.inkMuted)
                        if parts.count > 1 {
                            Text(parts[1].replacingOccurrences(of: "+", with: " + "))
                                .font(.system(size: 10, design: .monospaced))
                                .foregroundStyle(Palette.inkFaint)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .padding(.vertical, 3)
                }
                Caption(text: "Different companies file the same figure under different tags. Knowing which ones were summed is the only way to understand why two companies in the same sector have revenue that cannot be compared.")
            } else {
                EmptyNote(text: "No tags recorded for this ticker.")
            }
        }
    }

    private func metricLabel(_ raw: String) -> String {
        switch raw {
        case "eps":        return "EPS"
        case "revenue":    return "Revenue"
        case "net_income": return "Net income"
        case "fcf":        return "Free cash flow"
        case "ocf":        return "Operating cash flow"
        default:           return raw.capitalized
        }
    }
}

/// Una riga di metrica con il valore in evidenza. Sta qui e non fra i
/// componenti perche' serve solo a questa scheda: il valore che ha vinto
/// l'arbitrato va distinto dai due candidati, o le tre righe si leggono come
/// tre numeri alla pari.
private struct MetringRowStyled: View {
    let label: String
    let value: String
    var emphasis: Bool = false

    var body: some View {
        HStack {
            Text(label)
                .font(emphasis ? .subheadline.weight(.semibold) : .subheadline)
                .foregroundStyle(emphasis ? Palette.ink : Palette.inkMuted)
            Spacer()
            Text(value)
                .font(emphasis ? .numberBody.weight(.semibold) : .numberBody)
                .foregroundStyle(Palette.ink)
        }
    }
}
