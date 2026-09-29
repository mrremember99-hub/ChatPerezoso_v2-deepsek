"""Tests de scripts/eval/cli.py - paths de _find_latest_run.

Sin red, sin Ollama.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval import cli  # noqa: E402


def _setup_results(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(cli, "EVAL_DIR", tmp_path)
    results = tmp_path / "results"
    results.mkdir()
    return results


def test_find_latest_run_baseline(tmp_path, monkeypatch):
    results = _setup_results(tmp_path, monkeypatch)
    (results / "baseline_20260101-000000.json").write_text("{}")
    (results / "baseline_20260102-000000.json").write_text("{}")
    got = cli._find_latest_run("baseline")
    assert got is not None
    assert got.endswith("baseline_20260102-000000.json")


def test_find_latest_run_tools_prefiere_actual(tmp_path, monkeypatch):
    results = _setup_results(tmp_path, monkeypatch)
    (results / "eval_tools_20260101-000000_actual.json").write_text("{}")
    (results / "eval_tools_20260101-000000_reduced.json").write_text("{}")
    got = cli._find_latest_run("tools")
    assert got is not None
    assert got.endswith("_actual.json")


def test_find_latest_run_tools_fallback_reduced(tmp_path, monkeypatch):
    results = _setup_results(tmp_path, monkeypatch)
    (results / "eval_tools_20260101-000000_reduced.json").write_text("{}")
    got = cli._find_latest_run("tools")
    assert got is not None
    assert got.endswith("_reduced.json")


def test_find_latest_run_none_si_vacio(tmp_path, monkeypatch):
    _setup_results(tmp_path, monkeypatch)
    assert cli._find_latest_run("baseline") is None
    assert cli._find_latest_run("tools") is None
