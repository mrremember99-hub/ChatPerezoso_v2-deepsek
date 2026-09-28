#!/usr/bin/env python3
"""CLI consolidada de evaluacion para ChatPerezoso.

Subcomandos:
    run --suite baseline|tools     Ejecuta y compara con baseline
    compare <a.json> <b.json>      Compara dos runs
    baseline-update <run.json>     Fija baseline.json = run

Uso:
    python scripts/eval/cli.py run --suite baseline --model gpt-oss:20b
    python scripts/eval/cli.py run --suite tools --variant both
    python scripts/eval/cli.py compare results/a.json results/b.json
    python scripts/eval/cli.py baseline-update results/x.json

Exit code:
    0 si success >= baseline_success - 1
    1 si baja 2 o mas casos
    2 si error de invocacion
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


EVAL_DIR = Path(__file__).resolve().parent
ROOT = EVAL_DIR.parent.parent
BASELINE_FILE = EVAL_DIR / "baseline.json"


def _load_baseline() -> dict:
    if not BASELINE_FILE.exists():
        return {}
    try:
        return json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _load_success_count(path: str) -> tuple[int, int]:
    """Devuelve (success, total) de un JSON de resultados."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    s = data.get("summary", {})
    if "success" in s:
        return int(s["success"]), int(s["total"])
    if "correct" in s:
        return int(s["correct"]), int(s["total"])
    return 0, 0


def _find_latest_run(suite: str) -> str | None:
    results = sorted((EVAL_DIR / "results").glob(f"{suite}_*.json"))
    return str(results[-1]) if results else None


def cmd_run(args: argparse.Namespace) -> int:
    if args.suite == "baseline":
        sys.path.insert(0, str(ROOT))
        from scripts.eval import runner  # noqa: E402
        argv = ["runner", "--model", args.model]
        if args.case:
            argv += ["--case", args.case]
        sys.argv = ["runner"] + argv[1:]
        rc = runner.main()
    elif args.suite == "tools":
        sys.path.insert(0, str(ROOT / "scripts"))
        import eval_tools  # noqa: E402
        sys.argv = ["eval_tools", "--model", args.model]
        if args.variant:
            sys.argv += ["--variant", args.variant]
        rc = eval_tools.main()
    else:
        print(f"ERROR: suite desconocida: {args.suite}", file=sys.stderr)
        return 2

    # Comparacion contra baseline
    latest = _find_latest_run(args.suite)
    baseline = _load_baseline()
    baseline_suite = baseline.get(args.suite) if isinstance(baseline, dict) else None

    if latest and baseline_suite:
        try:
            cur_s, cur_t = _load_success_count(latest)
            base_s = int(baseline_suite["summary"].get("success", baseline_suite["summary"].get("correct", 0)))
            base_t = int(baseline_suite["summary"].get("total", 0))
            print()
            print(f"Baseline: {base_s}/{base_t}  Actual: {cur_s}/{cur_t}")
            if cur_s < base_s - 1:
                print(f"REGRESION: baja {base_s - cur_s} casos vs baseline")
                return 1
            print("OK: sin regresion significativa")
        except Exception as exc:
            print(f"Aviso: no se pudo comparar con baseline: {exc}", file=sys.stderr)

    return rc


def cmd_compare(args: argparse.Namespace) -> int:
    a = json.loads(Path(args.a).read_text(encoding="utf-8"))
    b = json.loads(Path(args.b).read_text(encoding="utf-8"))
    print(f"A: {args.a} ({a.get('model','?')}, {a.get('timestamp','?')})")
    print(f"B: {args.b} ({b.get('model','?')}, {b.get('timestamp','?')})")
    print()
    a_cases = {c["id"]: c for c in a.get("cases", [])}
    b_cases = {c["id"]: c for c in b.get("cases", [])}
    all_ids = sorted(set(a_cases) | set(b_cases))
    print(f"{'case':<28} {'A':<6} {'B':<6}")
    print("-" * 45)
    diffs = 0
    for cid in all_ids:
        ca, cb = a_cases.get(cid), b_cases.get(cid)
        if ca is None or cb is None:
            print(f"{cid:<28} {'-':<6} {'-':<6} (faltante)")
            continue
        sa = "OK" if (ca.get("success") or ca.get("correct")) else "FAIL"
        sb = "OK" if (cb.get("success") or cb.get("correct")) else "FAIL"
        marker = " <<<" if sa != sb else ""
        if sa != sb:
            diffs += 1
        print(f"{cid:<28} {sa:<6} {sb:<6}{marker}")
    print()
    print(f"Diferencias: {diffs} casos")
    return 0


def cmd_baseline_update(args: argparse.Namespace) -> int:
    run = json.loads(Path(args.run).read_text(encoding="utf-8"))
    suite = run.get("suite") or run.get("variant") or "baseline"
    if suite not in ("baseline", "tools"):
        # eval_tools produce JSON con "variant": "actual"/"reduced"
        suite = "tools"
    baseline = _load_baseline()
    baseline[suite] = run
    BASELINE_FILE.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Baseline actualizado: suite={suite}, file={BASELINE_FILE}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="CLI consolidada de eval.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Ejecuta una suite.")
    p_run.add_argument("--suite", choices=["baseline", "tools"], required=True)
    p_run.add_argument("--model", default="gpt-oss:20b")
    p_run.add_argument("--case", default=None)
    p_run.add_argument("--variant", default=None)
    p_run.set_defaults(func=cmd_run)

    p_cmp = sub.add_parser("compare", help="Compara dos runs.")
    p_cmp.add_argument("a")
    p_cmp.add_argument("b")
    p_cmp.set_defaults(func=cmd_compare)

    p_bu = sub.add_parser("baseline-update", help="Actualiza baseline.")
    p_bu.add_argument("run")
    p_bu.set_defaults(func=cmd_baseline_update)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
