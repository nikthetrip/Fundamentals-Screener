//
//  Components.swift — I pezzi che compongono ogni schermata.
//
//  Esistono per una ragione sola: una metrica deve avere lo stesso aspetto in
//  tutte e cinque le schede. Nella dashboard Streamlit questo lo garantiva
//  metric_grid(); qui lo garantiscono queste viste. Senza, ogni scheda
//  reinventa la propria riga e l'applicazione si legge come cinque
//  applicazioni.
//

import SwiftUI

// ---------------------------------------------------------------------------
// SEZIONE
// ---------------------------------------------------------------------------

/// Un titoletto in maiuscoletto con la sua riga. Divide senza aggiungere peso.
struct SectionHeader: View {
    let title: String
    var note: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title.uppercased())
                .font(.sectionLabel)
                .tracking(0.8)
                .foregroundStyle(Palette.inkMuted)
            if let note {
                Text(note)
                    .font(.caption)
                    .foregroundStyle(Palette.inkFaint)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// Il contenitore di ogni blocco: fondo, bordo sottile, angoli appena smussati.
struct Panel<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) { content }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Palette.surface)
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .stroke(Palette.separator, lineWidth: 0.5))
            .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}

// ---------------------------------------------------------------------------
// METRICHE
// ---------------------------------------------------------------------------

/// Una voce con il suo valore e, quando c'e', il confronto con l'industria.
///
/// IL CONFRONTO E' META' DEL DATO. Un ROE del 18% non e' ne' buono ne' cattivo
/// finche' non si sa che le sue concorrenti stanno al 9 o al 30. La dashboard
/// mette la mediana di settore accanto a ogni voce; qui pure, e nello stesso
/// posto in tutte le schede.
struct MetricRow: View {
    let label: String
    let value: String
    var peer: String? = nil
    var tone: Color? = nil

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(label)
                .font(.subheadline)
                .foregroundStyle(Palette.inkMuted)
            Spacer(minLength: 8)
            VStack(alignment: .trailing, spacing: 1) {
                Text(value)
                    .font(.numberBody)
                    .foregroundStyle(tone ?? Palette.ink)
                if let peer {
                    Text("industry \(peer)")
                        .font(.caption2)
                        .foregroundStyle(Palette.inkFaint)
                }
            }
        }
    }
}

/// Il numero grande di una scheda, con la sua didascalia sotto.
struct BigStat: View {
    let caption: String
    let value: String
    var tone: Color? = nil
    var footnote: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(caption.uppercased())
                .font(.system(size: 10, weight: .semibold))
                .tracking(0.6)
                .foregroundStyle(Palette.inkFaint)
            Text(value)
                .font(.numberLarge)
                .foregroundStyle(tone ?? Palette.ink)
                .lineLimit(1)
                .minimumScaleFactor(0.6)
            if let footnote {
                Text(footnote)
                    .font(.caption2)
                    .foregroundStyle(Palette.inkFaint)
                    .lineLimit(2)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// Griglia di numeri grandi, due per riga.
struct StatGrid: View {
    let items: [(String, String, Color?)]

    var body: some View {
        LazyVGrid(columns: [GridItem(.flexible(), alignment: .topLeading),
                            GridItem(.flexible(), alignment: .topLeading)],
                  spacing: 18) {
            ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                BigStat(caption: item.0, value: item.1, tone: item.2)
            }
        }
    }
}

// ---------------------------------------------------------------------------
// SELETTORE A PASTIGLIE
//
// PERCHE' NON IL SEGMENTED PICKER DI SISTEMA. Quello divide la larghezza in
// parti uguali e taglia cio' che non ci sta: cinque voci in 360 punti danno
// "Economi…" e "Patrimon…", che sono due etichette che non si leggono. Questo
// invece lascia a ogni voce la larghezza del suo testo e fa scorrere la fila.
// Costa uno scorrimento in piu' e restituisce delle parole intere.
// ---------------------------------------------------------------------------
struct ChipPicker<T: Hashable & Identifiable>: View {
    let options: [T]
    @Binding var selection: T
    let label: (T) -> String
    var icon: ((T) -> String)? = nil

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(options) { option in
                        Button {
                            withAnimation(.easeOut(duration: 0.15)) { selection = option }
                        } label: {
                            HStack(spacing: 5) {
                                if let icon {
                                    Image(systemName: icon(option)).font(.system(size: 11))
                                }
                                Text(label(option))
                                    .font(.system(size: 13, weight: .medium))
                                    .fixedSize()
                            }
                            .padding(.horizontal, 11)
                            .padding(.vertical, 7)
                            .background(selection == option ? Palette.ink : Palette.panel)
                            .foregroundStyle(selection == option ? Palette.background
                                                                 : Palette.inkMuted)
                            .clipShape(Capsule())
                        }
                        .buttonStyle(.plain)
                        .id(option)
                    }
                }
                .padding(.vertical, 2)
            }
            .onChange(of: selection) { _, new in
                withAnimation { proxy.scrollTo(new, anchor: .center) }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// ETICHETTE
// ---------------------------------------------------------------------------

/// La categoria di Lynch: una barra del suo colore e il nome. Niente emoji.
struct CategoryTag: View {
    let category: String?
    var compact: Bool = false

    var body: some View {
        HStack(spacing: 6) {
            RoundedRectangle(cornerRadius: 1)
                .fill(LynchCategory.color(category))
                .frame(width: 3, height: compact ? 11 : 14)
            Text(category ?? "Unclassified")
                .font(compact ? .caption2 : .caption)
                .foregroundStyle(Palette.inkMuted)
                .lineLimit(1)
        }
    }
}

/// Il verdetto: sotto, sopra o in linea con il fair value.
struct ValuationTag: View {
    let verdict: String?

    private var tone: Color {
        switch (verdict ?? "").lowercased() {
        case "undervalued": return Palette.positive
        case "overvalued":  return Palette.negative
        case "fair":        return Palette.inkMuted
        default:            return Palette.inkFaint
        }
    }

    private var text: String {
        switch (verdict ?? "").lowercased() {
        case "undervalued": return "Undervalued"
        case "overvalued":  return "Overvalued"
        case "fair":        return "Fairly valued"
        default:            return "Not valuable"
        }
    }

    var body: some View {
        Text(text)
            .font(.system(size: 11, weight: .medium))
            .foregroundStyle(tone)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(tone.opacity(0.12))
            .clipShape(RoundedRectangle(cornerRadius: 4))
    }
}

/// Uno scarto percentuale, colorato secondo il segno.
struct DeltaText: View {
    let value: Double?
    var font: Font = .numberBody
    /// Quando `true`, un valore positivo e' un fatto negativo (es. il premio
    /// sul fair value). Il colore segue il giudizio, non il segno.
    var invert: Bool = false

    var body: some View {
        let v = value ?? .nan
        let good = invert ? v < 0 : v > 0
        Text(Fmt.signedPct(value))
            .font(font)
            .foregroundStyle(v.isFinite ? (good ? Palette.positive : Palette.negative)
                                        : Palette.inkFaint)
    }
}

// ---------------------------------------------------------------------------
// STATI VUOTI
//
// Dove un dato manca, si dice quale e perche'. Un riquadro che sparisce
// lascia chi legge a chiedersi se non c'e' il dato o se non c'e' la funzione.
// ---------------------------------------------------------------------------
struct EmptyNote: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.footnote)
            .foregroundStyle(Palette.inkFaint)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 8)
    }
}

/// Una nota discorsiva sotto un blocco: spiega da dove viene un numero.
struct Caption: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(Palette.inkFaint)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// ---------------------------------------------------------------------------
// TABELLE
// ---------------------------------------------------------------------------

/// Una riga di bilancio: la voce a sinistra, un esercizio per colonna.
/// Scorre in orizzontale perche' cinque esercizi non stanno in 390 punti e
/// rimpicciolire il testo per farceli stare li' rende illeggibili.
struct FinancialRow: View {
    let label: String
    let values: [String]
    var emphasis: Bool = false
    var colWidth: CGFloat = 92

    var body: some View {
        HStack(spacing: 0) {
            Text(label)
                .font(emphasis ? .subheadline.weight(.semibold) : .subheadline)
                .foregroundStyle(emphasis ? Palette.ink : Palette.inkMuted)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
                .frame(width: 132, alignment: .leading)
            ForEach(Array(values.enumerated()), id: \.offset) { _, v in
                Text(v)
                    .font(.numberSmall)
                    .foregroundStyle(Palette.ink)
                    .frame(width: colWidth, alignment: .trailing)
            }
        }
        .padding(.vertical, 5)
    }
}
