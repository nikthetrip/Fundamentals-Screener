#!/usr/bin/env python3
"""
export_metrics.py — Porta il dizionario METRICS di app.py dentro l'app iOS.

PERCHE' GENERATO E NON RISCRITTO A MANO. METRICS e' sessanta voci con
ventunomila caratteri di spiegazioni: quale grandezza e', con quale formula si
ottiene, come si legge, e se un valore alto sia un bene o un male. Ricopiarle in
Swift significherebbe avere due testi da tenere allineati, e dopo il primo
ritocco ad app.py sarebbero gia' diversi — con il risultato peggiore possibile,
cioe' due spiegazioni discordanti della stessa voce.

Cosi' invece la fonte resta una: si modifica app.py, si rilancia questo script,
e l'app iOS mostra la stessa cosa.

NON IMPORTA app.py. Importarlo significherebbe eseguirlo, e app.py all'import
apre Streamlit e carica i dataset. Si legge l'albero sintattico, si isola la
sola assegnazione di METRICS e si esegue quella.

Uso:
  python tools/export_metrics.py
"""

from __future__ import annotations

import ast
from pathlib import Path

OUT = Path("ios/FundamentalsScreener/Design/Metrics.swift")

HEADER = '''//
//  Metrics.swift — GENERATO DA tools/export_metrics.py. NON MODIFICARE A MANO.
//
//  La fonte e' il dizionario METRICS di app.py: etichette, formule,
//  spiegazioni, e per ogni voce se un valore alto sia un bene o un male. Per
//  cambiare un testo si cambia app.py e si rilancia lo script.
//
//  A COSA SERVE `better`. Streamlit colora di verde ogni delta positivo. Su
//  metriche dove il basso e' meglio — il P/E, il debito — quello e' esattamente
//  il contrario di cio' che serve: un P/E superiore ai pari verrebbe segnato
//  come un pregio. Qui il colore segue il giudizio, non il segno.
//

import SwiftUI

struct MetricSpec {
    enum Kind: String { case money, ratio, pct, usd, count }
    enum Better: String { case high, low }

    let label: String
    let kind: Kind
    let better: Better?
    /// Se la voce ha senso confrontata con la mediana dell'industria. La
    /// capitalizzazione no: essere piu' grandi non e' essere migliori.
    let comparable: Bool
    let group: String
    let help: String

    /// Il valore formattato secondo il tipo dichiarato, cosi' che la stessa
    /// grandezza si legga uguale in tutte le schede.
    func format(_ value: Double?) -> String {
        switch kind {
        case .money: return Fmt.money(value)
        case .usd:   return Fmt.usd(value)
        case .pct:   return Fmt.pct(value)
        case .count: return Fmt.count(value)
        case .ratio: return Fmt.ratio(value, digits: 2)
        }
    }
}

enum Metrics {

    /// Prima il dizionario generato da app.py, poi il supplemento scritto a
    /// mano in MetricsExtra.swift — le grandezze proprie del modello di Lynch,
    /// che nella dashboard non passano da `st.metric` e quindi non hanno una
    /// voce in METRICS.
    static func spec(_ key: String) -> MetricSpec? { all[key] ?? extra[key] }

    /// Tutte le chiavi confrontabili con i pari, generate e supplementari.
    static var comparableKeys: [String] {
        Array(all.filter { $0.value.comparable }.keys)
            + Array(extra.filter { $0.value.comparable }.keys)
    }

    /// Lo scarto rispetto ai pari.
    ///
    /// Le percentuali si confrontano in PUNTI PERCENTUALI, i rapporti e gli
    /// importi in termini relativi. Un margine del 12% contro una mediana del
    /// 9% e' "+3 punti", non "+33%": la seconda forma fa sembrare enorme una
    /// differenza di tre punti, e minuscola una differenza fra 0,1% e 0,4%.
    static func gap(_ key: String, value: Double?, peer: Double?)
        -> (amount: Double, text: String)? {
        guard let value, let peer, let spec = spec(key) else { return nil }
        if spec.kind == .pct {
            let g = value - peer
            return (g, String(format: "%+.1f pp vs peers", g))
        }
        guard peer != 0 else { return nil }
        let g = (value / peer - 1) * 100
        return (g, String(format: "%+.0f%% vs peers", g))
    }

    /// Il colore di uno scarto: verde quando e' un pregio, rosso quando e' un
    /// difetto, neutro quando la voce non ha un verso migliore.
    static func tone(_ key: String, gap: Double?) -> Color {
        guard let gap, let better = spec(key)?.better else { return Palette.inkFaint }
        let good = better == .high ? gap > 0 : gap < 0
        return good ? Palette.positive : Palette.negative
    }

'''

FOOTER = "}\n"


def swift_string(text: str) -> str:
    """Un letterale Swift su una riga: apici, barre e a capo protetti."""
    return ('"' + text.replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\t", "\\t") + '"')


def main() -> None:
    tree = ast.parse(Path("app.py").read_text())
    node = next((n for n in tree.body
                 if isinstance(n, ast.AnnAssign)
                 and getattr(n.target, "id", "") == "METRICS"), None)
    if node is None:
        raise SystemExit("METRICS non trovato in app.py")

    namespace: dict = {}
    exec(compile(ast.Module([node], []), "app.py", "exec"), namespace)
    metrics: dict[str, dict] = namespace["METRICS"]

    lines = [HEADER, "    static let all: [String: MetricSpec] = [\n"]
    for key, spec in metrics.items():
        better = spec.get("better")
        better_swift = f".{better}" if better else "nil"
        lines.append(
            f'        "{key}": MetricSpec(\n'
            f'            label: {swift_string(spec["label"])},\n'
            f'            kind: .{spec.get("kind", "ratio")},\n'
            f'            better: {better_swift},\n'
            f'            comparable: {str(spec.get("peer", True)).lower()},\n'
            f'            group: {swift_string(spec.get("group", ""))},\n'
            f'            help: {swift_string(spec.get("help", ""))}),\n')
    lines.append("    ]\n")
    lines.append(FOOTER)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(lines))
    print(f"{OUT}: {len(metrics)} metriche, "
          f"{OUT.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
