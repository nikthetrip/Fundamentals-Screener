//
//  ModelExplanation.swift — Che differenza c'e' fra i due modelli.
//
//  PERCHE' SERVE UN FOGLIO INTERO. Il selettore in cima allo screener sceglie
//  fra due fair value, e il sottotitolo di una riga non basta a dire in cosa
//  differiscono: la stessa societa' puo' comparire a sconto del 63% in uno e
//  del 75% nell'altro, e senza sapere perche' quella differenza sembra un
//  errore dell'applicazione invece che il suo contenuto.
//
//  Il testo viene dallo stesso posto della dashboard — il riquadro «Lynch
//  category definitions» dello screener e il README — perche' due spiegazioni
//  della stessa cosa scritte in due momenti diversi finiscono per dire cose
//  diverse.
//

import SwiftUI

struct ModelExplanation: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    intro
                    modelOne
                    modelTwo
                    categories
                    pegPrinciple
                    reading
                }
                .padding(20)
            }
            .background(Palette.background)
            .navigationTitle("The two models")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }.fontWeight(.semibold)
                }
            }
        }
    }

    // -----------------------------------------------------------------------

    private var intro: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Same 990 stocks, two yardsticks")
                .font(.displaySmall)
                .foregroundStyle(Palette.ink)
            Text("The switch at the top of the screener does not change which "
                 + "companies you see — it changes what each one is measured "
                 + "against. Lynch used two different tools, and they disagree "
                 + "on purpose.")
                .font(.subheadline)
                .foregroundStyle(Palette.inkMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var modelOne: some View {
        Panel {
            SectionHeader(title: "Flat P/E 15", note: "The same multiple for every company")
            formula("fair value  =  TTM EPS  ×  15")
            Text("The *earnings line* from **One Up on Wall Street**: one "
                 + "yardstick applied to every company, which is exactly what "
                 + "makes it useful for scanning hundreds of them at once.")
                .font(.subheadline)
                .foregroundStyle(Palette.ink)
                .fixedSize(horizontal: false, vertical: true)
            Caption(text: "Its limit is the same thing: it penalises companies "
                    + "growing at 30% and flatters those growing at 3%, because "
                    + "it refuses to know the difference.")
        }
    }

    private var modelTwo: some View {
        Panel {
            SectionHeader(title: "Lynch P/E",
                          note: "The multiple its category earns")
            formula("fair value  =  base  ×  category multiple")
            Text("Both the base and the multiple depend on what kind of "
                 + "company it is. A fast grower is capitalised on its own "
                 + "growth rate; a cyclical on mid-cycle earnings; an asset "
                 + "play is not capitalised at all — it is worth its balance "
                 + "sheet.")
                .font(.subheadline)
                .foregroundStyle(Palette.ink)
                .fixedSize(horizontal: false, vertical: true)
            Caption(text: "This is why the same stock can show a 63% discount "
                    + "under one model and 75% under the other. The gap is the "
                    + "information: when the two agree the signal is robust, "
                    + "and when they diverge by more than 35% the Valuation "
                    + "tab says so.")
        }
    }

    private var categories: some View {
        Panel {
            SectionHeader(title: "The categories, in the order they are tested",
                          note: "First match wins")
            Caption(text: "The conditions that invalidate a P/E are checked "
                    + "before the growth bands. A loss-making company growing "
                    + "40% is not a fast grower — that 40% is a rebound.")

            ForEach(Array(Self.rules.enumerated()), id: \.offset) { index, rule in
                Divider().overlay(Palette.separator)
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 7) {
                        Text("\(index + 1)")
                            .font(.system(size: 10, weight: .bold, design: .monospaced))
                            .foregroundStyle(Palette.background)
                            .frame(width: 17, height: 17)
                            .background(LynchCategory.color(rule.name))
                            .clipShape(Circle())
                        Text(rule.name)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(Palette.ink)
                    }
                    Text(rule.test)
                        .font(.caption)
                        .foregroundStyle(Palette.inkMuted)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(rule.anchor)
                        .font(.caption.weight(.medium))
                        .foregroundStyle(Palette.accent)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.vertical, 3)
            }
        }
    }

    private var pegPrinciple: some View {
        Panel {
            SectionHeader(title: "Why PEG = 1")
            Text("For the growth-based categories the fair P/E tracks the "
                 + "company's own growth rate — so it varies stock by stock "
                 + "even inside a single category. Two stalwarts growing 12% "
                 + "and 18% do not get the same multiple.")
                .font(.subheadline)
                .foregroundStyle(Palette.ink)
                .fixedSize(horizontal: false, vertical: true)
            Caption(text: "The growth figure comes from a ladder — 5-year "
                    + "log-linear trend first, then the 5-year CAGR, then the "
                    + "3-year windows — because a multiple set by two endpoints "
                    + "is a multiple set by two quarters.")
        }
    }

    private var reading: some View {
        Panel {
            SectionHeader(title: "How to read the two together")
            bullet(Palette.positive, "They agree",
                   "The signal is robust: the valuation does not depend on "
                   + "which tool you picked.")
            bullet(Palette.caution, "They diverge past 35%",
                   "Usually the stock sits in a category where a flat P/E 15 "
                   + "makes no sense, or its earnings contain one-off items.")
            bullet(Palette.inkFaint, "One of them is blank",
                   "The category has no valuation anchor — a company with no "
                   + "positive earnings, current or normalized, cannot be "
                   + "capitalised, and inventing a number would be worse than "
                   + "showing none.")
            Divider().overlay(Palette.separator)
            Caption(text: "Every classification carries a confidence — high, "
                    + "medium or low — with the reason in plain words: how the "
                    + "growth was measured, how erratic the earnings are, "
                    + "whether the window contained losses.")
        }
    }

    // -----------------------------------------------------------------------

    private func formula(_ text: String) -> some View {
        Text(text)
            .font(.system(.footnote, design: .monospaced).weight(.medium))
            .foregroundStyle(Palette.accent)
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Palette.panel)
            .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private func bullet(_ color: Color, _ title: String, _ body: String) -> some View {
        HStack(alignment: .top, spacing: 9) {
            Circle().fill(color).frame(width: 7, height: 7).padding(.top, 6)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Palette.ink)
                Text(body)
                    .font(.caption)
                    .foregroundStyle(Palette.inkMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.vertical, 3)
    }

    /// Le regole, nell'ordine in cui vengono verificate. Sono le stesse del
    /// riquadro «Lynch category definitions» della dashboard.
    private static let rules: [(name: String, test: String, anchor: String)] = [
        ("Turnaround",
         "Loss-making now, or back to profit less than a year ago, or more than one distinct loss episode in five years with the last one still recent.",
         "P/E 10 on 3-year normalized earnings — a recovery multiple, not a PEG multiple."),
        ("Asset Play",
         "Price-to-book below 1.",
         "Book value per share, i.e. a fair P/B of 1. The P/E is not the tool here: the balance sheet is the floor."),
        ("Cyclical",
         "A structurally cyclical sector — energy, materials, industrials, consumer cyclical, real estate — and erratic earnings.",
         "P/E 12 on mid-cycle earnings, because a low P/E at the top of a cycle is a warning, not a bargain."),
        ("Fast Grower",
         "Growth above 20%, measured on a window with no losses.",
         "Fair P/E = the growth rate, capped at 25. Measured across a loss it becomes Stalwart (recovering), held at 15."),
        ("Stalwart",
         "Growth between 10% and 20%. Split by size into large, mid cap and size unknown.",
         "Fair P/E = the growth rate."),
        ("Slow Grower",
         "Growth below 10%. With negative growth it becomes Slow Grower (declining earnings).",
         "Fair P/E = growth + dividend yield, floored at 6 and capped at 12. When growth is negative it contributes zero — no credit for shrinking — and the multiple applies to normalized earnings."),
        ("Unclassified",
         "Growth not calculable on any of the four windows tried.",
         "No category multiple: the flat P/E 15 line is the only reading available."),
    ]
}
