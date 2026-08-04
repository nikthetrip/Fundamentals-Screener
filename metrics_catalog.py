#!/usr/bin/env python3
"""
metrics_catalog.py — Il catalogo delle metriche, i formati e i confronti.

PERCHE' STA IN UN MODULO SUO. Questo materiale viveva dentro app.py, e finche'
l'unico lettore del dataset era la dashboard andava bene. Ora i lettori sono
tre: la dashboard, il generatore che porta le spiegazioni dentro l'app iOS, e
la build che calcola i commenti di valutazione. Tenerlo dentro app.py
significherebbe che gli altri due devono o importare un modulo che all'import
apre Streamlit e carica i dataset, oppure ricopiarne dei pezzi — e un catalogo
ricopiato diverge alla prima correzione.

COSA C'E' DENTRO, in tre gruppi:

  METRICS         sessanta voci: etichetta, tipo, verso migliore, e la
                  spiegazione in chiaro di che cosa sono e come si leggono.
  i formattatori  la stessa funzione che disegna un numero in un riquadro e
                  quello dentro una frase di commento. E' questo a garantire
                  che i due non possano divergere.
  i confronti     mediane del gruppo di pari, scelta del gruppo, scarto e
                  colore del delta.

MIN_PEERS sta qui perche' e' la soglia sotto la quale una mediana non
significa niente, e vale allo stesso modo ovunque venga usata.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Numero minimo di societa' perche' una mediana di gruppo abbia senso.
MIN_PEERS = 5

# IL COSTO DEL CAPITALE NON E' PIU' UNA COSTANTE.
#
# Qui viveva `COST_OF_CAPITAL = 9.0`, una soglia fissa uguale per tutte le
# societa'. Il suo difetto non era l'imprecisione ma la DIREZIONE dell'errore:
# con un beta medio di 0,62 le utility hanno un costo del capitale intorno al
# 6% e venivano marchiate come distruttrici di valore rendendo l'8%; le
# tecnologiche, beta 1,35, superano l'11% ed erano promosse rendendo il 10%.
# Un errore costante per interi settori e' peggio del rumore.
#
# Ora `derived_metrics.add_cost_of_capital` calcola un WACC per societa' dal
# suo beta, dal tasso privo di rischio del giorno e dagli interessi che paga
# davvero — e dove non si puo' calcolare, il rilievo tace invece di ipotizzare.
# La colonna si chiama `wacc_pct` ed e' fra le metriche qui sotto.


# ===========================================================================
# METRIC DEFINITIONS — one place for label, formula, formatting and polarity
# ===========================================================================

# 'better': "high" = a higher value is better, "low" = lower is better,
#           None   = neither (no colour, no peer comparison judgement).
# 'peer'  : False disables the comparison against the peer group. Set on every
#           ABSOLUTE amount — market cap, revenue, net income, free cash flow,
#           debt. Saying a company's revenue is "+30% vs peers" only says it is
#           bigger, and size is not a quality: it is the same information the
#           user explicitly did not want next to market cap. What does compare
#           meaningfully is the ratios built from those amounts (margins, FCF
#           yield, debt/equity), which keep their delta.
# 'kind'  : how to format, and how to express the gap against the peer group.
#           A percentage is compared in PERCENTAGE POINTS (a ROE of 12% vs an
#           industry at 9% is "+3 pp", not "+33%"), a ratio in relative terms
#           (a P/E of 20 vs an industry at 25 is "-20%"). Mixing the two is the
#           quickest way to make a comparison table lie.
#           money = compact currency (12.9 B$) · pct = percentage · ratio = plain
#           number · usd = a PER-SHARE amount, which is money but must never be
#           abbreviated ("$19.82", never "20 $") · count = a quantity of things,
#           shares in particular, scaled but with no currency symbol.
# 'group' : which statement the figure belongs to — market · income · balance ·
#           cash · returns · growth. It is what drives the layout of the
#           Financials tab: sections are generated from this, so adding a metric
#           here makes it appear in the right place with no further edit.
#
# ADDING A METRIC. Put it in this dictionary with a label, a kind, a polarity
# and a 'help' written in the three-part shape used throughout — what it is,
# the formula, how to read it — then list its key in the relevant section of
# the Financials tab. Everything else (formatting, peer median, delta, colour)
# follows automatically.
METRICS: dict[str, dict] = {

    # ------------------------------------------------------------------ #
    # PRICE — what the market is charging                                 #
    # ------------------------------------------------------------------ #
    "market_cap": dict(
        label="Market cap", kind="money", better=None, peer=False,
        group="market",
        help="**What it is** — the price tag on all the shares put together.\n\n"
             "**Formula** — share price × shares outstanding.\n\n"
             "**How to read it** — size, nothing more. It is not compared with "
             "the peer group because being bigger is not the same as being "
             "better."),
    "enterprise_value": dict(
        label="Enterprise value", kind="money", better=None, peer=False,
        group="market",
        help="**What it is** — what the whole business costs, not just its "
             "shares: buying the equity also means taking on the debt and "
             "getting the cash.\n\n"
             "**Formula** — market cap + total debt − cash.\n\n"
             "**How to read it** — two companies with the same market cap do "
             "not cost the same if one carries debt and the other is sitting "
             "on cash."),
    "pe_ratio": dict(
        label="P/E (trailing)", kind="ratio", better="low", group="market",
        help="**What it is** — how many years of current earnings you are "
             "paying for one share.\n\n"
             "**Formula** — price ÷ trailing-twelve-month EPS.\n\n"
             "**How to read it** — lower is cheaper, but only against the same "
             "kind of company: 30 is expensive for a utility and cheap for "
             "software. Recomputed here from the EPS shown on this page, never "
             "copied from the provider, so it always matches the rest."),
    "forward_pe": dict(
        label="P/E (forward)", kind="ratio", better="low", group="market",
        help="**What it is** — the same ratio, but on the earnings analysts "
             "*expect* over the next twelve months.\n\n"
             "**Formula** — price ÷ consensus next-twelve-month EPS.\n\n"
             "**How to read it** — the only forward-looking number on this "
             "page. Well below the trailing P/E means the market is pricing in "
             "a jump in profits; it is an expectation, not a filed figure."),
    "earnings_yield_pct": dict(
        label="Earnings yield", kind="pct", better="high", group="market",
        help="**What it is** — the P/E turned upside down, so it can be "
             "compared with a bond yield.\n\n"
             "**Formula** — 100 ÷ P/E, i.e. EPS ÷ price.\n\n"
             "**How to read it** — the profit the business earns each year for "
             "every $100 of share price. Under the yield of a government bond, "
             "you are paying for growth you have not seen yet."),
    "ps_ratio": dict(
        label="P/S", kind="ratio", better="low", group="market",
        help="**What it is** — the price paid for each unit of sales.\n\n"
             "**Formula** — market cap ÷ trailing-twelve-month revenue.\n\n"
             "**How to read it** — the one multiple that still works when "
             "profits are temporarily depressed or negative. Only comparable "
             "between companies with similar margins: 1× sales is dear for a "
             "supermarket and cheap for software."),
    "pfcf_ratio": dict(
        label="P/FCF", kind="ratio", better="low", group="market",
        help="**What it is** — the P/E's cash-based twin: the price paid for "
             "each unit of cash the business actually produces.\n\n"
             "**Formula** — market cap ÷ trailing-twelve-month free cash "
             "flow.\n\n"
             "**How to read it** — much higher than the P/E means the reported "
             "profit is not turning into cash — the gap is where accounting "
             "choices hide."),
    "price_to_book": dict(
        label="P/B", kind="ratio", better="low", group="market",
        help="**What it is** — the price paid for each unit of accounting net "
             "worth.\n\n"
             "**Formula** — price ÷ book value per share.\n\n"
             "**How to read it** — below 1 the market values the company at "
             "less than its own balance sheet, which is what Lynch called an "
             "*asset play*. Meaningful for banks and industrials, close to "
             "useless where the real assets are brands and people."),
    "ev_to_ebit": dict(
        label="EV / EBIT", kind="ratio", better="low", group="market",
        help="**What it is** — the debt-aware P/E: the price of the whole "
             "business against what the business earns before financing.\n\n"
             "**Formula** — enterprise value ÷ trailing-twelve-month operating "
             "income.\n\n"
             "**How to read it** — the fairest way to compare two companies "
             "with very different debt loads, because both the numerator and "
             "the denominator ignore who financed what."),
    "peg_ratio": dict(
        label="PEG", kind="ratio", better="low", group="market",
        help="**What it is** — the P/E measured against the growth that is "
             "supposed to justify it.\n\n"
             "**Formula** — P/E ÷ 5-year earnings growth rate.\n\n"
             "**How to read it** — below 1 cheap for the growth on offer, "
             "above 2 expensive. Meaningless when growth is negative, and "
             "therefore left blank."),
    "fcf_yield_pct": dict(
        label="FCF yield", kind="pct", better="high", group="market",
        help="**What it is** — the cash return the business throws off at "
             "today's price, the way a rental yield works for a flat.\n\n"
             "**Formula** — free cash flow (TTM) ÷ market cap.\n\n"
             "**How to read it** — higher is cheaper. It is the honest "
             "counterpart of the earnings yield, because cash cannot be "
             "smoothed by accounting choices."),
    "dividend_yield": dict(
        label="Dividend yield", kind="pct", better="high", group="market",
        help="**What it is** — the part of the return that is paid to you in "
             "cash rather than left inside the company, at the rate the "
             "company is paying **now**.\n\n"
             "**Formula** — the last regular dividend × how many times a year "
             "it is paid, ÷ price. Rebuilt from the actual payment history, "
             "not taken from the provider's field: that field keeps the old "
             "figure for a year after a cut, and swallows one-off special "
             "dividends as if they came back every year.\n\n"
             "**How to read it** — this is the forward-looking rate: a cut "
             "shows up the day it happens. Read it together with the payout "
             "ratio — a high yield paid out of an unsustainable share of "
             "profits is a cut waiting to happen — and with the trailing "
             "figure next to it, which is what was actually paid."),
    "dividend_ttm_yield_pct": dict(
        label="Dividend paid (12m)", kind="pct", better="high", group="market",
        help="**What it is** — everything the company actually distributed "
             "over the last twelve months, special dividends included.\n\n"
             "**Formula** — sum of the dividends paid in the last 365 days ÷ "
             "price.\n\n"
             "**How to read it** — the counterpart of the yield next to it. "
             "Much **higher** means a special dividend was paid, or the "
             "dividend has just been cut; much **lower** means it has just "
             "been raised or reinstated. Specials are excluded from the "
             "headline yield on purpose: by definition they do not repeat."),
    "dividend_rate": dict(
        label="Dividend / share", kind="usd", better="high", peer=False,
        group="market",
        help="**What it is** — the annual dividend one share is entitled to at "
             "the current rate.\n\n"
             "**Formula** — the last regular payment × the number of payments "
             "per year.\n\n"
             "**How to read it** — the number the yield is built from: "
             "dividend ÷ price = yield. Compare it with EPS and with free cash "
             "flow per share to see how much room the payout has."),
    "payout_ratio_pct": dict(
        label="Payout ratio", kind="pct", better=None, group="market",
        help="**What it is** — the slice of profit handed to shareholders as "
             "dividends instead of being reinvested.\n\n"
             "**Formula** — dividends paid (dividend yield × market cap) ÷ net "
             "income (TTM).\n\n"
             "**How to read it** — under 60% comfortable, over 100% the "
             "company is paying more than it earns and is funding the "
             "dividend from cash or debt. Neither high nor low is "
             "intrinsically good: a growing company that pays nothing is doing "
             "the right thing. Blank when there are no profits to share."),
    "beta": dict(
        label="Beta", kind="ratio", better=None, group="market",
        help="**What it is** — how violently the stock moves compared with the "
             "market as a whole.\n\n"
             "**Formula** — regression of the stock's returns on the market's "
             "(provider data).\n\n"
             "**How to read it** — 1 means it moves with the index, 1.5 that "
             "it exaggerates every move by half, below 1 that it is calmer. It "
             "measures volatility, not risk of loss."),

    # ------------------------------------------------------------------ #
    # INCOME STATEMENT — what the company sells and keeps                 #
    # ------------------------------------------------------------------ #
    "revenue_ttm": dict(
        label="Revenue (TTM)", kind="money", better="high", peer=False,
        group="income",
        help="**What it is** — everything the company billed its customers in "
             "the last twelve months.\n\n"
             "**Formula** — the last four discrete quarters filed with the "
             "SEC, added up.\n\n"
             "**How to read it** — the top line: every margin below is a "
             "fraction of this number. Not compared with peers — size is not a "
             "quality."),
    "revenue_latest_fy": dict(
        label="Revenue (latest FY)", kind="money", better="high", peer=False,
        group="income",
        help="**What it is** — revenue for the most recent *complete* fiscal "
             "year, as it appears in the annual report.\n\n"
             "**Formula** — straight from the 10-K income statement.\n\n"
             "**How to read it** — the audited figure. It differs from the TTM "
             "number by however many quarters have been filed since the year "
             "ended."),
    "ebit_ttm": dict(
        label="EBIT (TTM)", kind="money", better="high", peer=False,
        group="income",
        help="**What it is** — the profit the business makes from operating, "
             "before interest and taxes get their share.\n\n"
             "**Formula** — operating income over the last four quarters; "
             "pre-tax income where EBIT is not tagged separately (banks).\n\n"
             "**How to read it** — the cleanest measure of the trading "
             "performance, because it is not affected by how the company is "
             "financed or where it pays tax."),
    "net_income_ttm": dict(
        label="Net income (TTM)", kind="money", better="high", peer=False,
        group="income",
        help="**What it is** — the bottom line: what is left for shareholders "
             "after every cost, interest and tax.\n\n"
             "**Formula** — the last four discrete quarters filed with the "
             "SEC, added up.\n\n"
             "**How to read it** — the numerator of the P/E. It is also the "
             "figure most exposed to one-off items, which is why the page also "
             "shows a normalized version."),
    "eps_ttm": dict(
        label="EPS (TTM)", kind="usd", better="high", peer=False,
        group="income",
        help="**What it is** — the profit attributable to one single share.\n\n"
             "**Formula** — four discrete quarterly EPS figures added up, "
             "restated for stock splits, after arbitration between the SEC "
             "filings and the provider.\n\n"
             "**How to read it** — the number this whole page is built on: the "
             "fair value multiplies it, the P/E divides by it."),
    "operating_margin_pct": dict(
        label="Operating margin", kind="pct", better="high", group="income",
        help="**What it is** — how many cents of each dollar of sales survive "
             "the cost of running the business.\n\n"
             "**Formula** — EBIT ÷ revenue, both trailing twelve months.\n\n"
             "**How to read it** — the best single indicator of pricing power. "
             "Compare it only within an industry, and watch its direction over "
             "the years more than its level."),
    "net_margin_pct": dict(
        label="Net margin", kind="pct", better="high", group="income",
        help="**What it is** — how many cents of each dollar of sales end up as "
             "profit for the shareholder.\n\n"
             "**Formula** — net income ÷ revenue, both trailing twelve "
             "months.\n\n"
             "**How to read it** — the operating margin after interest and "
             "tax. A wide gap between the two says the debt or the tax bill is "
             "eating the business's own performance."),
    "revenue_growth_yoy_pct": dict(
        label="Revenue growth YoY", kind="pct", better="high", group="income",
        help="**What it is** — how much bigger the top line is than a year "
             "ago.\n\n"
             "**Formula** — trailing-twelve-month revenue against the point "
             "closest to twelve months earlier (compared by date, not by "
             "position in the series).\n\n"
             "**How to read it** — the fastest-reacting growth figure on the "
             "page, and the noisiest: one strong quarter moves it."),
    "eps_growth_yoy": dict(
        label="EPS growth YoY", kind="pct", better="high", group="income",
        help="**What it is** — how much more the company earns per share than "
             "a year ago.\n\n"
             "**Formula** — trailing-twelve-month EPS against the point "
             "closest to twelve months earlier.\n\n"
             "**How to read it** — persistently faster than revenue growth "
             "means margins are widening or the share count is shrinking; "
             "persistently slower means the opposite."),
    "revenue_per_share": dict(
        label="Revenue / share", kind="usd", better="high", peer=False,
        group="income",
        help="**What it is** — the sales that belong to one share.\n\n"
             "**Formula** — trailing-twelve-month revenue ÷ shares "
             "outstanding.\n\n"
             "**How to read it** — the per-share view is the only one that "
             "sees dilution: revenue that grows while the share count grows "
             "faster leaves the individual shareholder with less."),
    "shares_outstanding": dict(
        label="Shares outstanding", kind="count", better="low", peer=False,
        group="income",
        help="**What it is** — how many slices the company is cut into.\n\n"
             "**Formula** — the latest share count from the provider.\n\n"
             "**How to read it** — falling over the years means buybacks are "
             "concentrating the profit on fewer shares; rising means dilution, "
             "which quietly eats the growth of every per-share figure."),

    # ------------------------------------------------------------------ #
    # BALANCE SHEET — what it owns and owes                               #
    # ------------------------------------------------------------------ #
    "assets": dict(
        label="Total assets", kind="money", better=None, peer=False,
        group="balance",
        help="**What it is** — everything the company owns, at the value "
             "carried in its books.\n\n"
             "**Formula** — the latest filed balance sheet.\n\n"
             "**How to read it** — on its own it says only how capital-heavy "
             "the business is. What matters is how much of it is financed by "
             "the owners (equity ratio) and what it earns (earning power)."),
    "equity": dict(
        label="Shareholders' equity", kind="money", better="high", peer=False,
        group="balance",
        help="**What it is** — the part of the assets that belongs to the "
             "shareholders once every creditor has been paid.\n\n"
             "**Formula** — total assets − total liabilities, from the latest "
             "filed balance sheet.\n\n"
             "**How to read it** — the accounting floor under the share price, "
             "and the denominator of ROE. Negative equity is not automatically "
             "fatal, but it makes ROE and debt/equity unreadable."),
    "cash": dict(
        label="Cash & equivalents", kind="money", better="high", peer=False,
        group="balance",
        help="**What it is** — money available immediately, plus what can be "
             "turned into money within days.\n\n"
             "**Formula** — the cash line of the latest filed balance "
             "sheet.\n\n"
             "**How to read it** — the buffer that lets a company survive a "
             "bad year without asking anyone's permission. Only meaningful "
             "next to the debt it might have to repay."),
    "total_debt": dict(
        label="Total debt", kind="money", better="low", peer=False,
        group="balance",
        help="**What it is** — every dollar the company has borrowed, whenever "
             "it falls due.\n\n"
             "**Formula** — long-term debt + the current portion and "
             "short-term borrowings, from the latest filed balance sheet.\n\n"
             "**How to read it** — this, not the long-term figure, is the "
             "number that matters: modest long-term debt next to a pile of "
             "commercial paper maturing this year is not a low-debt company."),
    "long_term_debt": dict(
        label="Long term debt", kind="money", better="low", peer=False,
        group="balance",
        help="**What it is** — the borrowings that do not fall due within the "
             "year.\n\n"
             "**Formula** — the non-current debt line of the latest filed "
             "balance sheet; where a company only tags debt including current "
             "maturities, that broader figure is used.\n\n"
             "**How to read it** — the patient part of the debt: it does not "
             "create refinancing pressure this year, but it still costs "
             "interest."),
    "net_debt": dict(
        label="Net debt", kind="money", better="low", peer=False,
        group="balance",
        help="**What it is** — the debt that would still be there if the "
             "company spent all its cash repaying borrowings tomorrow.\n\n"
             "**Formula** — total debt − cash & equivalents.\n\n"
             "**How to read it** — negative is a *net cash* position: the "
             "company owes less than it holds."),
    "equity_ratio_pct": dict(
        label="Equity ratio", kind="pct", better="high", group="balance",
        help="**What it is** — how much of the balance sheet is genuinely "
             "owned rather than borrowed.\n\n"
             "**Formula** — shareholders' equity ÷ total assets.\n\n"
             "**How to read it** — higher means more shock absorption. "
             "Structurally low for banks, whose business *is* lending other "
             "people's money — which is why the comparison shown is against "
             "the same industry, never the whole market."),
    "debt_to_equity": dict(
        label="Debt / equity", kind="ratio", better="low", group="balance",
        help="**What it is** — how many dollars the company has borrowed for "
             "every dollar the shareholders put in.\n\n"
             "**Formula** — long-term debt ÷ shareholders' equity.\n\n"
             "**How to read it** — the classic leverage gauge. Above 1 the "
             "lenders have committed more than the owners; what counts as "
             "normal varies enormously by industry."),
    "net_debt_to_equity": dict(
        label="Net debt / equity", kind="ratio", better="low", group="balance",
        help="**What it is** — the same leverage test, but crediting the "
             "company for the cash it is holding.\n\n"
             "**Formula** — net debt ÷ shareholders' equity.\n\n"
             "**How to read it** — the fairer of the two for cash-rich "
             "companies. Negative means net cash: more money in the bank than "
             "borrowings outstanding."),
    "debt_to_assets_pct": dict(
        label="Debt / assets", kind="pct", better="low", group="balance",
        help="**What it is** — the share of everything the company owns that "
             "was paid for with borrowed money.\n\n"
             "**Formula** — total debt ÷ total assets.\n\n"
             "**How to read it** — the mirror image of the equity ratio, and "
             "readable even where equity is distorted by buybacks or "
             "write-downs."),
    "net_debt_to_fcf": dict(
        label="Net debt / FCF", kind="ratio", better="low", group="balance",
        help="**What it is** — how many years of free cash flow it would take "
             "to repay the net debt, if every spare dollar went to the "
             "lenders.\n\n**Formula** — net debt ÷ free cash flow (TTM).\n\n"
             "**How to read it** — the harshest of the debt tests, and the "
             "most honest: interest is paid with cash, not with operating "
             "profit. Under 3 the debt is comfortably serviceable, above 5 it "
             "is running the company. Blank when free cash flow is negative — "
             "a company burning cash cannot repay anything out of it."),
    "cash_to_debt_pct": dict(
        label="Cash / debt", kind="pct", better="high", group="balance",
        help="**What it is** — how much of the debt could be repaid tomorrow "
             "morning out of the money already in the bank.\n\n"
             "**Formula** — cash & equivalents ÷ total debt.\n\n"
             "**How to read it** — above 100% the company holds more cash than "
             "it owes (net cash). Low is not automatically bad, but it means "
             "the debt has to be repaid out of future profits rather than out "
             "of the balance sheet."),
    "net_debt_to_ebit": dict(
        label="Net debt / EBIT", kind="ratio", better="low", group="balance",
        help="**What it is** — how many years of operating profit it would "
             "take to clear the net debt.\n\n"
             "**Formula** — net debt ÷ trailing-twelve-month EBIT.\n\n"
             "**How to read it** — under 3 comfortable, above 4 the balance "
             "sheet starts dictating the strategy. This is the ratio bank "
             "covenants are usually written on."),
    "book_value_per_share": dict(
        label="Book value / share", kind="usd", better="high", peer=False,
        group="balance",
        help="**What it is** — the accounting net worth attached to one "
             "share.\n\n"
             "**Formula** — shareholders' equity ÷ shares outstanding.\n\n"
             "**How to read it** — for a Lynch asset play this, not the P/E, "
             "is the valuation anchor: the price is compared with the balance "
             "sheet rather than with the earnings."),
    "asset_turnover": dict(
        label="Asset turnover", kind="ratio", better="high", group="balance",
        help="**What it is** — how hard the assets are working: the sales "
             "produced by each dollar on the balance sheet.\n\n"
             "**Formula** — revenue (TTM) ÷ total assets.\n\n"
             "**How to read it** — a retailer turns its assets over several "
             "times a year, a utility a fraction of once. Rising over time "
             "means the same asset base is doing more work."),

    # ------------------------------------------------------------------ #
    # CASH FLOW — what actually reaches the bank account                  #
    # ------------------------------------------------------------------ #
    "ocf_ttm": dict(
        label="Operating cash flow (TTM)", kind="money", better="high",
        peer=False, group="cash",
        help="**What it is** — the cash the ordinary business generated, "
             "before deciding what to invest.\n\n"
             "**Formula** — the last four quarters. EDGAR reports cash flow "
             "year-to-date rather than per quarter, so quarters are recovered "
             "by differencing consecutive cumulative figures.\n\n"
             "**How to read it** — profit is an opinion, this is closer to a "
             "fact. It should track net income over the years; a lasting gap "
             "deserves an explanation."),
    "capex_ttm": dict(
        label="Capital expenditure (TTM)", kind="money", better=None,
        peer=False, group="cash",
        help="**What it is** — the cash spent on plant, equipment and other "
             "long-lived assets, shown as a positive outflow.\n\n"
             "**Formula** — operating cash flow − free cash flow, both "
             "trailing twelve months.\n\n"
             "**How to read it** — neither good nor bad in itself: it is the "
             "cost of staying in business, and for a growing company also the "
             "cost of getting bigger. Judge it as a share of revenue."),
    "fcf_ttm": dict(
        label="Free cash flow (TTM)", kind="money", better="high", peer=False,
        group="cash",
        help="**What it is** — the cash left over once the business has paid "
             "for everything it needs to keep running: the money that can pay "
             "dividends, repay debt or buy back shares.\n\n"
             "**Formula** — operating cash flow − capital expenditure, over "
             "the last four quarters.\n\n"
             "**How to read it** — the number a business is ultimately worth "
             "the discounted sum of. Missing for most banks and insurers, "
             "whose cash flow statement has a different shape."),
    "fcf_latest_fy": dict(
        label="FCF (latest FY)", kind="money", better="high", peer=False,
        group="cash",
        help="**What it is** — free cash flow for the most recent *complete* "
             "fiscal year.\n\n"
             "**Formula** — straight from the annual cash flow statement.\n\n"
             "**How to read it** — the audited counterpart of the TTM figure, "
             "and the one to prefer for businesses with heavy seasonality."),
    "fcf_per_share": dict(
        label="FCF / share", kind="usd", better="high", peer=False,
        group="cash",
        help="**What it is** — the free cash flow that belongs to one "
             "share.\n\n"
             "**Formula** — trailing-twelve-month free cash flow ÷ shares "
             "outstanding.\n\n"
             "**How to read it** — the cash equivalent of EPS. Compare it with "
             "the dividend per share to see how much room the payout has."),
    "fcf_margin_pct": dict(
        label="FCF margin", kind="pct", better="high", group="cash",
        help="**What it is** — how many cents of each dollar of sales end up as "
             "genuinely free cash.\n\n"
             "**Formula** — free cash flow ÷ revenue, both trailing twelve "
             "months.\n\n"
             "**How to read it** — the single hardest margin to manipulate. "
             "Above 15% is a business that finances itself; near zero means "
             "growth has to be paid for with debt or new shares."),
    "ocf_margin_pct": dict(
        label="OCF margin", kind="pct", better="high", group="cash",
        help="**What it is** — the share of sales that turns into operating "
             "cash before any investment.\n\n"
             "**Formula** — operating cash flow ÷ revenue, both trailing "
             "twelve months.\n\n"
             "**How to read it** — read next to the FCF margin: the distance "
             "between the two is exactly how capital-hungry the business is."),
    "fcf_conversion_pct": dict(
        label="FCF conversion", kind="pct", better="high", group="cash",
        help="**What it is** — how much of the accounting profit actually "
             "shows up as cash.\n\n"
             "**Formula** — free cash flow ÷ net income, both trailing twelve "
             "months.\n\n"
             "**How to read it** — around 100% the earnings are real. "
             "Persistently below 60% means profit is being tied up in working "
             "capital or capex; above 100% is common where depreciation "
             "exceeds the cash actually being reinvested. Blank when there is "
             "no profit to convert."),
    "capex_to_revenue_pct": dict(
        label="Capex / revenue", kind="pct", better=None, group="cash",
        help="**What it is** — how much of every dollar of sales has to be "
             "ploughed back into assets.\n\n"
             "**Formula** — capital expenditure ÷ revenue, both trailing "
             "twelve months.\n\n"
             "**How to read it** — low means an asset-light business that can "
             "grow without swallowing cash; high is normal for utilities, "
             "telecoms and heavy industry. Compare only within an industry."),
    "fcf_growth_yoy_pct": dict(
        label="FCF growth YoY", kind="pct", better="high", group="cash",
        help="**What it is** — how much more (or less) free cash the business "
             "produced than in the previous year.\n\n"
             "**Formula** — the latest full fiscal year against the one "
             "before; falls back to the trailing-twelve-month series when only "
             "one fiscal year is on file.\n\n"
             "**How to read it** — the most volatile growth figure on the "
             "page: one large investment year is enough to turn it negative "
             "without anything being wrong."),

    # ------------------------------------------------------------------ #
    # RETURNS — how well the capital is being used                        #
    # ------------------------------------------------------------------ #
    "roe_pct": dict(
        label="ROE", kind="pct", better="high", group="returns",
        help="**What it is** — the return the company earns on the money the "
             "shareholders have left in it.\n\n"
             "**Formula** — net income (TTM) ÷ **average** equity over the "
             "same twelve months — average, not year-end, because a flow "
             "divided by a closing snapshot flatters companies that have just "
             "bought back stock.\n\n"
             "**How to read it** — above 15% sustained is a genuinely good "
             "business. But debt inflates it: always read it next to ROIC and "
             "the equity ratio."),
    "roic_pct": dict(
        label="ROIC", kind="pct", better="high", group="returns",
        help="**What it is** — the return on *all* the capital at work, "
             "borrowed as well as owned. The one profitability measure "
             "leverage cannot flatter.\n\n"
             "**Formula** — EBIT × (1 − 21%) ÷ (equity + total debt − cash). "
             "The 21% US federal rate is applied to every company rather than "
             "each one's own effective rate, which the dataset does not "
             "carry — comparable beats individually exact.\n\n"
             "**How to read it** — compare it with what this company's "
             "capital costs, which the app shows next to it as **Cost of "
             "capital**: above it, growing creates value; below it, growing "
             "consumes it. That bar used to be a flat 9% for everyone, which "
             "was wrong in a consistent direction — too high for a utility, "
             "too low for a software company. Blank when invested capital is "
             "negative."),
    "wacc_pct": dict(
        label="Cost of capital", kind="pct", better="low", group="returns",
        help="**What it is** — what this company's capital costs, blending "
             "what shareholders require with what its lenders charge. It is "
             "the bar the ROIC has to clear: above it, growing creates value; "
             "below it, growing consumes it.\n\n"
             "**Formula** — equity weight × (risk-free + beta × 5% equity risk "
             "premium) + debt weight × (interest actually paid ÷ total debt) × "
             "(1 − 21%). The risk-free rate is the 10-year Treasury; the beta "
             "is clamped to 0.4–2.0 and falls back to the sector median where "
             "it is missing or implausible.\n\n"
             "**How to read it** — it replaced a flat 9% applied to everyone, "
             "which was not merely imprecise but wrong in a consistent "
             "direction: utilities, whose capital genuinely costs about 6%, "
             "were marked as destroying value, and technology companies, whose "
             "capital costs over 11%, were flattered. The one judgement left "
             "in it is the 5% equity risk premium — nobody files that number, "
             "and every WACC assumes one.\n\n"
             "**Where it is blank** — banks, insurers and REITs. For a bank "
             "debt is raw material rather than financing, so neither the ROIC "
             "nor a comparison against it means anything."),

    "roic_spread_pp": dict(
        label="ROIC − cost of capital", kind="pct", better="high",
        group="returns",
        help="**What it is** — how far the return on capital clears what the "
             "capital costs, in percentage points. The single number behind "
             "the sentence in the commentary.\n\n"
             "**Formula** — ROIC − WACC.\n\n"
             "**How to read it** — positive means every dollar reinvested "
             "comes back worth more than a dollar, which is what makes growth "
             "worth having. Negative means the opposite, and a company in that "
             "position should be shrinking rather than expanding. Blank where "
             "the ROIC is not interpretable."),

    "earning_power_pct": dict(
        label="Earning power", kind="pct", better="high", group="returns",
        help="**What it is** — what the assets earn before financing and tax "
             "get involved. Graham's *earning power* test.\n\n"
             "**Formula** — EBIT (TTM) ÷ total assets; pre-tax income where "
             "EBIT is not tagged (banks).\n\n"
             "**How to read it** — comparable across companies with completely "
             "different debt and tax situations, because it ignores both."),

    # ------------------------------------------------------------------ #
    # GROWTH — the five-year track record                                 #
    # ------------------------------------------------------------------ #
    "cagr_revenue_5y": dict(
        label="Revenue CAGR 5y", kind="pct", better="high", group="growth",
        help="**What it is** — the average yearly rate at which sales have "
             "compounded over five years.\n\n"
             "**Formula** — ((value now ÷ value five years ago) ^ (1/5)) − 1, "
             "on the trailing-twelve-month series.\n\n"
             "**How to read it** — the most reliable growth line, because "
             "revenue is the hardest number to massage. The two endpoints "
             "behind it are listed at the bottom of this section."),
    "cagr_eps_5y": dict(
        label="EPS CAGR 5y", kind="pct", better="high", group="growth",
        help="**What it is** — the average yearly rate at which earnings per "
             "share have compounded over five years.\n\n"
             "**Formula** — ((value now ÷ value five years ago) ^ (1/5)) − 1, "
             "on the trailing-twelve-month series.\n\n"
             "**How to read it** — this is the growth the fair multiple is "
             "built on. Left blank when the starting value was a loss: a "
             "compound rate from a negative base means nothing."),
    "cagr_net_income_5y": dict(
        label="Net income CAGR 5y", kind="pct", better="high", group="growth",
        help="**What it is** — five-year compound growth of total profit.\n\n"
             "**Formula** — ((value now ÷ value five years ago) ^ (1/5)) − 1, "
             "on the trailing-twelve-month series.\n\n"
             "**How to read it** — compare it with the EPS rate: EPS growing "
             "faster means buybacks are helping, slower means shares are being "
             "issued."),
    "cagr_fcf_5y": dict(
        label="FCF CAGR 5y", kind="pct", better="high", group="growth",
        help="**What it is** — five-year compound growth of free cash "
             "flow.\n\n"
             "**Formula** — ((value now ÷ value five years ago) ^ (1/5)) − 1, "
             "on the trailing-twelve-month series.\n\n"
             "**How to read it** — the acid test of the earnings growth next "
             "to it. Profits that rise while cash does not are the classic "
             "early warning."),
    "cagr_ocf_5y": dict(
        label="Op. cash flow CAGR 5y", kind="pct", better="high",
        group="growth",
        help="**What it is** — five-year compound growth of operating cash "
             "flow.\n\n"
             "**Formula** — ((value now ÷ value five years ago) ^ (1/5)) − 1, "
             "on the trailing-twelve-month series.\n\n"
             "**How to read it** — steadier than free cash flow, which a "
             "single heavy investment year can distort, so it is the better "
             "read on the underlying trend."),
}

CAGR_METRICS = ["cagr_eps", "cagr_revenue", "cagr_fcf", "cagr_net_income", "cagr_ocf"]
CAGR_LABELS = {"cagr_eps": "EPS", "cagr_revenue": "Revenue", "cagr_fcf": "Free cash flow",
               "cagr_net_income": "Net income", "cagr_ocf": "Operating cash flow"}
CAGR_HORIZONS = [3, 5, 10]


# ===========================================================================
# FORMATTING
# ===========================================================================

def fmt_money(v, digits: int = 1) -> str:
    """
    Compact money: 12.9 B$, 491 M$, -3.2 B$.

    The decimal is dropped above 100 of any unit: "491 M$" carries the same
    information as "491.0 M$" and reads faster in a dense grid.
    """
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    a = abs(v)
    for unit, scale in (("T$", 1e12), ("B$", 1e9), ("M$", 1e6), ("K$", 1e3)):
        if a >= scale:
            scaled = v / scale
            return f"{scaled:,.{0 if abs(scaled) >= 100 else digits}f} {unit}"
    return f"{v:,.0f} $"


def fmt_pct(v, digits: int = 1) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{v:,.{digits}f}%"


def fmt_ratio(v, digits: int = 1) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{v:,.{digits}f}"


def fmt_usd(v, digits: int = 2) -> str:
    """
    Un importo PER AZIONE: "$19.82".

    Non passa da fmt_money di proposito. Abbreviare un valore per azione lo
    rende illeggibile — un EPS di 19,82 dollari stampato "20 $" perde
    esattamente la precisione che serve, visto che il fair value nasce da una
    moltiplicazione su quel numero.
    """
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"${v:,.{digits}f}"


def fmt_count(v) -> str:
    """Una quantita' di cose (azioni): 7.43 B, 512 M. Scalata, senza valuta."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    a = abs(v)
    for unit, scale in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if a >= scale:
            s = v / scale
            return f"{s:,.{0 if abs(s) >= 100 else 2}f} {unit}"
    return f"{v:,.0f}"


_FORMATTERS = {"money": fmt_money, "pct": fmt_pct, "ratio": fmt_ratio,
               "usd": fmt_usd, "count": fmt_count}


def fmt_metric(key: str, v) -> str:
    kind = METRICS.get(key, {}).get("kind", "ratio")
    return _FORMATTERS.get(kind, fmt_ratio)(v)


def _num(v):
    """Value as float, or None when missing/not numeric."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


# ===========================================================================
# PEER GROUP (industry) STATISTICS
# ===========================================================================

def peer_medians(fund_df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    """
    Median of every comparable metric within each peer group, plus the group's
    size.

    The MEDIAN, not the mean: valuation multiples have a long right tail (one
    company at a P/E of 900 drags a 20-stock mean up by 45 points) and the mean
    would describe that outlier rather than the group. The count travels with
    the median so the interface can refuse to compare against a group of two.
    """
    cols = [k for k, m in METRICS.items()
            if m.get("peer", True) and k in fund_df.columns]
    grouped = fund_df.groupby(group_col)
    out = grouped[cols].median(numeric_only=True)
    out["_n"] = grouped.size()
    return out


def peer_reference(row: pd.Series, ind_med: pd.DataFrame,
                   sec_med: pd.DataFrame) -> tuple[pd.Series | None, str, int]:
    """
    Chooses the benchmark for one company: its industry when that group is big
    enough, otherwise its sector, otherwise nothing.

    Returns (medians, label, peer count).
    """
    ind = row.get("industry")
    if ind in ind_med.index and ind_med.loc[ind, "_n"] >= MIN_PEERS:
        return ind_med.loc[ind], f"industry «{ind}»", int(ind_med.loc[ind, "_n"])
    sec = row.get("sector")
    if sec in sec_med.index and sec_med.loc[sec, "_n"] >= MIN_PEERS:
        return (sec_med.loc[sec], f"{sec} sector — the «{ind}» industry has "
                f"too few companies here", int(sec_med.loc[sec, "_n"]))
    return None, "no comparable peer group", 0


def peer_gap(key: str, value, peer_value) -> tuple[float | None, str]:
    """
    Gap between a company's metric and its peer group.

    Percentages are compared in PERCENTAGE POINTS, ratios and money in relative
    terms. Returns (gap, formatted text); the sign is always "company minus
    peers".
    """
    v, p = _num(value), _num(peer_value)
    if v is None or p is None:
        return None, ""
    kind = METRICS.get(key, {}).get("kind", "ratio")
    if kind == "pct":
        gap = v - p
        return gap, f"{gap:+,.1f} pp vs peers"
    if p == 0:
        return None, ""
    gap = (v / p - 1) * 100
    return gap, f"{gap:+,.0f}% vs peers"


def delta_color(key: str, gap: float | None) -> str:
    """
    Streamlit paints a positive delta green by default. For "lower is better"
    metrics (P/E, debt) that is exactly backwards, so those get "inverse".
    """
    better = METRICS.get(key, {}).get("better")
    if gap is None or better is None:
        return "off"
    return "normal" if better == "high" else "inverse"
