"""Regresión: ChatWorker ya no expone la señal muerta `text`.

Antes había `text = Signal(str)` pero nadie la emitía. La ruta real
de streaming es buffer + `stream_ready` + drain por timer en el
controller. Eliminarla evita que un lector futuro crea que hay dos
rutas paralelas.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from ui.workers import ChatWorker


def test_chat_worker_has_no_text_signal():
    assert not hasattr(ChatWorker, "text"), (
        "ChatWorker.text existe pero ya no se emite. Si vas a añadirla "
        "de nuevo, asegúrate de emitirla en _on_model_text."
    )


def test_chat_worker_has_stream_ready_signal():
    assert hasattr(ChatWorker, "stream_ready")


def test_chat_worker_emits_finished_before_summary():
    """H3 (auditoria 2026-09-26): `finished` se emite ANTES de
    `_maybe_run_summary`. Antes, el resumen bloqueaba la senal
    de fin de turno (1-3s de UI congelada en 'Generando...').

    Test de regresion textual: inspecciona el orden en el codigo
    fuente de `run()`. Un test de senales real exigiria QThread +
    event loop, y no compensa para un orden de 2 lineas.
    """
    import inspect
    src = inspect.getsource(ChatWorker.run)
    finished_pos = src.find("self.finished.emit(result)")
    summary_pos = src.find("self._maybe_run_summary()")
    assert finished_pos >= 0, "no se encontro finished.emit(result) en run()"
    assert summary_pos >= 0, "no se encontro _maybe_run_summary() en run()"
    assert finished_pos < summary_pos, (
        "H3 regresion: _maybe_run_summary() se ejecuta ANTES de "
        "finished.emit(). El resumen bloquea el fin de turno."
    )


def test_chat_worker_emits_finished_before_summary():
    """H3 (auditoria 2026-09-26): `finished` se emite ANTES de
    `_maybe_run_summary`. Antes, el resumen bloqueaba la senal
    de fin de turno (1-3s de UI congelada en 'Generando...').

    Test de regresion textual: inspecciona el orden en el codigo
    fuente de `run()`. Un test de senales real exigiria QThread +
    event loop, y no compensa para un orden de 2 lineas.
    """
    import inspect
    src = inspect.getsource(ChatWorker.run)
    finished_pos = src.find("self.finished.emit(result)")
    summary_pos = src.find("self._maybe_run_summary()")
    assert finished_pos >= 0, "no se encontro finished.emit(result) en run()"
    assert summary_pos >= 0, "no se encontro _maybe_run_summary() en run()"
    assert finished_pos < summary_pos, (
        "H3 regresion: _maybe_run_summary() se ejecuta ANTES de "
        "finished.emit(). El resumen bloquea el fin de turno."
    )
