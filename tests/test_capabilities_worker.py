"""Tests del CapabilitiesWorker con generación.

El worker emite la generación en ambas señales (finished/error) para
que AppController pueda descartar resultados obsoletos cuando el
usuario cambia de modelo varias veces seguidas.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from ui.workers import CapabilitiesWorker


def test_worker_has_default_generation_zero():
    w = CapabilitiesWorker("http://localhost", "modelo")
    assert w.generation == 0


def test_worker_accepts_explicit_generation():
    w = CapabilitiesWorker("http://localhost", "modelo", generation=7)
    assert w.generation == 7


def test_worker_emits_generation_on_success(monkeypatch):
    from core.model_capabilities import ModelCapabilities

    fake_caps = ModelCapabilities(
        name="modelo", native_tools=True, probed=True,
    )
    monkeypatch.setattr(
        "core.model_capabilities.get_capabilities",
        lambda host, model: fake_caps,
    )

    w = CapabilitiesWorker("http://localhost", "modelo", generation=42)
    received: list[tuple] = []
    w.finished.connect(lambda *args: received.append(args))
    w.run()

    assert len(received) == 1
    model, caps, gen = received[0]
    assert model == "modelo"
    assert caps is fake_caps
    assert gen == 42


def test_worker_emits_generation_on_error(monkeypatch):
    def boom(host, model):
        raise RuntimeError("fallo de prueba")

    monkeypatch.setattr(
        "core.model_capabilities.get_capabilities", boom,
    )

    w = CapabilitiesWorker("http://localhost", "modelo", generation=13)
    received: list[tuple] = []
    w.error.connect(lambda *args: received.append(args))
    w.run()

    assert len(received) == 1
    model, msg, gen = received[0]
    assert model == "modelo"
    assert "fallo de prueba" in msg
    assert gen == 13
