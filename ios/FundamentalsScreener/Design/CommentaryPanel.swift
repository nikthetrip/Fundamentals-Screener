//
//  CommentaryPanel.swift — «What these numbers say».
//
//  E' IL RIQUADRO CHE MANCAVA. Nella dashboard sta sotto ogni sezione della
//  scheda finanziaria e dice in parole cosa raccontano quelle cifre: il margine
//  e' sceso di tre punti in quattro anni, il debito netto vale sei anni di
//  cassa libera, il ROIC sta sotto il costo del capitale. Senza, la scheda e'
//  una griglia di numeri che ognuno interpreta per conto suo — ed e' proprio
//  quel lavoro di interpretazione che l'applicazione dovrebbe togliere di mezzo.
//
//  I TESTI NON SONO SCRITTI QUI. Li calcola commentary.py dentro la build, con
//  le stesse funzioni di formattazione che disegnano i riquadri sopra: e' quello
//  a garantire che un numero citato in una frase e lo stesso numero nella
//  griglia non possano divergere. L'app li legge e basta.
//
//  E NON SONO UN GIUDIZIO SULL'AZIENDA. Il verdetto e' la somma pesata dei
//  rilievi a favore e contro — un conteggio, non un parere. Un modello che non
//  sa niente del prodotto, dei clienti e della concorrenza non puo' produrre
//  altro, e la didascalia lo dice invece di lasciarlo intendere.
//

import SwiftUI

struct CommentaryPanel: View {
    let findings: [Finding]
    var verdict: String? = nil
    var sectorContext: String? = nil

    var body: some View {
        Panel {
            HStack(alignment: .firstTextBaseline) {
                SectionHeader(title: "What these numbers say")
                if let verdict {
                    VerdictTag(text: verdict)
                }
            }

            if findings.isEmpty {
                EmptyNote(text: "Not enough comparable figures for this company "
                          + "to say anything worth reading here.")
            } else {
                ForEach(findings) { finding in
                    HStack(alignment: .top, spacing: 9) {
                        Circle()
                            .fill(finding.tone.color)
                            .frame(width: 7, height: 7)
                            .padding(.top, 6)
                        // Il testo arriva in Markdown (`**grassetto**` sui
                        // numeri): LocalizedStringKey lo interpreta, cosi' gli
                        // asterischi non finiscono a schermo.
                        Text(LocalizedStringKey(finding.text))
                            .font(.subheadline)
                            .foregroundStyle(Palette.ink)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(.vertical, 3)
                }
            }

            if let sectorContext {
                Divider().overlay(Palette.separator)
                Caption(text: "Sector context — \(sectorContext)")
            }

            Caption(text: "Every figure quoted here is the same variable shown "
                    + "in the cards above, formatted by the same function: this "
                    + "is arithmetic on the page's own numbers, not an opinion "
                    + "about the company.")
        }
    }
}

/// Il saldo dei rilievi, con il colore che gli spetta.
struct VerdictTag: View {
    let text: String

    /// L'etichetta e' la prima parte, prima del trattino lungo; il resto e' il
    /// conteggio che la giustifica e sta sotto, piu' piccolo.
    private var parts: (label: String, detail: String?) {
        let split = text.components(separatedBy: " — ")
        return (split.first ?? text, split.count > 1 ? split[1] : nil)
    }

    private var tone: Color {
        let l = parts.label.lowercased()
        if l.contains("bullish") { return Palette.positive }
        if l.contains("bearish") { return Palette.negative }
        if l.contains("mixed")   { return Palette.caution }
        return Palette.inkFaint
    }

    var body: some View {
        VStack(alignment: .trailing, spacing: 1) {
            Text(parts.label)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(tone)
                .padding(.horizontal, 7)
                .padding(.vertical, 3)
                .background(tone.opacity(0.12))
                .clipShape(RoundedRectangle(cornerRadius: 4))
            if let detail = parts.detail {
                Text(detail)
                    .font(.system(size: 9))
                    .foregroundStyle(Palette.inkFaint)
                    .multilineTextAlignment(.trailing)
            }
        }
        .fixedSize(horizontal: false, vertical: true)
    }
}

/// Il giudizio complessivo: la somma dei rilievi di tutte e cinque le sezioni.
///
/// Non e' la media dei cinque verdetti, ed e' una differenza che conta: un
/// rilievo strutturale sul debito deve pesare anche quando le altre quattro
/// sezioni sono tranquille.
struct AssessmentPanel: View {
    let assessment: String
    let counts: (bull: Int, bear: Int, flag: Int)
    /// I rilievi che hanno prodotto il punteggio, sezione per sezione.
    var breakdown: [(section: String, findings: [Finding])] = []

    @State private var expanded = false

    var body: some View {
        Panel {
            HStack(alignment: .firstTextBaseline) {
                SectionHeader(title: "Overall assessment")
                VerdictTag(text: assessment)
            }
            HStack(spacing: 16) {
                tally("in favour", counts.bull, Palette.positive)
                tally("against", counts.bear, Palette.negative)
                tally("data flags", counts.flag, Palette.caution)
                Spacer()
            }

            // IL TESTO LUNGO STA DIETRO UN DROPDOWN. La cautela va detta, ma
            // ripetuta a tutta larghezza in cima a ogni scheda diventa il
            // blocco piu' grande di una schermata che dovrebbe cominciare con
            // i numeri. Qui resta una riga, e chi vuole capire come si forma il
            // punteggio apre e trova i rilievi che lo compongono.
            DisclosureGroup(isExpanded: $expanded) {
                VStack(alignment: .leading, spacing: 14) {
                    ForEach(Array(breakdown.enumerated()), id: \.offset) { _, group in
                        VStack(alignment: .leading, spacing: 5) {
                            Text(Self.sectionName(group.section).uppercased())
                                .font(.system(size: 9, weight: .semibold))
                                .tracking(0.6)
                                .foregroundStyle(Palette.inkFaint)
                            ForEach(group.findings) { finding in
                                HStack(alignment: .top, spacing: 8) {
                                    Circle().fill(finding.tone.color)
                                        .frame(width: 6, height: 6).padding(.top, 5)
                                    Text(LocalizedStringKey(finding.text))
                                        .font(.caption)
                                        .foregroundStyle(Palette.inkMuted)
                                        .fixedSize(horizontal: false, vertical: true)
                                    Spacer(minLength: 4)
                                    Text("\(finding.weight)")
                                        .font(.system(size: 9, design: .monospaced))
                                        .foregroundStyle(Palette.inkFaint)
                                }
                            }
                        }
                    }

                    Divider().overlay(Palette.separator)
                    Text("The number on the right is the weight: 1 is a detail, "
                         + "3 a structural fact. Only the balance uses it — in "
                         + "the text every finding carries the same standing.")
                        .font(.caption2)
                        .foregroundStyle(Palette.inkFaint)
                        .fixedSize(horizontal: false, vertical: true)
                    Text("A count, not a verdict. A model that knows nothing "
                         + "about the product, the customers, the competition or "
                         + "the price paid cannot produce anything more — "
                         + "claiming otherwise would be the dishonest part.")
                        .font(.caption2)
                        .foregroundStyle(Palette.inkFaint)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.top, 8)
            } label: {
                Text(expanded ? "Hide how this score is built"
                              : "How this score is built")
                    .font(.caption)
                    .foregroundStyle(Palette.accent)
            }
            .tint(Palette.accent)
        }
    }

    private static func sectionName(_ raw: String) -> String {
        switch raw {
        case "income":  return "Income"
        case "balance": return "Balance sheet"
        case "cash":    return "Cash flow"
        case "ratios":  return "Ratios"
        case "growth":  return "Growth"
        default:        return raw
        }
    }

    private func tally(_ label: String, _ n: Int, _ color: Color) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text("\(n)")
                .font(.numberLarge)
                .foregroundStyle(n > 0 ? color : Palette.inkFaint)
            Text(label)
                .font(.system(size: 9))
                .foregroundStyle(Palette.inkFaint)
        }
    }
}
