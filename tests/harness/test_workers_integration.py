"""S1-bis: integracion del LoopDetector en ChatWorker.

No usa Qt real: los signals de ChatWorker funcionan sin QApplication
si solo conectamos lambdas (no widgets).
"""
from __future__ import annotations

import pytest

from core.harness.loop import LoopDetector
from core.harness.policy import LoopPolicy
from ui.workers import ChatWorker


class _FakeTools:
    """Registra las llamadas y devuelve un resultado fijo."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def requires_confirmation(self, _name: str) -> bool:
        return False

    def call(self, name, arguments, **_kwargs):
        self.calls.append((name, dict(arguments)))
        return f"resultado de {name}"


class _FakeClient:
    pass


def _make_worker(*, with_detector: bool, **policy_kw) -> ChatWorker:
    tools = _FakeTools()
    detector = (
        LoopDetector(LoopPolicy(**policy_kw)) if with_detector else None
    )
    return ChatWorker(
        client=_FakeClient(),
        model="test",
        messages=[],
        tools=tools,
        auto_approve=True,
        loop_detector=detector,
    )


# ── Sin detector: comportamiento intacto ────────────────────


def test_sin_detector_no_emite_senales():
    w = _make_worker(with_detector=False)
    warning_events: list = []
    corrective_events: list = []
    abort_events: list = []
    w.loop_warning.connect(lambda *a: warning_events.append(a))
    w.loop_corrective.connect(lambda *a: corrective_events.append(a))
    w.loop_aborted.connect(lambda *a: abort_events.append(a))

    for _ in range(10):
        w._call_tool("t", {"x": 1})

    assert warning_events == []
    assert corrective_events == []
    assert abort_events == []


# ── Con detector: escalada correcta ─────────────────────────


def test_warning_a_las_3_repeticiones():
    w = _make_worker(with_detector=True)
    warnings: list = []
    w.loop_warning.connect(lambda *a: warnings.append(a))

    # result distinto para aislar generic_repeat.
    # Los resultados de FakeTools son identicos -> poll_no_progress
    # dispara tambien. Para este test, no pasa nada: warning y
    # corrective llegan por canales distintos.
    for _ in range(3):
        w._call_tool("t", {"x": 1})

    assert len(warnings) >= 1
    # detector, reason
    detector, _reason = warnings[0]
    assert detector in ("generic_repeat", "poll_no_progress")


def test_corrective_a_las_5_repeticiones():
    w = _make_worker(with_detector=True)
    correctives: list = []
    w.loop_corrective.connect(lambda *a: correctives.append(a))

    for _ in range(5):
        w._call_tool("t", {"x": 1})

    assert len(correctives) >= 1
    detector, _reason = correctives[0]
    assert detector in ("generic_repeat", "poll_no_progress")


def test_abort_a_las_8_repeticiones_cancela():
    w = _make_worker(
        with_detector=True,
        generic_repeat=(100, 100, 8),   # solo abort a 8
        poll_no_progress=(100, 100, 8),
    )
    aborts: list = []
    w.loop_aborted.connect(lambda *a: aborts.append(a))

    for _ in range(8):
        w._call_tool("t", {"x": 1})

    assert len(aborts) >= 1
    detector, _reason = aborts[0]
    assert detector in ("generic_repeat", "poll_no_progress")
    # El worker debe haberse cancelado.
    assert w._cancel_event.is_set()


# ── Detector roto no rompe el worker ────────────────────────


class _BrokenDetector(LoopDetector):
    def observe(self, *_args, **_kwargs):
        raise RuntimeError("simulado")


def test_detector_roto_no_rompe_worker():
    tools = _FakeTools()
    broken = _BrokenDetector(LoopPolicy())
    w = ChatWorker(
        client=_FakeClient(),
        model="test",
        messages=[],
        tools=tools,
        auto_approve=True,
        loop_detector=broken,
    )
    # No debe lanzar, aunque observe() explote.
    out = w._call_tool("t", {"x": 1})
    assert "resultado de t" in out
