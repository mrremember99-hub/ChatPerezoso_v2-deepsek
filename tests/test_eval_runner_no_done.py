"""P3#3: run_case debe marcar failure si el stream termina sin done.

Sin red: monkeypatch de httpx.stream en scripts.eval.runner.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval import runner  # noqa: E402


class _FakeResp:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def raise_for_status(self) -> None:
        pass

    def iter_lines(self):
        yield from self._lines


class _FakeStream:
    def __init__(self, lines: list[str]) -> None:
        self._resp = _FakeResp(lines)

    def __enter__(self):
        return self._resp

    def __exit__(self, *exc):
        return False


def _install(monkeypatch, lines: list[str]) -> None:
    monkeypatch.setattr(httpx, "stream", lambda *a, **kw: _FakeStream(lines))


_CASE = {"id": "x", "category": "y", "prompt": "hola"}


def test_stream_sin_done_marca_failure(monkeypatch):
    lines = [
        json.dumps({"message": {"content": "hola"}}),
        json.dumps({"message": {"content": " mundo"}}),
    ]
    _install(monkeypatch, lines)
    r = runner.run_case(_CASE, host="http://localhost:1",
                        model="m", timeout=1.0)
    assert r["success"] is False
    assert "stream_incomplete_no_done" in r["failures"]
    assert r["response_len"] > 0  # hubo tokens antes del corte


def test_stream_con_done_no_marca_failure(monkeypatch):
    lines = [
        json.dumps({"message": {"content": "hola"}}),
        json.dumps({"done": True, "done_reason": "stop"}),
    ]
    _install(monkeypatch, lines)
    r = runner.run_case(_CASE, host="http://localhost:1",
                        model="m", timeout=1.0)
    assert "stream_incomplete_no_done" not in r["failures"]


def test_stream_vacio_sin_done_marca_ambas(monkeypatch):
    """Sin tokens y sin done: las dos failures son honestas."""
    _install(monkeypatch, [])
    r = runner.run_case(_CASE, host="http://localhost:1",
                        model="m", timeout=1.0)
    assert "stream_incomplete_no_done" in r["failures"]
    assert "empty_response" in r["failures"]
