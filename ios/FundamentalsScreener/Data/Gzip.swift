//
//  Gzip.swift — Scompattare il database scaricato.
//
//  PERCHE' C'E' DA SCRIVERLO A MANO. iOS ha il framework Compression, che sa
//  fare DEFLATE, ma non conosce l'involucro gzip: i dieci byte di intestazione
//  con cui gzip.open() in Python impacchetta il file. Sono dieci byte da
//  saltare, piu' i campi facoltativi — e uno di questi, il nome del file
//  originale, Python lo scrive sempre. Saltarli male significa dare al
//  decompressore un flusso che comincia due byte piu' in la': l'errore che ne
//  esce non dice "intestazione", dice "dati corrotti".
//
//  L'alternativa era spedire il database non compresso: 29 MB invece di 7 su
//  rete mobile, ogni volta che il dataset cambia. Valgono le sessanta righe.
//

import Foundation
import Compression

enum Gzip {

    enum Failure: LocalizedError {
        case notGzip
        case truncatedHeader
        case corrupt

        var errorDescription: String? {
            switch self {
            case .notGzip:         return "The downloaded file is not a gzip archive."
            case .truncatedHeader: return "The downloaded file is incomplete."
            case .corrupt:         return "The downloaded file is corrupted."
            }
        }
    }

    /// Scompatta `source` (gzip) dentro `destination`, scrivendo a blocchi.
    ///
    /// Il compresso sta in memoria — sono sette megabyte — ma il risultato no:
    /// ventinove megabyte tenuti tutti insieme su un telefono che ha gia' la
    /// lista dei titoli caricata sono il modo di farsi terminare dal sistema
    /// proprio mentre si aggiornano i dati.
    static func inflate(source: URL, to destination: URL) throws {
        let raw = try Data(contentsOf: source)
        let body = try stripHeader(raw)

        FileManager.default.createFile(atPath: destination.path, contents: nil)
        let out = try FileHandle(forWritingTo: destination)
        defer { try? out.close() }

        var stream = compression_stream(
            dst_ptr: UnsafeMutablePointer<UInt8>(bitPattern: 1)!,
            dst_size: 0,
            src_ptr: UnsafePointer<UInt8>(bitPattern: 1)!,
            src_size: 0,
            state: nil)

        guard compression_stream_init(&stream, COMPRESSION_STREAM_DECODE,
                                      COMPRESSION_ZLIB) == COMPRESSION_STATUS_OK
        else { throw Failure.corrupt }
        defer { compression_stream_destroy(&stream) }

        let bufferSize = 1 << 18                     // 256 KB per giro
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: bufferSize)
        defer { buffer.deallocate() }

        var thrown: Error?
        body.withUnsafeBytes { (src: UnsafeRawBufferPointer) in
            guard let base = src.bindMemory(to: UInt8.self).baseAddress else {
                thrown = Failure.corrupt
                return
            }
            stream.src_ptr = base
            stream.src_size = body.count

            while true {
                stream.dst_ptr = buffer
                stream.dst_size = bufferSize

                let status = compression_stream_process(&stream, Int32(COMPRESSION_STREAM_FINALIZE.rawValue))
                let produced = bufferSize - stream.dst_size
                if produced > 0 {
                    out.write(Data(bytes: buffer, count: produced))
                }

                switch status {
                case COMPRESSION_STATUS_END:   return
                case COMPRESSION_STATUS_OK:    continue
                default:
                    thrown = Failure.corrupt
                    return
                }
            }
        }
        if let thrown { throw thrown }
    }

    /// Toglie l'intestazione gzip e restituisce il flusso DEFLATE grezzo.
    private static func stripHeader(_ data: Data) throws -> Data {
        guard data.count > 18 else { throw Failure.truncatedHeader }
        guard data[data.startIndex] == 0x1F,
              data[data.startIndex + 1] == 0x8B,
              data[data.startIndex + 2] == 0x08 else { throw Failure.notGzip }

        let flags = data[data.startIndex + 3]
        var i = data.startIndex + 10

        func requireByte() throws -> UInt8 {
            guard i < data.endIndex else { throw Failure.truncatedHeader }
            let b = data[i]; i += 1
            return b
        }

        if flags & 0x04 != 0 {                       // FEXTRA
            let lo = Int(try requireByte()), hi = Int(try requireByte())
            i += lo | (hi << 8)
            guard i <= data.endIndex else { throw Failure.truncatedHeader }
        }
        if flags & 0x08 != 0 {                       // FNAME
            while try requireByte() != 0 {}
        }
        if flags & 0x10 != 0 {                       // FCOMMENT
            while try requireByte() != 0 {}
        }
        if flags & 0x02 != 0 {                       // FHCRC
            i += 2
            guard i <= data.endIndex else { throw Failure.truncatedHeader }
        }

        // In coda ci sono CRC32 e dimensione originale: otto byte che non
        // fanno parte del flusso compresso.
        let end = data.endIndex - 8
        guard end > i else { throw Failure.truncatedHeader }
        return data[i..<end]
    }
}
