"""Tests de scripts/eval/catalog.py — sin red, sin Ollama."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval import catalog  # noqa: E402


# -- slug --

def test_slug_replaces_colon():
    assert catalog._slug("gpt-oss:20b") == "gpt-oss_20b"


def test_slug_replaces_slash():
    assert catalog._slug("library/qwen3:8b") == "library_qwen3_8b"


# -- auto_verdict --

@pytest.mark.parametrize("ok,total,lat,expected", [
    (3, 3, 10.0, "ok"),
    (3, 3, 30.0, "ok"),
    (3, 3, 60.1, "warning"),
    (2, 3, 5.0, "warning"),
    (1, 3, 5.0, "bad"),
    (0, 3, 5.0, "bad"),
    (0, 0, 0.0, "unknown"),
])
def test_auto_verdict(ok, total, lat, expected):
    assert catalog.auto_verdict(ok, total, lat) == expected


# -- build_entry --

def _model_info():
    return {
        "name": "gpt-oss:20b",
        "size": 12_000_000_000,
        "digest": "abcdef0123456789abcdef",
        "details": {
            "family": "gpt-oss",
            "parameter_size": "20B",
            "quantization_level": "Q4_K_M",
        },
    }


def _eval_result(ok=3, total=3, lat=5.0):
    return {
        "cases": [],
        "summary": {
            "success": ok, "total": total,
            "avg_latency_s": lat, "avg_ttft_s": 0.5,
        },
    }


def test_build_entry_basico():
    e = catalog.build_entry(_model_info(), {}, _eval_result())
    assert e.name == "gpt-oss:20b"
    assert e.digest == "abcdef012345"
    assert e.family == "gpt-oss"
    assert e.verdict == "ok"
    assert e.effective_verdict == "ok"


def test_build_entry_sin_eval():
    e = catalog.build_entry(_model_info(), {}, None)
    assert e.verdict == "unknown"
    assert e.total_count == 0


def test_build_entry_con_override():
    notes = {"gpt-oss:20b": {
        "verdict_override": "bad", "role": "chat", "notes": "lento",
    }}
    e = catalog.build_entry(_model_info(), notes, _eval_result(ok=3))
    assert e.verdict == "ok"
    assert e.verdict_override == "bad"
    assert e.effective_verdict == "bad"
    assert e.role == "chat"
    assert e.notes == "lento"


def test_build_entry_ignora_probe_si_falla():
    # _probe_caps devuelve None si core no expone API compatible.
    # El resto de campos no debe romper.
    e = catalog.build_entry(_model_info(), {}, _eval_result())
    assert isinstance(e.native_tools, bool)
    assert isinstance(e.context_length, int)


# -- MD --

def test_write_md(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "DOC_MD", tmp_path / "modelos-probados.md")
    e1 = catalog.ModelEntry(name="a:1b", verdict="ok", success_count=3,
                            total_count=3, avg_latency_s=1.0, role="chat")
    e2 = catalog.ModelEntry(name="b:2b", verdict="bad", success_count=0,
                            total_count=3, avg_latency_s=20.0)
    catalog.write_md([e1, e2])
    text = (tmp_path / "modelos-probados.md").read_text(encoding="utf-8")
    assert "`a:1b`" in text
    assert "`b:2b`" in text
    assert "| ok |" in text
    assert "| bad |" in text


def test_write_md_marca_override(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "DOC_MD", tmp_path / "m.md")
    e = catalog.ModelEntry(name="a:1b", verdict="ok", verdict_override="bad",
                           success_count=3, total_count=3, avg_latency_s=1.0)
    catalog.write_md([e])
    text = (tmp_path / "m.md").read_text(encoding="utf-8")
    assert "bad (manual)" in text


# -- notes --

def test_load_notes_inexistente(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "NOTES_JSON", tmp_path / "no.json")
    assert catalog.load_notes() == {}


def test_load_notes_valido(tmp_path, monkeypatch):
    p = tmp_path / "n.json"
    p.write_text(json.dumps({"m:1": {"role": "chat"}}), encoding="utf-8")
    monkeypatch.setattr(catalog, "NOTES_JSON", p)
    assert catalog.load_notes()["m:1"]["role"] == "chat"
