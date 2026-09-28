#!/usr/bin/env python3
"""Suite baseline: 15 casos congelados contra Ollama directo.

Adaptado de scripts/eval_baseline.py (2026-09-28). La versión CLI
sigue disponible como wrapper en scripts/eval_baseline.py.

Uso:
    python -m scripts.eval.runner
    python -m scripts.eval.runner --model qwen3:8b
    python -m scripts.eval.runner --case cod_crear_funcion
    python -m scripts.eval.runner --compare a.json b.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parent.parent.parent
CASES_FILE = Path(__file__).resolve().parent / "cases.json"
DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "gpt-oss:20b"
DEFAULT_TIMEOUT = 180.0


def load_cases() -> dict:
    return json.loads(CASES_FILE.read_text(encoding="utf-8"))


def run_case(case: dict, *, host: str, model: str, timeout: float) -> dict:
    """Ejecuta un caso contra Ollama. Devuelve dict de metricas."""
    messages = []
    if case.get("system_prompt"):
        messages.append({"role": "system", "content": case["system_prompt"]})
    messages.append({"role": "user", "content": case["prompt"]})

    payload = {"model": model, "messages": messages, "stream": True}

    result = {
        "id": case["id"],
        "category": case["category"],
        "latency_s": 0.0,
        "ttft_s": None,
        "prompt_tokens": 0,
        "eval_tokens": 0,
        "done_reason": None,
        "response_len": 0,
        "response_preview": "",
        "tool_calls": [],
        "success": False,
        "failures": [],
    }

    start = time.monotonic()
    first_token_at = None
    response_parts: list[str] = []
    final_chunk = None

    try:
        with httpx.stream(
            "POST", f"{host.rstrip('/')}/api/chat",
            json=payload, timeout=timeout,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = chunk.get("message") or {}
                content = msg.get("content") or ""
                if content:
                    if first_token_at is None:
                        first_token_at = time.monotonic()
                    response_parts.append(content)
                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        result["tool_calls"].append({
                            "name": fn.get("name", ""),
                            "arguments": fn.get("arguments", {}),
                        })
                if chunk.get("done"):
                    final_chunk = chunk
                    break
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        result["failures"].append(f"http_error: {exc}")
        result["latency_s"] = round(time.monotonic() - start, 3)
        return result

    end = time.monotonic()
    result["latency_s"] = round(end - start, 3)
    if first_token_at is not None:
        result["ttft_s"] = round(first_token_at - start, 3)

    response_text = "".join(response_parts)
    result["response_len"] = len(response_text)
    result["response_preview"] = response_text[:200]

    if final_chunk:
        result["prompt_tokens"] = int(final_chunk.get("prompt_eval_count") or 0)
        result["eval_tokens"] = int(final_chunk.get("eval_count") or 0)
        result["done_reason"] = final_chunk.get("done_reason")

    # -- Validaciones heuristicas --
    failures = result["failures"]
    if not response_text and not result["tool_calls"]:
        failures.append("empty_response")
    if result["done_reason"] == "length":
        failures.append("truncated_by_length")
    min_chars = case.get("min_response_chars", 0)
    if result["response_len"] < min_chars:
        failures.append(f"too_short: {result['response_len']} < {min_chars}")
    if case.get("expects_code_block") and "```" not in response_text:
        failures.append("no_code_block")
    if case.get("expects_tool_call") and not result["tool_calls"]:
        failures.append("no_tool_call")
    if "forbidden_substring" in case:
        if case["forbidden_substring"] in response_text:
            failures.append(f"forbidden: {case['forbidden_substring']!r} presente")
    if "max_lines" in case:
        lines = [l for l in response_text.strip().split(chr(10)) if l.strip()]
        if len(lines) > case["max_lines"]:
            failures.append(f"too_many_lines: {len(lines)} > {case['max_lines']}")

    result["success"] = len(failures) == 0
    return result


def run_all(cases: dict, *, host: str, model: str, timeout: float,
            only_case: str | None = None) -> dict:
    selected = cases["cases"]
    if only_case:
        selected = [c for c in selected if c["id"] == only_case]
        if not selected:
            print(f"ERROR: caso {only_case!r} no encontrado", file=sys.stderr)
            sys.exit(1)

    results = []
    for i, case in enumerate(selected, 1):
        print(f"[{i}/{len(selected)}] {case['id']}...", end=" ", flush=True)
        r = run_case(case, host=host, model=model, timeout=timeout)
        status = "OK" if r["success"] else "FAIL"
        ttft = f"{r['ttft_s']:.2f}s" if r["ttft_s"] is not None else "n/a"
        print(f"{status} ({r['latency_s']:.2f}s, TTFT {ttft})")
        for f in r["failures"]:
            print(f"      - {f}")
        results.append(r)

    successes = sum(1 for r in results if r["success"])
    avg_latency = (sum(r["latency_s"] for r in results) / len(results)) if results else 0.0
    ttfts = [r["ttft_s"] for r in results if r["ttft_s"] is not None]
    avg_ttft = sum(ttfts) / len(ttfts) if ttfts else 0.0

    return {
        "suite": "baseline",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "host": host,
        "model": model,
        "cases": results,
        "summary": {
            "total": len(results),
            "success": successes,
            "failures": len(results) - successes,
            "avg_latency_s": round(avg_latency, 3),
            "avg_ttft_s": round(avg_ttft, 3),
        },
    }


def compare(file_a: str, file_b: str) -> int:
    a = json.loads(Path(file_a).read_text(encoding="utf-8"))
    b = json.loads(Path(file_b).read_text(encoding="utf-8"))
    print(f"A: {file_a} ({a['model']}, {a['timestamp']})")
    print(f"B: {file_b} ({b['model']}, {b['timestamp']})")
    print()
    a_cases = {c["id"]: c for c in a["cases"]}
    b_cases = {c["id"]: c for c in b["cases"]}
    all_ids = sorted(set(a_cases) | set(b_cases))
    print(f"{'case':<28} {'A':<6} {'B':<6} {'delta':<10}")
    print("-" * 60)
    for cid in all_ids:
        ca, cb = a_cases.get(cid), b_cases.get(cid)
        if ca is None or cb is None:
            print(f"{cid:<28} {'-':<6} {'-':<6} (faltante)")
            continue
        sa = "OK" if ca["success"] else "FAIL"
        sb = "OK" if cb["success"] else "FAIL"
        delta = f"{cb['latency_s'] - ca['latency_s']:+.2f}s"
        marker = " <<<" if sa != sb else ""
        print(f"{cid:<28} {sa:<6} {sb:<6} {delta}{marker}")
    print()
    sa, sb = a["summary"], b["summary"]
    print(f"  A: {sa['success']}/{sa['total']} OK")
    print(f"  B: {sb['success']}/{sb['total']} OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Suite baseline.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--case", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--compare", nargs=2, metavar=("A.json", "B.json"))
    args = parser.parse_args()

    if args.compare:
        return compare(*args.compare)

    cases = load_cases()
    print(f"Modelo: {args.model}")
    print(f"Host:   {args.host}")
    print(f"Casos:  {len(cases['cases'])}")
    print()

    report = run_all(cases, host=args.host, model=args.model,
                     timeout=args.timeout, only_case=args.case)

    output = args.output
    if output is None:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        output = str(EVAL_DIR / "results" / f"baseline_{ts}.json")
    Path(output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print()
    print(f"Resultados guardados en: {output}")
    s = report["summary"]
    print(f"Resumen: {s['success']}/{s['total']} OK, "
          f"latencia media {s['avg_latency_s']:.2f}s")
    return 0 if s["failures"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
