#!/usr/bin/env python3
"""
test_imports.py — Nessun modulo usa un nome che non ha.

PERCHE' ESISTE. Spostando il catalogo delle metriche da app.py a
metrics_catalog.py sono rimaste indietro tre costanti — CAGR_METRICS,
CAGR_HORIZONS, CAGR_LABELS — che la dashboard continuava a usare senza piu'
importarle. La dashboard PARTIVA lo stesso: il nome mancante si incontra solo
aprendo la scheda Bilancio di un titolo, e li' l'applicazione moriva con un
NameError.

E' il difetto piu' insidioso di Python: un nome che non esiste non e' un errore
finche' non ci passa sopra l'esecuzione. Un controllo di avvio non lo trova,
perche' l'avvio quella riga non la esegue mai.

QUESTO TEST NON ESEGUE NIENTE. Legge l'albero sintattico e verifica che ogni
nome letto sia definito nel modulo, importato, o un builtin. Trova in mezzo
secondo quello che alla dashboard costava un giro completo di click.
"""

from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

# I moduli che compongono la dashboard e la pipeline. I test non si controllano
# da soli, e .venv non e' nostro.
MODULES = [
    "app.py", "overview.py", "commentary.py", "segments.py", "charts.py",
    "price_chart.py", "screener_grid.py", "metrics_catalog.py",
    "derived_metrics.py", "edgar_logic.py", "data_sources.py", "sic_map.py",
    "build_dataset.py", "build_extras.py", "build_commentary.py",
    "build_mobile_db.py", "audit_dataset.py",
]


def declared_names(tree: ast.Module) -> set[str]:
    """Tutto cio' che il modulo definisce, importa o lega a un nome."""
    names: set[str] = set()

    class Collector(ast.NodeVisitor):
        def visit_Import(self, node):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])

        def visit_ImportFrom(self, node):
            for a in node.names:
                names.add(a.asname or a.name)

        def visit_FunctionDef(self, node):
            names.add(node.name)
            self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node):
            names.add(node.name)
            self.generic_visit(node)

        def visit_Name(self, node):
            # Ogni assegnazione, compresi i cicli e le comprensioni.
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                names.add(node.id)

        def visit_arg(self, node):
            names.add(node.arg)

        def visit_ExceptHandler(self, node):
            if node.name:
                names.add(node.name)
            self.generic_visit(node)

        def visit_Global(self, node):
            names.update(node.names)

    Collector().visit(tree)
    return names


def undefined_names(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(), filename=str(path))
    known = declared_names(tree) | set(dir(builtins)) | {
        "__file__", "__name__", "__doc__", "self", "cls",
    }
    seen: set[str] = set()
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in known and node.id not in seen:
                seen.add(node.id)
                out.append((node.lineno, node.id))
    return out


def main() -> int:
    failures = 0
    for name in MODULES:
        path = Path(name)
        if not path.exists():
            print(f"  · {name:24s} assente, saltato")
            continue
        missing = undefined_names(path)
        if missing:
            failures += 1
            print(f"  ✗ {name}")
            for line, symbol in missing:
                print(f"      riga {line}: '{symbol}' non e' definito ne' importato")
        else:
            print(f"  ✓ {name}")

    if failures:
        print(f"\n{failures} moduli usano nomi che non hanno.")
        return 1
    print(f"\nTutti i {len(MODULES)} moduli usano solo nomi definiti o importati.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
