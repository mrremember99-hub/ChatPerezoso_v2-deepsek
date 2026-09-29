"""Tests de scripts/eval/catalog.run_eval - casos faltantes (P3#2).

Sin red, sin Ollama: monkeypatch de catalog._import_runner.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval import catalog  # noqa: E402


class _FakeRunner:
    def __init__(self, case_ids: list[str]) -> None:
        self._cases = [{"id": cid, "prompt": "x"} for cid in case_ids]

    def load_cases(self) -> dict:
        return {"cases": self._cases}

    def run_case(self, case, *, host, model, timeout):  # noqa: ANN001
        return {
            "id": case["id"],
            "success": True,
            "latency_s": 1.0,
            "ttft_s": 0.5,
        }


def _install(monkeypatch, case_ids: list[str]) -> None:
    fake = _FakeRunner(case_ids)
    monkeypatch.setattr(catalog, "_import_runner", lambda: fake)


def test_run_eval_reporta_faltantes(monkeypatch, capsys):
    _install(monkeypatch, ["cod_crear_funcion"])
    r = catalog.run_eval("dummy", host="h", timeout=1.0)
    assert r["missing_cases"] == ["cod_editar_funcion", "cod_explicar"]
    assert r["summary"]["total"] == 1
    err = capsys.readouterr().err
    assert "cod_editar_funcion" in err
    assert "cod_explicar" in err


def test_run_eval_sin_faltantes(monkeypatch, capsys):
    _install(monkeypatch, list(catalog.CATALOG_CASE_IDS))
    r = catalog.run_eval("dummy", host="h", timeout=1.0)
    assert r["missing_cases"] == []
    assert r["summary"]["total"] == len(catalog.CATALOG_CASE_IDS)
    assert capsys.readouterr().err == ""


def test_run_eval_todos_faltantes(monkeypatch, capsys):
    _install(monkeypatch, [])
    r = catalog.run_eval("dummy", host="h", timeout=1.0)
    assert r["missing_cases"] == list(catalog.CATALOG_CASE_IDS)
    assert r["cases"] == []
    assert r["summary"]["total"] == 0
    assert "AVISO" in capsys.readouterr().err
