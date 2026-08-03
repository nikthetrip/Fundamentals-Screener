//
//  Theme.swift — Colori, testo e formati.
//
//  IL TONO E' UNA DECISIONE, NON UN GUSTO. Questa e' un'applicazione che dice
//  a qualcuno se un titolo costa troppo. Un'interfaccia allegra — icone tonde,
//  colori saturi, emoji al posto delle categorie — comunica una sicurezza che
//  i numeri qui dentro non hanno: sono stime, con una confidenza dichiarata.
//  Quindi carta e inchiostro, un solo accento, serif per i nomi e cifre
//  monospaziate. Nessuna emoji: dove la dashboard scrive 🚀 Fast Grower, qui
//  c'e' scritto Fast Grower, con accanto una barra del colore che gli spetta.
//
//  I COLORI ESISTONO IN DUE VERSIONI perche' iOS ha due temi e l'utente
//  sceglie il suo. Un colore fisso funziona bene in uno dei due e male
//  nell'altro; questi sono definiti a coppie e risolti a runtime.
//

import SwiftUI
import UIKit

enum Palette {

    /// Costruisce un colore che cambia con il tema di sistema.
    private static func dyn(_ light: UInt32, _ dark: UInt32) -> Color {
        Color(UIColor { $0.userInterfaceStyle == .dark ? UIColor(hex: dark)
                                                       : UIColor(hex: light) })
    }

    // --- superfici ---
    static let background = dyn(0xF6F4EF, 0x0C1016)
    static let surface    = dyn(0xFFFFFF, 0x151B24)
    static let panel      = dyn(0xEFECE4, 0x1A222D)
    static let separator  = dyn(0xDCD6C9, 0x263040)

    // --- testo ---
    static let ink        = dyn(0x161C24, 0xECE7DC)
    static let inkMuted   = dyn(0x5C6470, 0x93A0B0)
    static let inkFaint   = dyn(0x8A919B, 0x67717F)

    // --- giudizio ---
    // Verde e rosso smorzati: devono distinguersi, non gridare. Il verde tende
    // al bottiglia e il rosso al mattone, che e' come li stampa un bilancio.
    static let positive   = dyn(0x2C6A4B, 0x5FB98C)
    static let negative   = dyn(0x9B3B32, 0xD9776C)
    static let caution    = dyn(0x8A6A20, 0xD3A94C)

    // --- accento unico: ottone ---
    static let accent     = dyn(0x8A6D33, 0xC7A159)
}

extension UIColor {
    convenience init(hex: UInt32) {
        self.init(red:   CGFloat((hex >> 16) & 0xFF) / 255,
                  green: CGFloat((hex >> 8)  & 0xFF) / 255,
                  blue:  CGFloat( hex        & 0xFF) / 255,
                  alpha: 1)
    }
}

// ---------------------------------------------------------------------------
// TIPOGRAFIA
//
// Serif per i nomi propri (societa', titoli di sezione), sans per tutto il
// resto, monospaziato per le cifre. La ragione della terza: in una colonna di
// numeri con larghezze diverse le unita' non stanno incolonnate, e una
// tabella di valutazioni che non si legge in verticale non e' una tabella.
// ---------------------------------------------------------------------------
extension Font {
    static let displayLarge  = Font.system(.title, design: .serif).weight(.semibold)
    static let displayMedium = Font.system(.title3, design: .serif).weight(.semibold)
    static let displaySmall  = Font.system(.headline, design: .serif)

    static let sectionLabel  = Font.system(.caption, design: .default).weight(.semibold)
    static let numberLarge   = Font.system(.title2, design: .rounded)
        .weight(.medium).monospacedDigit()
    static let numberBody    = Font.system(.body).monospacedDigit()
    static let numberSmall   = Font.system(.footnote).monospacedDigit()
}

// ---------------------------------------------------------------------------
// LE CATEGORIE DI LYNCH
//
// Nella dashboard hanno un'emoji davanti. Qui hanno un colore e basta: una
// barra verticale accanto al nome. L'emoji su uno schermo da sei pollici,
// ripetuta novecento volte in una lista, e' rumore colorato.
// ---------------------------------------------------------------------------
enum LynchCategory {
    static func color(_ name: String?) -> Color {
        switch (name ?? "").lowercased() {
        case let s where s.hasPrefix("fast grower"):  return Palette.positive
        case let s where s.hasPrefix("stalwart"):     return Palette.accent
        case let s where s.hasPrefix("slow grower"):  return Palette.inkMuted
        case let s where s.hasPrefix("cyclical"):     return Palette.caution
        case let s where s.hasPrefix("asset play"):   return Palette.accent
        case let s where s.hasPrefix("turnaround"):   return Palette.negative
        default:                                      return Palette.inkFaint
        }
    }

    /// La confidenza della classificazione: alta, media, bassa.
    static func confidenceColor(_ value: String?) -> Color {
        switch (value ?? "").lowercased() {
        case "high":   return Palette.positive
        case "medium": return Palette.caution
        case "low":    return Palette.negative
        default:       return Palette.inkFaint
        }
    }

    static func confidenceLabel(_ value: String?) -> String {
        switch (value ?? "").lowercased() {
        case "high":   return "High confidence"
        case "medium": return "Medium confidence"
        case "low":    return "Low confidence"
        default:       return "Confidence not stated"
        }
    }
}

// ---------------------------------------------------------------------------
// FORMATI
//
// Ricalcano fmt_money / fmt_pct / fmt_ratio di app.py. Stessa regola: un dato
// che manca si scrive "—", non "0" e non "N/A". Uno zero e' un'informazione
// (il debito e' zero), un trattino e' l'assenza di informazione, e
// confonderli e' il modo piu' rapido di far prendere una decisione sbagliata.
// ---------------------------------------------------------------------------
enum Fmt {
    static let dash = "—"

    static func money(_ v: Double?, digits: Int = 1) -> String {
        guard let v, v.isFinite else { return dash }
        let a = abs(v)
        let sign = v < 0 ? "-" : ""
        switch a {
        case 1e12...:  return "\(sign)$\(fixed(a / 1e12, digits))T"
        case 1e9...:   return "\(sign)$\(fixed(a / 1e9,  digits))B"
        case 1e6...:   return "\(sign)$\(fixed(a / 1e6,  digits))M"
        case 1e3...:   return "\(sign)$\(fixed(a / 1e3,  digits))K"
        default:       return "\(sign)$\(fixed(a, digits))"
        }
    }

    static func usd(_ v: Double?, digits: Int = 2) -> String {
        guard let v, v.isFinite else { return dash }
        return "$" + fixed(v, digits)
    }

    static func pct(_ v: Double?, digits: Int = 1) -> String {
        guard let v, v.isFinite else { return dash }
        return fixed(v, digits) + "%"
    }

    static func signedPct(_ v: Double?, digits: Int = 1) -> String {
        guard let v, v.isFinite else { return dash }
        return (v > 0 ? "+" : "") + fixed(v, digits) + "%"
    }

    static func ratio(_ v: Double?, digits: Int = 1) -> String {
        guard let v, v.isFinite else { return dash }
        return fixed(v, digits)
    }

    /// Un conteggio, con i separatori delle migliaia. Senza, "150000"
    /// dipendenti si legge contando le cifre.
    private static let grouped: NumberFormatter = {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.maximumFractionDigits = 0
        return f
    }()

    static func count(_ v: Double?) -> String {
        guard let v, v.isFinite else { return dash }
        return grouped.string(from: NSNumber(value: v)) ?? fixed(v, 0)
    }

    private static func fixed(_ v: Double, _ digits: Int) -> String {
        String(format: "%.\(digits)f", v)
    }

    /// Da 20260731 a "31 Jul 2026". Le date nel database sono interi YYYYMMDD.
    static func date(_ yyyymmdd: Int?) -> String {
        guard let d = yyyymmdd, d > 10_000_000 else { return dash }
        let y = d / 10000, m = (d / 100) % 100, day = d % 100
        let months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        guard (1...12).contains(m) else { return dash }
        return "\(day) \(months[m - 1]) \(y)"
    }

    static func date(_ iso: String?) -> String {
        guard let iso, iso.count >= 10 else { return dash }
        let parts = iso.prefix(10).split(separator: "-")
        guard parts.count == 3, let y = Int(parts[0]), let m = Int(parts[1]),
              let d = Int(parts[2]) else { return dash }
        return date(y * 10000 + m * 100 + d)
    }
}
