//
//  MetricsExtra.swift — Le grandezze proprie del modello di Lynch.
//
//  PERCHE' NON SONO IN Metrics.swift. Quel file e' generato dal dizionario
//  METRICS di app.py, che raccoglie le voci mostrate come `st.metric`: i
//  multipli, i margini, le voci di bilancio. Il fair value, il rapporto di
//  Lynch, la volatilita' degli utili e il tasso di crescita che determina il
//  multiplo nella dashboard non passano di li' — sono disegnati a mano dentro
//  le rispettive schede, con la spiegazione nel testo intorno.
//
//  Su un telefono quel testo intorno non c'e' sempre: la scheda e' stretta e
//  molto di quel discorso diventa una didascalia sotto il riquadro. Quindi
//  queste voci hanno la loro spiegazione qui, scritta a mano, con la stessa
//  struttura delle altre — che cosa e', come si ottiene, come si legge — cosi'
//  che toccando un numero si ottenga una risposta anche quando quel numero
//  arriva dal modello e non dal bilancio.
//

import Foundation

extension Metrics {

    static let extra: [String: MetricSpec] = [

        "current_price": MetricSpec(
            label: "Price", kind: .usd, better: nil, comparable: false,
            group: "market",
            help: "**What it is** — the last close on file for this ticker.\n\n"
                + "**Where it comes from** — the price provider used by the "
                + "nightly build, the same series that draws the chart above.\n\n"
                + "**How to read it** — it is not a live quote. The dataset is "
                + "rebuilt once a night, and every figure on this page is "
                + "consistent with this price rather than with the market right "
                + "now."),

        "fair_value_pe15": MetricSpec(
            label: "Fair value · P/E 15", kind: .usd, better: nil,
            comparable: false, group: "valuation",
            help: "**What it is** — what one share would cost at a flat "
                + "fifteen times its trailing earnings.\n\n"
                + "**Formula** — trailing-twelve-month EPS × 15.\n\n"
                + "**How to read it** — this is Lynch's *earnings line* from "
                + "*One Up on Wall Street*: one yardstick for every company, "
                + "which is what makes it useful for scanning hundreds of "
                + "them. Its limit is the same thing: it penalises fast "
                + "growers and flatters slow ones."),

        "fair_value_peg": MetricSpec(
            label: "Fair value · category", kind: .usd, better: nil,
            comparable: false, group: "valuation",
            help: "**What it is** — the fair value built on the anchor that "
                + "belongs to this company's category.\n\n"
                + "**Formula** — base × multiple, where both depend on the "
                + "category: earnings × a growth-derived P/E for most, book "
                + "value × 1 for an asset play, normalized earnings × a "
                + "recovery multiple for a turnaround.\n\n"
                + "**How to read it** — the principle is PEG = 1: the fair P/E "
                + "tends towards the growth rate. Where it disagrees sharply "
                + "with the flat P/E 15 line, the disagreement is the "
                + "information."),

        "lynch_ratio": MetricSpec(
            label: "Lynch ratio", kind: .ratio, better: .low, comparable: true,
            group: "valuation",
            help: "**What it is** — how far the price sits from the fair "
                + "value, as a single number.\n\n"
                + "**Formula** — price ÷ fair value.\n\n"
                + "**How to read it** — below 1 the stock trades at a "
                + "discount to the model, above 1 at a premium. It says "
                + "nothing about whether the model is right for this company: "
                + "check the category and its confidence first."),

        "lynch_fair_pe": MetricSpec(
            label: "Fair P/E", kind: .ratio, better: nil, comparable: false,
            group: "valuation",
            help: "**What it is** — the multiple the category assigns to this "
                + "company's earnings.\n\n"
                + "**Where it comes from** — the growth rate, capped and "
                + "floored by category: a fast grower is capped at 25, a slow "
                + "grower sits between 6 and 12 with the dividend added, a "
                + "cyclical gets a flat 12 on mid-cycle earnings.\n\n"
                + "**How to read it** — compare it with the actual P/E. The "
                + "gap between the two is the whole valuation call."),

        "eps_normalized_3y": MetricSpec(
            label: "Normalized EPS", kind: .usd, better: .high,
            comparable: false, group: "valuation",
            help: "**What it is** — earnings per share with the single "
                + "exceptional period taken out.\n\n"
                + "**Formula** — the median of trailing EPS over three "
                + "years.\n\n"
                + "**How to read it** — on a cyclical or a company coming out "
                + "of losses, capitalising the last twelve months means "
                + "capitalising the top or the bottom of a cycle. The median "
                + "is what the business earns in a normal year."),

        "eps_vs_normalized_pct": MetricSpec(
            label: "EPS vs normalized", kind: .pct, better: nil,
            comparable: false, group: "valuation",
            help: "**What it is** — how far the last twelve months sit from "
                + "the three-year normal.\n\n"
                + "**Formula** — (EPS TTM ÷ normalized EPS − 1) × 100.\n\n"
                + "**How to read it** — a large positive figure means the "
                + "current earnings are unusually good, and a fair value built "
                + "on them is unusually generous. A large negative figure is "
                + "the same warning upside down."),

        "earnings_volatility": MetricSpec(
            label: "Earnings volatility", kind: .ratio, better: .low,
            comparable: true, group: "valuation",
            help: "**What it is** — how erratic the earnings series is.\n\n"
                + "**Formula** — the median absolute deviation of year-on-year "
                + "EPS changes, with the denominator floored and the changes "
                + "clipped at ±200%.\n\n"
                + "**How to read it** — above 50 the company is treated as "
                + "cyclical and valued on mid-cycle earnings. The robust "
                + "measure is deliberate: with a standard deviation the 2020 "
                + "collapse alone made retail chains look more cyclical than "
                + "carmakers."),

        "growth_5y_cagr": MetricSpec(
            label: "Growth used", kind: .pct, better: .high, comparable: true,
            group: "growth",
            help: "**What it is** — the growth rate that sets the fair "
                + "multiple. It is not a detail: it is the multiplicand.\n\n"
                + "**Where it comes from** — a ladder, most reliable first: "
                + "the 5-year trend (a least-squares regression on log EPS, "
                + "which uses every point in the window), then the 5-year "
                + "CAGR, then the same two over three years.\n\n"
                + "**How to read it** — check the basis stated next to it. A "
                + "CAGR between two endpoints hands the verdict to the "
                + "starting quarter."),

        "growth_5y_trend": MetricSpec(
            label: "5-year trend", kind: .pct, better: .high, comparable: false,
            group: "growth",
            help: "**What it is** — the growth rate implied by a regression "
                + "through the whole earnings series.\n\n"
                + "**Formula** — least squares on log(EPS) over five years.\n\n"
                + "**How to read it** — the preferred measure, because it uses "
                + "all the points rather than two. On Apple it gives 6.5% "
                + "where the endpoint CAGR gives 13.1%, and the filed years "
                + "say 7.4%."),

        "growth_5y_cagr_raw": MetricSpec(
            label: "5-year CAGR", kind: .pct, better: .high, comparable: false,
            group: "growth",
            help: "**What it is** — compound growth between the first and the "
                + "last point of the window.\n\n"
                + "**Formula** — (end ÷ start)^(1/years) − 1.\n\n"
                + "**How to read it** — two numbers decide it, so a bad "
                + "starting quarter inflates it and a good one buries it. If "
                + "the window contains losses, much of the figure is rebound "
                + "rather than trend, and the multiple is capped accordingly."),

        "beta": MetricSpec(
            label: "Beta", kind: .ratio, better: nil, comparable: true,
            group: "market",
            help: "**What it is** — how much the share price has moved "
                + "relative to the market.\n\n"
                + "**How to read it** — above 1 it has swung more than the "
                + "index, below 1 less. It measures past volatility, not risk "
                + "of loss: a stable business bought far too dear has a low "
                + "beta and a high chance of disappointing."),
    ]
}
