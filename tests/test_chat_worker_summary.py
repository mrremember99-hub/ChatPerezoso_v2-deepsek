"""Tests del resumen dentro de ChatWorker (D1, auditoria 2026-09-26)."""
from __future__ import annotations

from unittest.mock import MagicMock

from ui.workers import ChatWorker


def _make_worker(
    summary_prompt: str = "",
    summary_new_index: int = 0,
    summary_model: str = "qwen3:1.7b",
) -> ChatWorker:
    client = MagicMock()
    client.chat = MagicMock(return_value="resultado principal")
    w = ChatWorker(
        client=client,
        model="m",
        messages=[],
        tools=None,
        summary_model=summary_model,
        summary_prompt=summary_prompt,
        summary_new_index=summary_new_index,
    )
    return w


def test_worker_sin_summary_prompt_no_emite(qapp):
    w = _make_worker()
    signals: list = []
    w.summary_ready.connect(lambda raw, idx: signals.append((raw, idx)))
    w.run()
    assert signals == []
    assert w.client.chat.call_count == 1  # solo el turno principal


def test_worker_con_summary_prompt_emite(qapp):
    w = _make_worker(summary_prompt="P", summary_new_index=42)
    w.client.chat = MagicMock(side_effect=["principal", "RESUMEN"])
    signals: list = []
    w.summary_ready.connect(lambda raw, idx: signals.append((raw, idx)))
    w.run()
    assert len(signals) == 1
    assert signals[0] == ("RESUMEN", 42)


def test_worker_summary_failure_no_propaga(qapp):
    w = _make_worker(summary_prompt="P", summary_new_index=42)
    w.client.chat = MagicMock(
        side_effect=["principal", RuntimeError("boom")]
    )
    signals: list = []
    w.summary_ready.connect(lambda raw, idx: signals.append((raw, idx)))
    w.run()  # no debe lanzar
    assert signals == []


def test_worker_cancelado_no_ejecuta_resumen(qapp):
    w = _make_worker(summary_prompt="P", summary_new_index=42)
    w._cancel_event.set()
    w.client.chat = MagicMock(return_value="principal")
    signals: list = []
    w.summary_ready.connect(lambda raw, idx: signals.append((raw, idx)))
    w.run()
    # Con cancel seteado, _maybe_run_summary corta antes de llamar.
    assert w.client.chat.call_count == 1  # solo principal
    assert signals == []
