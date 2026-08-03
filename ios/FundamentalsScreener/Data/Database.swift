//
//  Database.swift — L'accesso a screener.sqlite, in sola lettura.
//
//  PERCHE' SQLITE E NIENT'ALTRO. SQLite e' dentro iOS: nessun pacchetto da
//  aggiungere, niente che possa rompersi al prossimo aggiornamento di una
//  libreria di terze parti. E soprattutto e' un file: si scarica una volta,
//  resta in cache, e finche' non ne arriva uno nuovo l'applicazione funziona
//  senza rete. Un'app di bilanci non ha bisogno di essere online: i numeri di
//  un 10-K depositato non cambiano fra stamattina e stasera.
//
//  SOLA LETTURA, DICHIARATA. Il database viene aperto con SQLITE_OPEN_READONLY
//  e non c'e' un solo INSERT in questo file. Non e' prudenza generica: e' che
//  ogni numero mostrato qui deve essere lo stesso che la pipeline Python ha
//  calcolato e che audit_dataset.py ha verificato. Nel momento in cui l'app
//  scrivesse un valore proprio, esisterebbero due verita' sullo stesso titolo.
//

import Foundation
import SQLite3

/// SQLite conserva il puntatore al testo solo fino alla prossima chiamata:
/// senza questo, `sqlite3_bind_text` passa una stringa gia' liberata.
private let SQLITE_TRANSIENT = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

final class Database {

    enum Failure: LocalizedError {
        case cannotOpen(String)
        case badSchema(Int)

        var errorDescription: String? {
            switch self {
            case .cannotOpen(let m): return "Cannot open the database: \(m)"
            case .badSchema(let v):  return "The database declares schema \(v), which this version of the app cannot read."
            }
        }
    }

    /// La versione di schema che questo codice sa leggere. La scrive
    /// build_mobile_db.py nella tabella `meta`. Se un giorno la pipeline
    /// cambiasse le colonne, un database nuovo aperto da un'app vecchia
    /// mostrerebbe campi vuoti senza dire perche': meglio rifiutarlo e
    /// tenersi quello in cache.
    /// Versione 2: la tabella `fundamentals` porta anche i rapporti derivati
    /// (ROIC, conversione in cassa, payout, earnings yield), calcolati dalla
    /// pipeline con derived_metrics.py invece che dall'app.
    static let supportedSchema = 2

    private let handle: OpaquePointer

    init(path: String) throws {
        var db: OpaquePointer?
        let flags = SQLITE_OPEN_READONLY | SQLITE_OPEN_FULLMUTEX
        guard sqlite3_open_v2(path, &db, flags, nil) == SQLITE_OK, let db else {
            let message = db.map { String(cString: sqlite3_errmsg($0)) } ?? "sconosciuto"
            sqlite3_close(db)
            throw Failure.cannotOpen(message)
        }
        self.handle = db

        let version = Int(meta("schema_version") ?? "") ?? 0
        guard version == Database.supportedSchema else {
            sqlite3_close(db)
            throw Failure.badSchema(version)
        }
    }

    deinit { sqlite3_close(handle) }

    // -----------------------------------------------------------------------
    // INTERROGAZIONE
    // -----------------------------------------------------------------------

    /// Esegue una query e trasforma ogni riga con `row`.
    ///
    /// I parametri sono sempre legati, mai interpolati nella stringa SQL —
    /// anche se qui arrivano da un campo di ricerca e non da una rete
    /// ostile: una societa' che si chiama "O'Reilly" basta a rompere una
    /// query costruita per concatenazione.
    func query<T>(_ sql: String,
                  _ params: [Value] = [],
                  row: (Row) -> T) -> [T] {
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(handle, sql, -1, &stmt, nil) == SQLITE_OK,
              let stmt else { return [] }
        defer { sqlite3_finalize(stmt) }

        for (i, p) in params.enumerated() {
            let idx = Int32(i + 1)
            switch p {
            case .text(let s):   sqlite3_bind_text(stmt, idx, s, -1, SQLITE_TRANSIENT)
            case .int(let n):    sqlite3_bind_int64(stmt, idx, Int64(n))
            case .double(let d): sqlite3_bind_double(stmt, idx, d)
            }
        }

        var out: [T] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            out.append(row(Row(stmt: stmt)))
        }
        return out
    }

    func queryFirst<T>(_ sql: String, _ params: [Value] = [],
                       row: (Row) -> T) -> T? {
        query(sql, params, row: row).first
    }

    func meta(_ key: String) -> String? {
        queryFirst("SELECT value FROM meta WHERE key = ?", [.text(key)]) {
            $0.string(0)
        } ?? nil
    }

    /// Le colonne effettivamente presenti in una tabella. Serve a non
    /// interrogare una colonna che un database piu' vecchio non ha: la query
    /// fallirebbe per intero e la scheda resterebbe vuota invece di
    /// mostrare i campi che ci sono.
    func columns(of table: String) -> Set<String> {
        Set(query("SELECT name FROM pragma_table_info(?)", [.text(table)]) {
            $0.string(0) ?? ""
        })
    }

    enum Value {
        case text(String)
        case int(Int)
        case double(Double)
    }

    /// Lettura di una riga per indice di colonna, con i tipi che servono qui.
    /// Ogni accessor restituisce un opzionale: nel dataset NULL vuol dire
    /// "non calcolabile", ed e' un'informazione da propagare fino allo
    /// schermo, non da appiattire a zero.
    struct Row {
        let stmt: OpaquePointer

        func string(_ i: Int32) -> String? {
            guard sqlite3_column_type(stmt, i) != SQLITE_NULL,
                  let c = sqlite3_column_text(stmt, i) else { return nil }
            let s = String(cString: c)
            return s.isEmpty ? nil : s
        }

        func double(_ i: Int32) -> Double? {
            guard sqlite3_column_type(stmt, i) != SQLITE_NULL else { return nil }
            let v = sqlite3_column_double(stmt, i)
            return v.isFinite ? v : nil
        }

        func int(_ i: Int32) -> Int? {
            guard sqlite3_column_type(stmt, i) != SQLITE_NULL else { return nil }
            return Int(sqlite3_column_int64(stmt, i))
        }

        func bool(_ i: Int32) -> Bool {
            // I booleani arrivano dai CSV di pandas come "True"/"False".
            if let s = string(i) { return s.lowercased() == "true" || s == "1" }
            return (int(i) ?? 0) != 0
        }
    }
}
