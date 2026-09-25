#!/usr/bin/env python3
"""Benchmark de modelos Ollama con bateria de pruebas fija.

Uso:
    python3 scripts/benchmark_modelos.py             # todos los modelos
    python3 scripts/benchmark_modelos.py qwen3:1.7b  # solo uno

Deja un .md con los resultados en la raiz del repo.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx


HOST = "http://localhost:11434"
TIMEOUT = 180.0  # segundos por peticion
UNLOAD_AFTER = True  # liberar VRAM tras cada modelo


# -- Suite de pruebas ----------------------------------------------------

TOOLS_CREAR = [{
    "type": "function",
    "function": {
        "name": "crear_archivo",
        "description": "Crea un archivo nuevo en el workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ruta relativa."},
                "content": {"type": "string", "description": "Contenido completo."},
            },
            "required": ["path", "content"],
        },
    },
}]

TOOLS_DOS = [
    TOOLS_CREAR[0],
    {
        "type": "function",
        "function": {
            "name": "listar_carpeta",
            "description": "Lista el contenido de una carpeta.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
]


def _test_tool_simple() -> dict:
    return {
        "name": "tool_simple",
        "prompt": "Crea un archivo hola.txt con el contenido 'hola mundo'",
        "tools": TOOLS_CREAR,
        "check": lambda r: (
            bool(r.get("tool_calls"))
            and r["tool_calls"][0]["function"]["name"] == "crear_archivo"
        ),
        "criterio": "tool_calls con crear_archivo",
    }


def _test_tool_selectivo() -> dict:
    return {
        "name": "tool_selectivo",
        "prompt": "Lista el contenido de la carpeta raiz.",
        "tools": TOOLS_DOS,
        "check": lambda r: (
            bool(r.get("tool_calls"))
            and r["tool_calls"][0]["function"]["name"] == "listar_carpeta"
        ),
        "criterio": "elige listar_carpeta, no crear_archivo",
    }


def _test_razonamiento() -> dict:
    return {
        "name": "razonamiento",
        "prompt": (
            "Explica en 2 frases por que el cielo es azul. "
            "Menciona la dispersion de Rayleigh."
        ),
        "tools": None,
        "check": lambda r: "rayleigh" in r.get("content", "").lower(),
        "criterio": "menciona 'Rayleigh'",
    }


def _test_codigo() -> dict:
    return {
        "name": "codigo",
        "prompt": (
            "Escribe una funcion Python que reciba una lista de numeros "
            "y devuelva la suma de los pares. Solo el codigo, sin "
            "explicaciones."
        ),
        "tools": None,
        "check": lambda r: (
            "def " in r.get("content", "")
            and "% 2" in r.get("content", "")
        ),
        "criterio": "def + operacion % 2",
    }


def _test_negativa() -> dict:
    return {
        "name": "negativa",
        "prompt": "Que es la fotosintesis? Responde en 1 frase.",
        "tools": TOOLS_CREAR,
        "check": lambda r: (
            not r.get("tool_calls")
            and len(r.get("content", "")) > 10
        ),
        "criterio": "no usa tools, responde texto",
    }


SUITE = [
    _test_tool_simple,
    _test_tool_selectivo,
    _test_razonamiento,
    _test_codigo,
    _test_negativa,
]


# -- Ejecucion ----------------------------------------------------------

def run_test(model: str, spec: dict) -> dict:
    """Ejecuta una prueba y devuelve {ok, elapsed, respuesta, error}."""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": spec["prompt"]}],
        "stream": False,
        "keep_alive": "5m",
    }
    if spec["tools"]:
        payload["tools"] = spec["tools"]

    t0 = time.monotonic()
    try:
        r = httpx.post(
            f"{HOST}/api/chat", json=payload, timeout=TIMEOUT
        )
        r.raise_for_status()
        data = r.json()
        elapsed = time.monotonic() - t0
        msg = data.get("message", {})
        ok = spec["check"](msg)
        return {
            "ok": ok,
            "elapsed": elapsed,
            "content": msg.get("content", ""),
            "tool_calls": msg.get("tool_calls"),
            "error": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "elapsed": time.monotonic() - t0,
            "content": "",
            "tool_calls": None,
            "error": str(exc),
        }


def list_models() -> list[str]:
    r = httpx.get(f"{HOST}/api/tags", timeout=10)
    r.raise_for_status()
    return [
        item["name"]
        for item in r.json().get("models", [])
        if item.get("name")
    ]


def unload(model: str) -> None:
    """Liberar VRAM: peticion vacia con keep_alive=0."""
    try:
        httpx.post(
            f"{HOST}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "ok"}],
                "stream": False,
                "keep_alive": 0,
            },
            timeout=30,
        )
    except Exception:
        pass


# -- Main ---------------------------------------------------------------

def main() -> int:
    models = sys.argv[1:] or list_models()
    if not models:
        print("ERROR: no hay modelos en Ollama.")
        return 1

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(f"benchmark_modelos_{ts}.md")

    print(f"Benchmark sobre {len(models)} modelo(s)")
    print(f"Resultado: {out}")
    print()

    lines: list[str] = []
    lines.append(f"# Benchmark de modelos — {ts}\n")
    lines.append(f"Suite: {len(SUITE)} pruebas por modelo.\n")
    lines.append(f"Timeout por prueba: {int(TIMEOUT)}s.\n\n")

    # Tabla resumen
    lines.append("## Resumen\n\n")
    header = "| Modelo | " + " | ".join(
        f"T{i+1}" for i in range(len(SUITE))
    ) + " | Total | Tiempo |"
    lines.append(header + "\n")
    lines.append("|---" * (len(SUITE) + 3) + "|\n")

    results_by_model: dict[str, list[dict]] = {}

    for i, model in enumerate(models, 1):
        print(f"[{i}/{len(models)}] {model}")
        model_results = []
        for j, spec_factory in enumerate(SUITE, 1):
            spec = spec_factory()
            res = run_test(model, spec)
            model_results.append({**res, "name": spec["name"]})
            status = "OK" if res["ok"] else "FALLO"
            if res["error"]:
                status = f"ERR"
            print(
                f"    {spec['name']:20s} {status:6s} "
                f"{res['elapsed']:6.2f}s"
            )
        results_by_model[model] = model_results

        row_marks = [
            "✅" if r["ok"] else ("❌" if not r["error"] else "⚠️")
            for r in model_results
        ]
        total_ok = sum(1 for r in model_results if r["ok"])
        total_t = sum(r["elapsed"] for r in model_results)
        lines.append(
            f"| {model} | " + " | ".join(row_marks)
            + f" | {total_ok}/{len(SUITE)} | {total_t:.1f}s |\n"
        )

        if UNLOAD_AFTER:
            unload(model)

        # Guardar progreso tras cada modelo (por si se interrumpe)
        out.write_text("".join(lines), encoding="utf-8")

    # Detalle por modelo
    lines.append("\n\n## Detalle\n")
    for model, results in results_by_model.items():
        lines.append(f"\n### {model}\n")
        for r in results:
            ok = "OK" if r["ok"] else ("ERROR" if r["error"] else "FALLO")
            lines.append(
                f"\n**{r['name']}** — {ok} — {r['elapsed']:.2f}s\n\n"
            )
            if r["error"]:
                lines.append(f"```\n{r['error']}\n```\n")
                continue
            if r["tool_calls"]:
                tc = json.dumps(
                    r["tool_calls"], ensure_ascii=False, indent=2
                )
                lines.append(f"```json\n{tc}\n```\n")
            content = (r["content"] or "")[:400]
            if content:
                lines.append(f"```\n{content}\n```\n")

    out.write_text("".join(lines), encoding="utf-8")
    print()
    print(f"Listo. Resultado en: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
