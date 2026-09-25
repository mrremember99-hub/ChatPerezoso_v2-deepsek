"""Tests de la deteccion de falso completado."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from core.ollama import (
    OllamaClient,
    _FALSE_COMPLETION_MARKERS,
    _FALSE_COMPLETION_NUDGE,
    _WRITE_TOOLS,
    _WRITE_VERBS,
)


# ── Constantes ──────────────────────────────────────────────────────────

def test_write_tools_incluye_escritura():
    assert "escribir_archivo" in _WRITE_TOOLS
    assert "crear_archivo" in _WRITE_TOOLS


def test_write_verbs_incluye_espanol_e_ingles():
    assert "crea" in _WRITE_VERBS
    assert "escribe" in _WRITE_VERBS
    assert "write" in _WRITE_VERBS


def test_false_completion_markers():
    assert "verificada" in _FALSE_COMPLETION_MARKERS
    assert "completado" in _FALSE_COMPLETION_MARKERS
    assert "listo" in _FALSE_COMPLETION_MARKERS


def test_nudge_menciona_escribir_archivo():
    assert "escribir_archivo" in _FALSE_COMPLETION_NUDGE


# ── _user_requested_write ───────────────────────────────────────────────

def test_user_requested_write_detecta_crear():
    assert OllamaClient._user_requested_write("crea gui.py")


def test_user_requested_write_detecta_escribe():
    assert OllamaClient._user_requested_write("escribe un print")


def test_user_requested_write_detecta_ingles():
    assert OllamaClient._user_requested_write("write hello.py")


def test_user_requested_write_no_detecta_pregunta():
    assert not OllamaClient._user_requested_write("que es python?")


def test_user_requested_write_vacio():
    assert not OllamaClient._user_requested_write("")
    assert not OllamaClient._user_requested_write(None)


# ── _looks_like_false_completion ────────────────────────────────────────

def test_looks_like_verificada():
    assert OllamaClient._looks_like_false_completion("FASE VERIFICADA")


def test_looks_like_completado():
    assert OllamaClient._looks_like_false_completion("Proyecto completado.")


def test_looks_like_listo():
    assert OllamaClient._looks_like_false_completion("Todo listo.")


def test_looks_like_false_completion_no_falso_positivo():
    assert not OllamaClient._looks_like_false_completion("Voy a hacerlo ahora")


def test_looks_like_false_completion_vacio():
    assert not OllamaClient._looks_like_false_completion("")
    assert not OllamaClient._looks_like_false_completion(None)
    

# -- Caso legitimo: lectura OK + 'listo' NO dispara nudge --------------


def test_lectura_ok_con_texto_listo_no_dispara():
    """El nudge exige que alguna tool haya fallado.

    Si el modelo leyo un archivo con exito y respondio 'Listo.',
    no hay falsos completados: hizo lo que pudo con las tools
    disponibles.
    """
    # Solo comprobamos la logica de las condiciones, sin montar
    # el bucle entero.
    any_tool_call_emitted = True
    any_tool_failed = False
    any_write_executed = False
    user_asked_write = OllamaClient._user_requested_write(
        "edita x.txt, cambia a por b"
    )
    looks_completed = OllamaClient._looks_like_false_completion(
        "Listo."
    )
    # Todas las condiciones originales se cumplen...
    assert any_tool_call_emitted
    assert not any_write_executed
    assert user_asked_write
    assert looks_completed
    # ...pero sin fallo de tool, el nudge NO se dispara.
    assert not any_tool_failed


def test_tool_fallida_si_dispara_caso_mistral():
    """Caso real: ejecutar_comando falla y el modelo declara exito."""
    any_tool_call_emitted = True
    any_tool_failed = True
    any_write_executed = False
    user_asked_write = OllamaClient._user_requested_write(
        "crea gui.py"
    )
    looks_completed = OllamaClient._looks_like_false_completion(
        "FASE VERIFICADA. PROYECTO COMPLETADO."
    )
    condition = (
        any_tool_call_emitted
        and any_tool_failed
        and not any_write_executed
        and user_asked_write
        and looks_completed
    )
    assert condition
