#!/usr/bin/env python3
"""A/B del prompt de tools en ChatPerezoso.

Objetivo: medir el impacto del `_tool_system_prompt` (~600 palabras)
frente a una version reducida (~150 palabras) sobre la tasa de
tool-call correcta.

Diferente de eval_baseline.py (que llama a Ollama directo): este
pasa por el pipeline real — OllamaClient.chat() con ToolRegistry,
ToolIntentGate, y el system prompt de tools que la app usa.

Uso:
    python scripts/eval_tools.py                     # ambos variants
    python scripts/eval_tools.py --variant actual    # solo uno
    python scripts/eval_tools.py --model qwen3:1.7b

Sin dependencias nuevas. No toca core/ ui/ plugins/.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.ollama import OllamaClient  # noqa: E402
from core.tools import ToolRegistry  # noqa: E402
from core.workspace import Workspace  # noqa: E402

HOST = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:1.7b"
DEFAULT_TIMEOUT_PER_CASE = 90.0


# -- Los 10 casos --------------------------------------------------------
#
# Cada caso indica si se ESPERA que el modelo emita una tool call.
# El prompt pasa por el gate de intent real; si el gate no autoriza,
# ninguna tool se expone al modelo.
#
# "expected_tool": None -> no debe llamar ninguna tool
# "expected_tool": "leer_archivo" -> debe llamar esa tool

CASES = [
    {
        "id": "leer_explicito",
        "prompt": "Lee el archivo main.py",
        "expected_tool": "leer_archivo",
    },
    {
        "id": "listar_explicito",
        "prompt": "Lista el contenido de la carpeta raiz",
        "expected_tool": "listar_carpeta",
    },
    {
        "id": "crear_archivo",
        "prompt": "Crea un archivo llamado nota.txt con el contenido 'hola mundo'",
        "expected_tool": "crear_archivo",
    },
    {
        "id": "editar_archivo",
        "prompt": (
            "En main.py, reemplaza la palabra 'foo' por 'bar'. "
            "Usa la herramienta de edicion."
        ),
        "expected_tool": "editar_archivo",
    },
    {
        "id": "borrar_archivo",
        "prompt": "Borra el archivo viejo.txt",
        "expected_tool": "borrar_archivo",
    },
    {
        "id": "pregunta_general",
        "prompt": "Explica en 2 frases que es un bucle for en Python.",
        "expected_tool": None,
    },
    {
        "id": "pregunta_informativa",
        "prompt": "Como leo un archivo en Python?",
        "expected_tool": None,
    },
    {
        "id": "charla",
        "prompt": "Hola, buenos dias.",
        "expected_tool": None,
    },
    {
        "id": "pregunta_conceptual",
        "prompt": "Cual es la diferencia entre lista y tupla?",
        "expected_tool": None,
    },
    {
        "id": "leer_y_editar",
        "prompt": (
            "Lee main.py y luego editalo para anadir un comentario "
            "al inicio."
        ),
        "expected_tool": "leer_archivo",
    },
    # Auditoria 2026-09-27: casos que distinguen "leer antes de
    # editar" (bien) de "editar sin leer" (mal).
    {
        "id": "editar_sin_leer",
        "prompt": (
            "En main.py reemplaza 'foo' por 'bar' directamente. "
            "No hace falta que lo leas antes."
        ),
        "expected_tool": "editar_archivo",
    },
    {
        "id": "editar_con_flujo_natural",
        "prompt": (
            "Necesito cambiar el nombre de la funcion foo() a bar() "
            "en main.py. Hazlo con la herramienta adecuada."
        ),
        "expected_tool": "editar_archivo",
    },
    {
        "id": "crear_y_verificar",
        "prompt": (
            "Crea un archivo config.py con la constante DEBUG = True. "
            "Cuando lo tengas, verifica que existe."
        ),
        "expected_tool": "crear_archivo",
    },
]


# -- Prompt alternativo (variante reducida) ------------------------------

def _reduced_tool_prompt(active_tools):
    """Version reducida del _tool_system_prompt (~150 palabras).

    Objetivo del A/B: verificar si las 600 palabras actuales son
    necesarias o si con 150 el modelo acierta igual.
    """
    names = [
        str(item.get("function", {}).get("name", ""))
        for item in active_tools
        if item.get("function", {}).get("name")
    ]
    whitelist = "\n".join(f"- {n}" for n in names)
    return (
        "Tienes acceso a las siguientes herramientas (lista cerrada):\n"
        f"{whitelist}\n\n"
        "Usalas solo para operaciones que el usuario solicite o "
        "confirme explicitamente. No inventes nombres ni afirmes que "
        "una herramienta se ejecuto sin recibir su resultado.\n"
        "Las rutas del workspace son relativas (usa \".\" para la raiz)."
    )


# -- Preparacion del workspace de prueba --------------------------------

def _setup_workspace(root: Path) -> None:
    """Crea los archivos que los casos necesitan."""
    (root / "main.py").write_text(
        "def foo():\n"
        "    print('hola')\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    foo()\n",
        encoding="utf-8",
    )
    (root / "viejo.txt").write_text("contenido viejo\n", encoding="utf-8")


# -- Runner de un caso ---------------------------------------------------

def run_case(
    client: OllamaClient,
    case: dict,
    *,
    model: str,
    workspace_root: Path,
    timeout: float,
) -> dict:
    tools = ToolRegistry(Workspace(workspace_root))

    tool_calls: list[dict] = []

    def on_tool(name, arguments):
        try:
            result = tools.call(name, arguments, allow_destructive=True)
        except Exception as exc:
            result = f"ERROR: {exc}"
        tool_calls.append({
            "name": name,
            "arguments": dict(arguments) if isinstance(arguments, dict) else {},
            "result_head": str(result)[:120],
            "errored": str(result).startswith("ERROR"),
        })
        return result

    texts: list[str] = []

    def on_text(t):
        texts.append(t)

    start = time.monotonic()
    error = None
    try:
        result_text = client.chat(
            model,
            [{"role": "user", "content": case["prompt"]}],
            tools,
            on_text,
            on_tool,
            max_rounds=3,
            options={"temperature": 0.0},
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        result_text = ""

    elapsed = time.monotonic() - start

    expected = case["expected_tool"]
    emitted = [c["name"] for c in tool_calls]
    first_call = emitted[0] if emitted else None

    # Verdicto:
    # - expected=None → no debe emitir tool calls.
    # - expected="X" → X debe estar en `emitted`, no forzosamente
    #   el primero. El modelo puede leer antes de editar (que es
    #   comportamiento correcto, no fallo). Auditoria 2026-09-27.
    if expected is None:
        correct = len(emitted) == 0
    else:
        correct = expected in emitted

    return {
        "id": case["id"],
        "prompt": case["prompt"],
        "expected_tool": expected,
        "tool_calls": tool_calls,
        "emitted": emitted,
        "first_call": first_call,
        "correct": correct,
        "latency_s": round(elapsed, 3),
        "response_len": len(result_text or ""),
        "error": error,
    }


# -- Runner de una variante ----------------------------------------------

def run_variant(
    variant: str,
    *,
    model: str,
    timeout: float,
) -> dict:
    print(f"\n=== Variante: {variant} ===")

    original_prompt = OllamaClient._tool_system_prompt

    if variant == "reduced":
        OllamaClient._tool_system_prompt = staticmethod(_reduced_tool_prompt)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp)
            _setup_workspace(workspace_root)

            client = OllamaClient(HOST)
            results = []
            try:
                for i, case in enumerate(CASES, 1):
                    print(
                        f"  [{i}/{len(CASES)}] {case['id']}...",
                        end=" ", flush=True,
                    )
                    r = run_case(
                        client, case,
                        model=model,
                        workspace_root=workspace_root,
                        timeout=timeout,
                    )
                    status = "OK" if r["correct"] else "FAIL"
                    print(
                        f"{status} (call={r['first_call']!r}, "
                        f"{r['latency_s']:.2f}s)"
                    )
                    results.append(r)
            finally:
                try:
                    client.shutdown(timeout=5.0)
                except Exception:
                    pass
    finally:
        OllamaClient._tool_system_prompt = original_prompt

    correct = sum(1 for r in results if r["correct"])
    return {
        "variant": variant,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "cases": results,
        "summary": {
            "total": len(results),
            "correct": correct,
            "failures": len(results) - correct,
            "accuracy": round(correct / len(results), 3),
        },
    }


def compare(rep_a: dict, rep_b: dict) -> None:
    print()
    print(f"A: {rep_a['variant']} ({rep_a['summary']['correct']}/"
          f"{rep_a['summary']['total']} = "
          f"{rep_a['summary']['accuracy']:.0%})")
    print(f"B: {rep_b['variant']} ({rep_b['summary']['correct']}/"
          f"{rep_b['summary']['total']} = "
          f"{rep_b['summary']['accuracy']:.0%})")
    print()
    print(f"{'case':<24} {'A first':<20} {'B first':<20} {'delta':<8}")
    print("-" * 78)

    a_cases = {c["id"]: c for c in rep_a["cases"]}
    b_cases = {c["id"]: c for c in rep_b["cases"]}

    for cid in sorted(set(a_cases) | set(b_cases)):
        ca = a_cases.get(cid) or {}
        cb = b_cases.get(cid) or {}
        fa = ca.get("first_call") or "(none)"
        fb = cb.get("first_call") or "(none)"
        da = ca.get("correct")
        db = cb.get("correct")
        marker = ""
        if da and not db:
            marker = " A>B"
        elif db and not da:
            marker = " B>A"
        print(f"{cid:<24} {fa:<20} {fb:<20}{marker}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Modelo a evaluar (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT_PER_CASE,
        help="Timeout por caso en segundos.",
    )
    parser.add_argument(
        "--variant", choices=["actual", "reduced", "both"],
        default="both",
        help="Variante(s) a correr.",
    )
    parser.add_argument(
        "--output", default=None,
        help="Ruta base para los JSON. Default: scripts/eval_tools_<ts>",
    )
    args = parser.parse_args()

    base = args.output or str(
        ROOT / "scripts" /
        f"eval_tools_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )

    variants = ["actual"] if args.variant == "actual" else (
        ["reduced"] if args.variant == "reduced" else ["actual", "reduced"]
    )

    reports = []
    for v in variants:
        r = run_variant(v, model=args.model, timeout=args.timeout)
        Path(f"{base}_{v}.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        reports.append(r)
        print(f"  Guardado: {base}_{v}.json")

    if len(reports) == 2:
        compare(reports[0], reports[1])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
