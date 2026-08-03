//
//  KPI.swift — La scheda di una metrica: valore, scarto dai pari, spiegazione.
//
//  E' IL PEZZO CHE MANCAVA rispetto alla dashboard. Li' ogni numero e' un
//  `st.metric`: valore grande, sotto il delta contro la mediana dell'industria,
//  e un punto interrogativo che apre la spiegazione — che cosa e' quella
//  grandezza, con quale formula si ottiene, come si legge. Senza quelle tre
//  cose restano dei numeri, e un numero da solo non e' un giudizio: un ROE del
//  18% non si sa se sia buono finche' non si sa che le concorrenti stanno al 9
//  o al 30.
//
//  IL COLORE SEGUE IL GIUDIZIO, NON IL SEGNO. Su un P/E o sul debito, stare
//  sopra i pari e' un difetto: e' `better` in Metrics.swift a dirlo, ed e'
//  esattamente l'errore che Streamlit fa di suo colorando di verde ogni delta
//  positivo.
//

import SwiftUI

// ---------------------------------------------------------------------------
// LA SCHEDA
// ---------------------------------------------------------------------------

struct KPICard: View {
    let key: String
    let value: Double?
    var peer: Double? = nil
    /// Sostituisce il valore formattato dalla specifica. Serve dove il numero
    /// mostrato non e' quello grezzo (una data, un conteggio con l'unita').
    var display: String? = nil

    @State private var explaining = false

    private var spec: MetricSpec? { Metrics.spec(key) }

    private var comparison: (amount: Double, text: String)? {
        guard let spec, spec.comparable else { return nil }
        return Metrics.gap(key, value: value, peer: peer)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 3) {
                Text((spec?.label ?? key).uppercased())
                    .font(.system(size: 10, weight: .semibold))
                    .tracking(0.5)
                    .foregroundStyle(Palette.inkFaint)
                    .lineLimit(1)
                if spec?.help.isEmpty == false {
                    Image(systemName: "info.circle")
                        .font(.system(size: 9))
                        .foregroundStyle(Palette.inkFaint.opacity(0.7))
                }
                Spacer(minLength: 0)
            }

            Text(display ?? spec?.format(value) ?? Fmt.ratio(value))
                .font(.numberLarge)
                .foregroundStyle(Palette.ink)
                .lineLimit(1)
                .minimumScaleFactor(0.55)

            if let comparison {
                Text(comparison.text)
                    .font(.system(size: 10, weight: .medium))
                    .foregroundStyle(Metrics.tone(key, gap: comparison.amount))
                    .lineLimit(1)
            } else if peer != nil, let spec, spec.comparable {
                Text("no peer median")
                    .font(.system(size: 10))
                    .foregroundStyle(Palette.inkFaint)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
        .onTapGesture { if spec?.help.isEmpty == false { explaining = true } }
        .sheet(isPresented: $explaining) {
            if let spec { MetricExplanation(spec: spec, value: value, peer: peer) }
        }
    }
}

/// La griglia: due schede per riga. Tre stanno strette e i numeri lunghi —
/// una capitalizzazione, un enterprise value — verrebbero rimpiccioliti fino
/// a non leggersi.
struct KPIGrid: View {
    let items: [(key: String, value: Double?, peer: Double?)]
    var columns: Int = 2

    var body: some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 14,
                                                     alignment: .topLeading),
                                 count: columns),
                  spacing: 16) {
            ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                KPICard(key: item.key, value: item.value, peer: item.peer)
            }
        }
    }
}

// ---------------------------------------------------------------------------
// LA SPIEGAZIONE
//
// Su un telefono non esiste il passaggio del mouse, quindi il testo che nella
// dashboard sta dentro un suggerimento qui e' un foglio che si apre toccando
// la scheda. Meglio cosi': quelle spiegazioni sono lunghe, e in un fumetto
// sopra il dito non si leggerebbero comunque.
// ---------------------------------------------------------------------------

struct MetricExplanation: View {
    let spec: MetricSpec
    let value: Double?
    let peer: Double?

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    HStack(alignment: .firstTextBaseline, spacing: 16) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("This company")
                                .font(.system(size: 10, weight: .semibold))
                                .tracking(0.5)
                                .foregroundStyle(Palette.inkFaint)
                            Text(spec.format(value))
                                .font(.numberLarge)
                                .foregroundStyle(Palette.ink)
                        }
                        if let peer {
                            VStack(alignment: .leading, spacing: 2) {
                                Text("Industry median")
                                    .font(.system(size: 10, weight: .semibold))
                                    .tracking(0.5)
                                    .foregroundStyle(Palette.inkFaint)
                                Text(spec.format(peer))
                                    .font(.numberLarge)
                                    .foregroundStyle(Palette.inkMuted)
                            }
                        }
                        Spacer()
                    }

                    Divider().overlay(Palette.separator)

                    // Il testo arriva da app.py in Markdown: `**grassetto**`
                    // sui titoletti. LocalizedStringKey lo interpreta, cosi'
                    // gli asterischi non finiscono a schermo.
                    Text(LocalizedStringKey(spec.help))
                        .font(.subheadline)
                        .foregroundStyle(Palette.ink)
                        .fixedSize(horizontal: false, vertical: true)

                    if spec.better != nil {
                        Divider().overlay(Palette.separator)
                        Text(spec.better == .high
                             ? "Higher is better, all else equal."
                             : "Lower is better, all else equal.")
                            .font(.footnote)
                            .foregroundStyle(Palette.inkMuted)
                    }
                }
                .padding(20)
            }
            .background(Palette.background)
            .navigationTitle(spec.label)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }.fontWeight(.semibold)
                }
            }
        }
        .presentationDetents([.medium, .large])
    }
}

// ---------------------------------------------------------------------------
// LA RIGA CON DELTA
//
// La versione compatta, per gli elenchi lunghi dove sessanta schede grandi
// sarebbero uno scorrimento infinito.
// ---------------------------------------------------------------------------

struct KPIRow: View {
    let key: String
    let value: Double?
    var peer: Double? = nil

    @State private var explaining = false
    private var spec: MetricSpec? { Metrics.spec(key) }

    var body: some View {
        let comparison = (spec?.comparable ?? false)
            ? Metrics.gap(key, value: value, peer: peer) : nil

        HStack(alignment: .firstTextBaseline, spacing: 10) {
            HStack(spacing: 4) {
                Text(spec?.label ?? key)
                    .font(.subheadline)
                    .foregroundStyle(Palette.inkMuted)
                if spec?.help.isEmpty == false {
                    Image(systemName: "info.circle")
                        .font(.system(size: 9))
                        .foregroundStyle(Palette.inkFaint.opacity(0.6))
                }
            }
            Spacer(minLength: 8)
            VStack(alignment: .trailing, spacing: 1) {
                Text(spec?.format(value) ?? Fmt.ratio(value))
                    .font(.numberBody)
                    .foregroundStyle(Palette.ink)
                if let comparison {
                    Text(comparison.text)
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(Metrics.tone(key, gap: comparison.amount))
                }
            }
        }
        .contentShape(Rectangle())
        .onTapGesture { if spec?.help.isEmpty == false { explaining = true } }
        .sheet(isPresented: $explaining) {
            if let spec { MetricExplanation(spec: spec, value: value, peer: peer) }
        }
    }
}
