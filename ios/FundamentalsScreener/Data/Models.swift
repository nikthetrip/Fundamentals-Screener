//
//  Models.swift — Le righe del database, come tipi Swift.
//
//  UNA SOLA LISTA DI COLONNE. `fundamentals` ha 121 colonne e leggerle per
//  indice numerico sparso nel codice e' il modo sicuro di sbagliare: basta
//  che la pipeline ne aggiunga una in mezzo e l'app inizia a mostrare il
//  patrimonio netto sotto l'etichetta del debito, senza che nulla vada in
//  errore. Qui la lista delle colonne e l'ordine di lettura sono lo stesso
//  elenco, scritto una volta: se cambia uno, cambia l'altro.
//

import Foundation

// ---------------------------------------------------------------------------
// STOCK — una riga di `fundamentals`
// ---------------------------------------------------------------------------

struct Stock: Identifiable, Hashable {
    var id: String { ticker }

    // anagrafica
    let ticker: String
    let cik: String?
    let company: String
    let sector: String?
    let industry: String?
    let exchange: String?
    let sicDescription: String?

    // prezzo e valutazione
    let currentPrice: Double?
    let peRatio: Double?
    let forwardPE: Double?
    let epsTTM: Double?
    let epsGrowthYoY: Double?
    let fairValuePE15: Double?
    let premiumVsFV15: Double?
    let fairValuePEG: Double?
    let premiumVsPEG: Double?
    let valuation: String?
    let valuationPEG: String?

    // classificazione di Lynch
    let lynchCategory: String?
    let lynchFairPE: Double?
    let lynchPEBasis: String?
    let lynchNote: String?
    let lynchAnchor: String?
    let lynchEPSBase: String?
    let lynchFairPB: Double?
    let lynchConfidence: String?
    let lynchConfidenceNote: String?
    let lynchRatio: Double?
    let fairValueNote: String?

    // crescita
    let growth5yCAGR: Double?
    let growthBasis: String?
    let growth5yCAGRRaw: Double?
    let growth5yTrend: Double?
    let lossPeriods5y: Int?
    let lossEpisodes5y: Int?
    let yearsSinceLastLoss: Double?
    let earningsVolatility: Double?
    let hadRecentLosses: Bool
    let epsPoints: Int?

    // normalizzazione
    let epsNormalized3y: Double?
    let fairValueNormPE15: Double?
    let epsVsNormalizedPct: Double?
    let epsLastDate: String?

    // dividendo e dimensione
    let dividendYield: Double?
    let dividendRate: Double?
    let dividendTTM: Double?
    let dividendTTMYieldPct: Double?
    let marketCap: Double?
    let sharesOutstanding: Double?
    let nextEarningsDate: String?
    let beta: Double?

    // conto economico e bilancio (TTM)
    let revenueTTM: Double?
    let netIncomeTTM: Double?
    let ebitTTM: Double?
    let ocfTTM: Double?
    let fcfTTM: Double?
    let equity: Double?
    let assets: Double?
    let longTermDebt: Double?
    let totalDebt: Double?
    let cash: Double?
    let netDebt: Double?
    let enterpriseValue: Double?

    // rapporti
    let evToEbit: Double?
    let netDebtToEbit: Double?
    let psRatio: Double?
    let pfcfRatio: Double?
    let pegRatio: Double?
    let priceToBook: Double?
    let roePct: Double?
    let equityRatioPct: Double?
    let earningPowerPct: Double?
    let netMarginPct: Double?
    let operatingMarginPct: Double?
    let debtToEquity: Double?
    let fcfYieldPct: Double?
    let fcfGrowthYoYPct: Double?
    let revenueGrowthYoYPct: Double?
    let bookValuePerShare: Double?
    let revenuePerShare: Double?
    let fcfPerShare: Double?

    // rapporti derivati (derived_metrics.py, gli stessi della dashboard)
    let capexTTM: Double?
    let fcfMarginPct: Double?
    let ocfMarginPct: Double?
    let capexToRevenuePct: Double?
    let fcfConversionPct: Double?
    let assetTurnover: Double?
    let roicPct: Double?
    let netDebtToEquity: Double?
    let debtToAssetsPct: Double?
    let netDebtToFcf: Double?
    let cashToDebtPct: Double?
    let earningsYieldPct: Double?
    let payoutRatioPct: Double?

    // CAGR per orizzonte
    let cagrEPS3y: Double?, cagrEPS5y: Double?, cagrEPS10y: Double?
    let cagrRevenue3y: Double?, cagrRevenue5y: Double?, cagrRevenue10y: Double?
    let cagrFCF3y: Double?, cagrFCF5y: Double?, cagrFCF10y: Double?
    let cagrNetIncome3y: Double?, cagrNetIncome5y: Double?, cagrNetIncome10y: Double?
    let cagrOCF3y: Double?, cagrOCF5y: Double?, cagrOCF10y: Double?

    // provenienza e qualita'
    let epsSource: String?
    let epsMethod: String?
    let epsBasis: String?
    let epsBasisNote: String?
    let epsSeriesAgeDays: Int?
    let epsTTMyf: Double?
    let epsTTMedgar: Double?
    let epsDivergencePct: Double?
    let epsFlag: String?
    let peRatioProvider: Double?
    let peDivergencePct: Double?
    let priceSource: String?
    let missingInputs: String?
    let financialsBasis: String?
    let conceptsUsed: String?
    let classificationSource: String?
    let staleSymbol: Bool
    let nSplits: Int?

    /// L'ordine e' quello in cui `init(row:)` legge le colonne. Non cambiare
    /// l'uno senza l'altro.
    static let columns = [
        "ticker", "cik", "company", "sector", "industry", "exchange",
        "sic_description",
        "current_price", "pe_ratio", "forward_pe", "eps_ttm", "eps_growth_yoy",
        "fair_value_pe15", "discount_vs_fv15_pct", "fair_value_peg",
        "discount_vs_peg_pct", "valuation", "valuation_peg",
        "lynch_category", "lynch_fair_pe", "lynch_pe_basis", "lynch_note",
        "lynch_anchor", "lynch_eps_base", "lynch_fair_pb", "lynch_confidence",
        "lynch_confidence_note", "lynch_ratio", "fair_value_note",
        "growth_5y_cagr", "growth_basis", "growth_5y_cagr_raw",
        "growth_5y_trend", "loss_periods_5y", "loss_episodes_5y",
        "years_since_last_loss", "earnings_volatility", "had_recent_losses",
        "eps_points",
        "eps_normalized_3y", "fair_value_norm_pe15", "eps_vs_normalized_pct",
        "eps_last_date",
        "dividend_yield", "dividend_rate", "dividend_ttm",
        "dividend_ttm_yield_pct", "market_cap", "shares_outstanding",
        "next_earnings_date", "beta",
        "revenue_ttm", "net_income_ttm", "ebit_ttm", "ocf_ttm", "fcf_ttm",
        "equity", "assets", "long_term_debt", "total_debt", "cash", "net_debt",
        "enterprise_value",
        "ev_to_ebit", "net_debt_to_ebit", "ps_ratio", "pfcf_ratio", "peg_ratio",
        "price_to_book", "roe_pct", "equity_ratio_pct", "earning_power_pct",
        "net_margin_pct", "operating_margin_pct", "debt_to_equity",
        "fcf_yield_pct", "fcf_growth_yoy_pct", "revenue_growth_yoy_pct",
        "book_value_per_share", "revenue_per_share", "fcf_per_share",
        "capex_ttm", "fcf_margin_pct", "ocf_margin_pct",
        "capex_to_revenue_pct", "fcf_conversion_pct", "asset_turnover",
        "roic_pct", "net_debt_to_equity", "debt_to_assets_pct",
        "net_debt_to_fcf", "cash_to_debt_pct", "earnings_yield_pct",
        "payout_ratio_pct",
        "cagr_eps_3y", "cagr_eps_5y", "cagr_eps_10y",
        "cagr_revenue_3y", "cagr_revenue_5y", "cagr_revenue_10y",
        "cagr_fcf_3y", "cagr_fcf_5y", "cagr_fcf_10y",
        "cagr_net_income_3y", "cagr_net_income_5y", "cagr_net_income_10y",
        "cagr_ocf_3y", "cagr_ocf_5y", "cagr_ocf_10y",
        "eps_source", "eps_method", "eps_basis", "eps_basis_note",
        "eps_series_age_days", "eps_ttm_yf", "eps_ttm_edgar",
        "eps_divergence_pct", "eps_flag", "pe_ratio_provider",
        "pe_divergence_pct", "price_source", "missing_inputs",
        "financials_basis", "concepts_used", "classification_source",
        "stale_symbol", "n_splits",
    ]

    static var selectList: String { columns.joined(separator: ", ") }

    init(row r: Database.Row) {
        var i: Int32 = -1
        func s() -> String? { i += 1; return r.string(i) }
        func d() -> Double? { i += 1; return r.double(i) }
        func n() -> Int?    { i += 1; return r.int(i) }
        func b() -> Bool    { i += 1; return r.bool(i) }

        ticker = s() ?? "?"; cik = s(); company = s() ?? "—"
        sector = s(); industry = s(); exchange = s(); sicDescription = s()

        currentPrice = d(); peRatio = d(); forwardPE = d(); epsTTM = d()
        epsGrowthYoY = d(); fairValuePE15 = d(); premiumVsFV15 = d()
        fairValuePEG = d(); premiumVsPEG = d(); valuation = s()
        valuationPEG = s()

        lynchCategory = s(); lynchFairPE = d(); lynchPEBasis = s()
        lynchNote = s(); lynchAnchor = s(); lynchEPSBase = s()
        lynchFairPB = d(); lynchConfidence = s(); lynchConfidenceNote = s()
        lynchRatio = d(); fairValueNote = s()

        growth5yCAGR = d(); growthBasis = s(); growth5yCAGRRaw = d()
        growth5yTrend = d(); lossPeriods5y = n(); lossEpisodes5y = n()
        yearsSinceLastLoss = d(); earningsVolatility = d()
        hadRecentLosses = b(); epsPoints = n()

        epsNormalized3y = d(); fairValueNormPE15 = d()
        epsVsNormalizedPct = d(); epsLastDate = s()

        dividendYield = d(); dividendRate = d(); dividendTTM = d()
        dividendTTMYieldPct = d(); marketCap = d(); sharesOutstanding = d()
        nextEarningsDate = s(); beta = d()

        revenueTTM = d(); netIncomeTTM = d(); ebitTTM = d(); ocfTTM = d()
        fcfTTM = d(); equity = d(); assets = d(); longTermDebt = d()
        totalDebt = d(); cash = d(); netDebt = d(); enterpriseValue = d()

        evToEbit = d(); netDebtToEbit = d(); psRatio = d(); pfcfRatio = d()
        pegRatio = d(); priceToBook = d(); roePct = d(); equityRatioPct = d()
        earningPowerPct = d(); netMarginPct = d(); operatingMarginPct = d()
        debtToEquity = d(); fcfYieldPct = d(); fcfGrowthYoYPct = d()
        revenueGrowthYoYPct = d(); bookValuePerShare = d()
        revenuePerShare = d(); fcfPerShare = d()

        capexTTM = d(); fcfMarginPct = d(); ocfMarginPct = d()
        capexToRevenuePct = d(); fcfConversionPct = d(); assetTurnover = d()
        roicPct = d(); netDebtToEquity = d(); debtToAssetsPct = d()
        netDebtToFcf = d(); cashToDebtPct = d(); earningsYieldPct = d()
        payoutRatioPct = d()

        cagrEPS3y = d(); cagrEPS5y = d(); cagrEPS10y = d()
        cagrRevenue3y = d(); cagrRevenue5y = d(); cagrRevenue10y = d()
        cagrFCF3y = d(); cagrFCF5y = d(); cagrFCF10y = d()
        cagrNetIncome3y = d(); cagrNetIncome5y = d(); cagrNetIncome10y = d()
        cagrOCF3y = d(); cagrOCF5y = d(); cagrOCF10y = d()

        epsSource = s(); epsMethod = s(); epsBasis = s(); epsBasisNote = s()
        epsSeriesAgeDays = n(); epsTTMyf = d(); epsTTMedgar = d()
        epsDivergencePct = d(); epsFlag = s(); peRatioProvider = d()
        peDivergencePct = d(); priceSource = s(); missingInputs = s()
        financialsBasis = s(); conceptsUsed = s(); classificationSource = s()
        staleSymbol = b(); nSplits = n()
    }

    /// Il valore di una metrica dal suo nome di colonna.
    ///
    /// SERVE PERCHE' LE SCHEDE SI DESCRIVONO PER CHIAVE. Una griglia di KPI
    /// dichiara `["pe_ratio", "roic_pct", ...]` e da quelle chiavi ricava
    /// etichetta, formato, spiegazione, verso migliore e mediana dei pari —
    /// tutto da Metrics.swift, che e' generato da app.py. Senza questo
    /// accesso per nome servirebbe ripetere in ogni griglia il valore, il suo
    /// formato e il suo confronto, che e' esattamente il modo in cui la stessa
    /// grandezza finisce formattata in due modi diversi in due schede.
    func value(for key: String) -> Double? {
        switch key {
        case "market_cap":              return marketCap
        case "enterprise_value":        return enterpriseValue
        case "pe_ratio":                return peRatio
        case "forward_pe":              return forwardPE
        case "earnings_yield_pct":      return earningsYieldPct
        case "ps_ratio":                return psRatio
        case "pfcf_ratio":              return pfcfRatio
        case "price_to_book":           return priceToBook
        case "ev_to_ebit":              return evToEbit
        case "peg_ratio":               return pegRatio
        case "fcf_yield_pct":           return fcfYieldPct
        case "dividend_yield":          return dividendYield
        case "dividend_ttm_yield_pct":  return dividendTTMYieldPct
        case "dividend_rate":           return dividendRate
        case "dividend_ttm":            return dividendTTM
        case "payout_ratio_pct":        return payoutRatioPct
        case "beta":                    return beta
        case "shares_outstanding":      return sharesOutstanding
        case "current_price":           return currentPrice
        case "eps_ttm":                 return epsTTM
        case "eps_growth_yoy":          return epsGrowthYoY
        case "eps_normalized_3y":       return epsNormalized3y
        case "eps_vs_normalized_pct":   return epsVsNormalizedPct

        case "revenue_ttm":             return revenueTTM
        case "net_income_ttm":          return netIncomeTTM
        case "ebit_ttm":                return ebitTTM
        case "ocf_ttm":                 return ocfTTM
        case "fcf_ttm":                 return fcfTTM
        case "capex_ttm":               return capexTTM
        case "revenue_latest_fy":       return nil
        case "net_margin_pct":          return netMarginPct
        case "operating_margin_pct":    return operatingMarginPct
        case "fcf_margin_pct":          return fcfMarginPct
        case "ocf_margin_pct":          return ocfMarginPct
        case "capex_to_revenue_pct":    return capexToRevenuePct
        case "fcf_conversion_pct":      return fcfConversionPct
        case "revenue_growth_yoy_pct":  return revenueGrowthYoYPct
        case "fcf_growth_yoy_pct":      return fcfGrowthYoYPct

        case "equity":                  return equity
        case "assets":                  return assets
        case "total_debt":              return totalDebt
        case "long_term_debt":          return longTermDebt
        case "cash":                    return cash
        case "net_debt":                return netDebt
        case "equity_ratio_pct":        return equityRatioPct
        case "debt_to_equity":          return debtToEquity
        case "net_debt_to_equity":      return netDebtToEquity
        case "debt_to_assets_pct":      return debtToAssetsPct
        case "net_debt_to_ebit":        return netDebtToEbit
        case "net_debt_to_fcf":         return netDebtToFcf
        case "cash_to_debt_pct":        return cashToDebtPct
        case "asset_turnover":          return assetTurnover

        case "roe_pct":                 return roePct
        case "roic_pct":                return roicPct
        case "earning_power_pct":       return earningPowerPct

        case "book_value_per_share":    return bookValuePerShare
        case "revenue_per_share":       return revenuePerShare
        case "fcf_per_share":           return fcfPerShare

        case "growth_5y_cagr":          return growth5yCAGR
        case "growth_5y_trend":         return growth5yTrend
        case "growth_5y_cagr_raw":      return growth5yCAGRRaw
        case "earnings_volatility":     return earningsVolatility
        case "lynch_ratio":             return lynchRatio
        case "lynch_fair_pe":           return lynchFairPE
        case "fair_value_pe15":         return fairValuePE15
        case "fair_value_peg":          return fairValuePEG

        case "cagr_eps_3y":             return cagrEPS3y
        case "cagr_eps_5y":             return cagrEPS5y
        case "cagr_eps_10y":            return cagrEPS10y
        case "cagr_revenue_3y":         return cagrRevenue3y
        case "cagr_revenue_5y":         return cagrRevenue5y
        case "cagr_revenue_10y":        return cagrRevenue10y
        case "cagr_fcf_3y":             return cagrFCF3y
        case "cagr_fcf_5y":             return cagrFCF5y
        case "cagr_fcf_10y":            return cagrFCF10y
        case "cagr_net_income_3y":      return cagrNetIncome3y
        case "cagr_net_income_5y":      return cagrNetIncome5y
        case "cagr_net_income_10y":     return cagrNetIncome10y
        case "cagr_ocf_3y":             return cagrOCF3y
        case "cagr_ocf_5y":             return cagrOCF5y
        case "cagr_ocf_10y":            return cagrOCF10y
        default:                        return nil
        }
    }

    // --- derivati -----------------------------------------------------------

    /// Il fair value del modello scelto. Con l'ancora `book` non e' un P/E ma
    /// il patrimonio netto per azione: dichiararlo evita che un numero che non
    /// nasce da un multiplo venga letto come se lo fosse.
    func fairValue(model: ValuationModel) -> Double? {
        model == .pe15 ? fairValuePE15 : fairValuePEG
    }

    func premium(model: ValuationModel) -> Double? {
        model == .pe15 ? premiumVsFV15 : premiumVsPEG
    }

    func verdict(model: ValuationModel) -> String? {
        model == .pe15 ? valuation : valuationPEG
    }
}

/// I due modelli, che sono davvero due e non due modi di dire lo stesso.
enum ValuationModel: String, CaseIterable, Identifiable {
    case pe15 = "Lynch Chart"
    case peg  = "Lynch Fair Value"
    var id: String { rawValue }

    var subtitle: String {
        switch self {
        case .pe15: return "TTM EPS × 15, the same yardstick for everyone"
        case .peg:  return "category anchor, PEG = 1"
        }
    }
}

// ---------------------------------------------------------------------------
// LE ALTRE TABELLE
// ---------------------------------------------------------------------------

/// Un punto dello storico: prezzo e EPS TTM a quella data.
struct HistoryPoint: Identifiable, Hashable {
    let date: Int          // YYYYMMDD
    let price: Double?
    let eps: Double?
    var id: Int { date }

    /// Le date del database sono interi: qui tornano `Date` per Swift Charts.
    var day: Date {
        var c = DateComponents()
        c.year = date / 10000
        c.month = (date / 100) % 100
        c.day = date % 100
        return Calendar(identifier: .gregorian).date(from: c) ?? .distantPast
    }

    func fairValue(pe: Double) -> Double? {
        guard let eps, eps > 0 else { return nil }
        return eps * pe
    }
}

/// Un esercizio di `financials_annual`.
struct AnnualRow: Identifiable, Hashable {
    let fyEnd: String
    let revenue: Double?
    let netIncome: Double?
    let ebit: Double?
    let ocf: Double?
    let capex: Double?
    let fcf: Double?
    let assets: Double?
    let equity: Double?
    let totalDebt: Double?
    let cash: Double?
    let dividendsPaid: Double?
    let eps: Double?

    var id: String { fyEnd }
    var year: String { String(fyEnd.prefix(4)) }
}

struct Filing: Identifiable, Hashable {
    let form: String
    let filingDate: String
    let periodDate: String?
    let url: String
    var id: String { url }
}

struct CorporateEvent: Identifiable, Hashable {
    let date: String
    let type: String
    let detail: String
    var id: String { date + type + detail }
}

/// Il profilo: che cosa fa la societa' e cosa se ne aspettano gli analisti.
/// Lo produce build_extras.py; se quel passaggio non e' ancora girato, la
/// tabella e' vuota e la scheda lo dichiara invece di restare muta.
struct Profile {
    let longName: String?
    let summary: String?
    let country: String?
    let city: String?
    let state: String?
    let website: String?
    let employees: Double?
    let targetLow: Double?
    let targetMean: Double?
    let targetMedian: Double?
    let targetHigh: Double?
    let analystCount: Int?
    let recommendation: String?
    let recommendationMean: Double?

    var location: String? {
        let parts = [city, state, country].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: ", ")
    }

    /// Il giudizio di consenso, in italiano. Yahoo lo consegna come chiave
    /// tecnica ("strong_buy"), che stampata cosi' com'e' non e' un giudizio.
    var recommendationLabel: String? {
        switch (recommendation ?? "").lowercased() {
        case "strong_buy": return "Strong buy"
        case "buy":        return "Buy"
        case "hold":       return "Hold"
        case "sell":       return "Sell"
        case "strong_sell":return "Strong sell"
        case "underperform": return "Underperform"
        case "outperform":   return "Outperform"
        default: return nil
        }
    }
}

/// Una voce della ripartizione dei ricavi: un segmento di un esercizio.
struct SegmentRow: Identifiable, Hashable {
    let axis: String
    let title: String?
    let note: String?
    let periodEnd: String
    let member: String
    let value: Double
    let total: Double?
    var id: String { axis + periodEnd + member }
}

/// Una riga di `cagr_detail`: il CAGR con i due estremi da cui esce.
struct CagrDetail: Identifiable, Hashable {
    let metric: String
    let horizonYears: Int
    let seriesBasis: String?
    let nPoints: Int?
    let startDate: String?
    let startValue: Double?
    let endDate: String?
    let endValue: Double?
    let spanYears: Double?
    let cagrPct: Double?

    var id: String { metric + String(horizonYears) }
}
