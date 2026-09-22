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
