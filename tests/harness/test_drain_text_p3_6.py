"""P3#6: drain_text no pierde deltas concurrentes."""
from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("PySide6")

from core.harness.events import MessageDelta
from core.harness.policy import (
    AgentSpec,
    HarnessConfig,
    ModelSpec,
)
from core.harness.session import HarnessSession
from ui.harness_worker import HarnessWorker


class _NoopModel:
    def chat(self, *a, **kw):
        return iter([])


def _make_worker(tmp_path):
    cfg = HarnessConfig(
        run_id="r1",
        workspace_root=tmp_path / "ws",
        storage_dir=tmp_path / "store",
        model=ModelSpec(name="fake"),
        agent=AgentSpec(name="test"),
    )
    s = HarnessSession(cfg, model_client=_NoopModel())
    return HarnessWorker(s, "hola")


def _delta(text: str) -> MessageDelta:
    return MessageDelta(
        seq=1,
        run_id="r1",
        ts="2026-10-02T00:00:00+00:00",
        role="assistant",
        content=text,
    )


def test_drain_simple(tmp_path):
    w = _make_worker(tmp_path)
    w._on_message_delta(_delta("abc"))
    w._on_message_delta(_delta("def"))
    assert w.drain_text() == "abcdef"
    assert w.drain_text() == ""


def test_drain_concurrente_no_pierde(tmp_path):
    """El caso del hallazgo: deltas llegando durante el drain."""
    w = _make_worker(tmp_path)
    received: list[str] = []
    stop = threading.Event()

    def producer():
        i = 0
        while not stop.is_set() and i < 500:
            w._on_message_delta(_delta(str(i)))
            i += 1
            time.sleep(0.0001)

    def consumer():
        for _ in range(50):
            text = w.drain_text()
            if text:
                received.append(text)
            time.sleep(0.001)

    t1 = threading.Thread(target=producer)
    t2 = threading.Thread(target=consumer)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    stop.set()
    t2.join(timeout=5)

    final = w.drain_text()
    combined = "".join(received) + final
    for i in range(500):
        assert str(i) in combined, f"falta {i}"
