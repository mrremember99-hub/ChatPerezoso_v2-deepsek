"""Evaluación del ToolIntentGate con 5 prompts representativos.

No necesita Ollama. El gate es una heurística pura de Python que
decide, dado el texto del usuario, qué herramientas se ofrecen al
modelo. Este script verifica que las reglas del proyecto clasifican
correctamente 5 casos típicos.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.intent import ToolIntentGate
from core.tools import ToolRegistry
from core.workspace import Workspace
from plugins.git import GitProvider
from plugins.search import SearchProvider
from plugins.shell import ShellProvider


CASES = [
    (
        "¿Qué es un closure en programación?",
        [
            ("listar_carpeta", False),
            ("leer_archivo", False),
            ("crear_archivo", False),
            ("borrar_archivo", False),
            ("buscar_en_workspace", False),
            ("ejecutar_comando", False),
        ],
    ),
    (
        "Lista los archivos del workspace",
        [("listar_carpeta", True)],
    ),
    (
        "No borres notas.txt",
        [("borrar_archivo", False)],
    ),
    (
        "¿Qué hace crear_archivo?",
        [("crear_archivo", False)],
    ),
    (
        "Crea el archivo utils.py con una función helper",
        [("crear_archivo", True)],
    ),
]


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        ws = Workspace(td)
        rules: dict = {}
        rules.update(ToolRegistry(ws).intent_rules())
        rules.update(GitProvider(ws).intent_rules())
        rules.update(SearchProvider(ws).intent_rules())
        rules.update(ShellProvider(ws).intent_rules())
        gate = ToolIntentGate(rules)

        total = 0
        passed = 0
        for i, (prompt, checks) in enumerate(CASES, 1):
            print(f"\n[Caso {i}] {prompt!r}")
            for tool, expected in checks:
                actual = gate.tool_is_requested(tool, prompt)
                ok = actual == expected
                mark = "OK " if ok else "FALLO"
                total += 1
                passed += int(ok)
                exp = "autorizar" if expected else "bloquear"
                act = "autoriza" if actual else "bloquea "
                print(f"  [{mark}] {tool:22s}  esperado={exp}  real={act}")

        print(f"\n{'='*55}")
        pct = (passed / total * 100) if total else 0
        print(f"Resultado: {passed}/{total} ({pct:.0f}%)")
        return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
