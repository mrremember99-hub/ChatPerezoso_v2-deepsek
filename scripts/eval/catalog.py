#!/usr/bin/env python3
"""Catalogo de modelos Ollama probados contra un subset del baseline.

Uso:
    python scripts/eval/catalog.py                     # todos los modelos
    python scripts/eval/catalog.py --only gpt-oss:20b
    python scripts/eval/catalog.py --regen             # MD desde JSON
    python scripts/eval/catalog.py --skip-eval         # inventario rapido

Salidas:
    models_catalog.json            (gitignored) datos generados
    models_catalog.notes.json      (versionado) notas + overrides
    docs/modelos-probados.md       (versionado) derivado del JSON
    scripts/eval/results/catalog/<slug>.json (gitignored) crudos

Veredicto auto (subset de 3 casos):
    3/3 y latencia <= 60s  -> ok
    >= 50%                  -> warning
    resto                  -> bad
Override manual en notes.json (campo verdict_override) gana siempre.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import httpx

# -- Paths --
EVAL_DIR = Path(__file__).resolve().parent
ROOT = EVAL_DIR.parent.parent
CATALOG_JSON = ROOT / "models_catalog.json"
NOTES_JSON = ROOT / "models_catalog.notes.json"
DOC_MD = ROOT / "docs" / "modelos-probados.md"
RAW_DIR = EVAL_DIR / "results" / "catalog"

# -- Defaults --
DEFAULT_HOST = "http://localhost:11434"
DEFAULT_TIMEOUT = 180.0
# Umbral de latencia media por debajo del cual un 3/3 se considera "ok".
# Por encima, degrada a "warning". Los LLM locales son lentos: 60s es
# realista para un modelo de 20B en M-series con contexto vacio.
LATENCY_OK_S = 60.0
# Subset reducido para el catalogo: codigo, edicion, explicacion.
# Mantener sincronizado con scripts/eval/cases.json.
CATALOG_CASE_IDS = ("cod_crear_funcion", "cod_editar_funcion", "cod_explicar")

VERDICT_OK = "ok"
VERDICT_WARN = "warning"
VERDICT_BAD = "bad"
VERDICT_UNKNOWN = "unknown"


def _import_runner():
    """Importa scripts.eval.runner con el root del proyecto en sys.path."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module("scripts.eval.runner")


def _probe_caps(model: str, host: str | None = None):
    """Best-effort: lee capacidades via core.model_capabilities.

    La API real es ``get_capabilities(host, model, *, timeout=...)``.
    Si no hay host o el modulo no esta disponible, devolvemos None
    y los campos de capacidades quedan a default.
    """
    if not host:
        return None
    try:
        from core import model_capabilities as mc  # type: ignore
    except Exception:
        return None
    fn = getattr(mc, "get_capabilities", None)
    if not callable(fn):
        return None
    try:
        return fn(host, model)
    except Exception:
        return None


@dataclass
class ModelEntry:
    name: str
    size_bytes: int = 0
    digest: str = ""
    family: str = ""
    parameter_size: str = ""
    quantization: str = ""
    # auto
    verdict: str = VERDICT_UNKNOWN
    success_count: int = 0
    total_count: int = 0
    avg_latency_s: float = 0.0
    avg_ttft_s: float = 0.0
    native_tools: bool = False
    thinking: bool = False
    context_length: int = 0
    probed_at: str = ""
    # de notes.json
    role: str = ""
    notes: str = ""
    recommendation: str = ""
    verdict_override: str | None = None

    @property
    def effective_verdict(self) -> str:
        return self.verdict_override or self.verdict


def _slug(model: str) -> str:
    return model.replace(":", "_").replace("/", "_")


def list_models(host: str, timeout: float = 10.0) -> list[dict]:
    r = httpx.get(f"{host.rstrip('/')}/api/tags", timeout=timeout)
    r.raise_for_status()
    return r.json().get("models", [])


def load_notes() -> dict:
    if not NOTES_JSON.exists():
        return {}
    try:
        return json.loads(NOTES_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def auto_verdict(success_count: int, total_count: int, avg_latency_s: float) -> str:
    if total_count == 0:
        return VERDICT_UNKNOWN
    ratio = success_count / total_count
    if ratio >= 1.0 and avg_latency_s <= LATENCY_OK_S:
        return VERDICT_OK
    if ratio >= 0.5:
        return VERDICT_WARN
    return VERDICT_BAD


def run_eval(model: str, *, host: str, timeout: float) -> dict:
    runner = _import_runner()
    cases = runner.load_cases()
    by_id = {c["id"]: c for c in cases["cases"]}
    missing = [cid for cid in CATALOG_CASE_IDS if cid not in by_id]
    if missing:
        print(
            f"AVISO: casos del subset no encontrados en cases.json: {missing}",
            file=sys.stderr,
        )
    selected = [by_id[cid] for cid in CATALOG_CASE_IDS if cid in by_id]
    if not selected:
        return {
            "cases": [],
            "missing_cases": missing,
            "summary": {"success": 0, "total": 0,
                        "avg_latency_s": 0.0, "avg_ttft_s": 0.0},
        }

    results = []
    for case in selected:
        print(f"    {case['id']}...", end=" ", flush=True)
        r = runner.run_case(case, host=host, model=model, timeout=timeout)
        status = "OK" if r["success"] else "FAIL"
        print(f"{status} ({r['latency_s']:.2f}s)")
        results.append(r)

    successes = sum(1 for r in results if r["success"])
    avg_latency = sum(r["latency_s"] for r in results) / len(results)
    ttfts = [r["ttft_s"] for r in results if r["ttft_s"] is not None]
    avg_ttft = sum(ttfts) / len(ttfts) if ttfts else 0.0
    return {
        "cases": results,
        "missing_cases": missing,
        "summary": {
            "success": successes,
            "total": len(results),
            "avg_latency_s": round(avg_latency, 3),
            "avg_ttft_s": round(avg_ttft, 3),
        },
    }


def build_entry(
    model_info: dict,
    notes: dict,
    eval_result: dict | None,
    *,
    host: str | None = None,
) -> ModelEntry:
    name = model_info.get("name") or model_info.get("model") or "?"
    details = model_info.get("details") or {}

    e = ModelEntry(
        name=name,
        size_bytes=int(model_info.get("size") or 0),
        digest=(model_info.get("digest") or "")[:12],
        family=details.get("family") or "",
        parameter_size=details.get("parameter_size") or "",
        quantization=details.get("quantization_level") or "",
    )

    if eval_result:
        s = eval_result["summary"]
        e.success_count = int(s["success"])
        e.total_count = int(s["total"])
        e.avg_latency_s = float(s["avg_latency_s"])
        e.avg_ttft_s = float(s["avg_ttft_s"])
        e.verdict = auto_verdict(e.success_count, e.total_count, e.avg_latency_s)
    else:
        e.verdict = VERDICT_UNKNOWN

    caps = _probe_caps(name, host=host)
    if caps is not None:
        e.native_tools = bool(getattr(caps, "native_tools", False))
        e.thinking = bool(getattr(caps, "thinking", False))
        e.context_length = int(getattr(caps, "context_length", 0))
        rec = getattr(caps, "recommendation", "") or ""
        if rec:
            e.recommendation = rec

    e.probed_at = datetime.now().isoformat(timespec="seconds")

    note = notes.get(name) or {}
    e.role = note.get("role", "") or ""
    e.notes = note.get("notes", "") or ""
    e.verdict_override = note.get("verdict_override") or None
    if note.get("recommendation"):
        e.recommendation = note["recommendation"]
    return e


def write_raw(model: str, data: dict) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{_slug(model)}.json"
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def write_catalog(entries: list[ModelEntry], host: str) -> None:
    data = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "host": host,
        "models": [asdict(e) for e in entries],
    }
    CATALOG_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_catalog() -> list[ModelEntry]:
    if not CATALOG_JSON.exists():
        return []
    data = json.loads(CATALOG_JSON.read_text(encoding="utf-8"))
    return [ModelEntry(**m) for m in data.get("models", [])]


def write_md(entries: list[ModelEntry]) -> None:
    lines = [
        "# Modelos probados",
        "",
        "Generado por `python3 scripts/eval/catalog.py`. No editar a mano:",
        "los cambios manuales van en `models_catalog.notes.json`.",
        "",
        f"Generado: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "| Modelo | Veredicto | OK/Total | Latencia | Rol | Notas |",
        "|---|---|---|---|---|---|",
    ]
    for e in sorted(entries, key=lambda x: (x.effective_verdict, x.name)):
        verdict = e.effective_verdict
        if e.verdict_override and e.verdict_override != e.verdict:
            verdict += " (manual)"
        ok = f"{e.success_count}/{e.total_count}" if e.total_count else "-"
        lat = f"{e.avg_latency_s:.2f}s" if e.avg_latency_s else "-"
        role = e.role or "-"
        notes = (e.notes or "-").replace("|", "\\|").replace("\n", " ")
        if len(notes) > 80:
            notes = notes[:77] + "..."
        lines.append(
            f"| `{e.name}` | {verdict} | {ok} | {lat} | {role} | {notes} |"
        )
    DOC_MD.parent.mkdir(parents=True, exist_ok=True)
    DOC_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_run(
    host: str,
    only: str | None,
    skip_eval: bool,
    timeout: float,
    replace: bool = False,
) -> int:
    print(f"Host: {host}")
    models = list_models(host)
    if only:
        models = [m for m in models if (m.get("name") or m.get("model")) == only]
        if not models:
            print(f"ERROR: modelo {only!r} no encontrado en /api/tags",
                  file=sys.stderr)
            return 2

    notes = load_notes()
    # Merge por defecto: los modelos ya catalogados que no se reevaluan
    # en esta pasada se conservan. --replace parte de cero.
    prev: dict[str, ModelEntry] = {}
    if not replace:
        for e in load_catalog():
            prev[e.name] = e
    if prev:
        print(f"    (mergeando con {len(prev)} modelos previos)")

    for i, m in enumerate(models, 1):
        name = m.get("name") or m.get("model") or "?"
        print(f"[{i}/{len(models)}] {name}")
        eval_result = None
        if not skip_eval:
            try:
                eval_result = run_eval(name, host=host, timeout=timeout)
                write_raw(name, {"model": name, **eval_result})
            except Exception as exc:
                print(f"    eval fallo: {exc}", file=sys.stderr)
        entry = build_entry(m, notes, eval_result, host=host)
        prev[entry.name] = entry
        print(f"    veredicto: {entry.effective_verdict}")

    entries = sorted(prev.values(), key=lambda e: e.name)
    write_catalog(entries, host)
    write_md(entries)
    print()
    print(f"Catalogo: {CATALOG_JSON}")
    print(f"Doc:      {DOC_MD}")
    return 0


def cmd_regen() -> int:
    entries = load_catalog()
    if not entries:
        print(f"ERROR: {CATALOG_JSON} no existe o esta vacio. "
              f"Corre sin --regen primero.", file=sys.stderr)
        return 2
    write_md(entries)
    print(f"Doc regenerado: {DOC_MD} ({len(entries)} modelos)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Catalogo de modelos Ollama.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--only", default=None, help="Solo este modelo.")
    parser.add_argument("--regen", action="store_true",
                        help="Regenera MD desde JSON sin tocar Ollama.")
    parser.add_argument("--skip-eval", action="store_true",
                        help="Solo inventario /api/tags, sin evals.")
    parser.add_argument("--replace", action="store_true",
                        help="Reescribe el catalogo desde cero (default: merge).")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)

    if args.regen:
        return cmd_regen()
    return cmd_run(args.host, args.only, args.skip_eval, args.timeout,
                   replace=args.replace)


if __name__ == "__main__":
    raise SystemExit(main())
