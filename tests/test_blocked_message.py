"""F7-ter (2026-09-28): el mensaje de bloqueo no debe empujar al modelo
a pedir confirmacion al usuario (bucle observado con varios modelos).
"""
from __future__ import annotations

from core.tool_strategies import _BLOCKED_MESSAGE


def test_blocked_message_mentions_alternative_tool():
    assert "OTRA herramienta" in _BLOCKED_MESSAGE


def test_blocked_message_does_not_ask_confirmation():
    assert "pídele que confirme" not in _BLOCKED_MESSAGE


def test_blocked_message_gives_concrete_examples():
    assert "leer_archivo" in _BLOCKED_MESSAGE
    assert "buscar_en_workspace" in _BLOCKED_MESSAGE
    assert "listar_carpeta" in _BLOCKED_MESSAGE


def test_blocked_message_says_no_retry():
    assert "NO vuelvas a intentar" in _BLOCKED_MESSAGE


def test_blocked_message_explicitly_forbids_confirmacion():
    assert "SIN pedirle confirmación" in _BLOCKED_MESSAGE
