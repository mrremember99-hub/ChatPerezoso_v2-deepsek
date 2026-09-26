"""Tests P1: confirmaciones conversacionales en el gate."""
from __future__ import annotations

import pytest

from core.intent import IntentRule, ToolIntentGate


def _gate() -> ToolIntentGate:
    return ToolIntentGate({
        "leer_archivo": IntentRule(
            verbs=("lee", "leer", "abre"),
            target_words=("archivo",), accepts_filename=True,
        ),
        "escribir_archivo": IntentRule(
            verbs=("escribe", "escribir", "modifica"),
            target_words=("archivo",), accepts_filename=True,
        ),
    })


def testis_short_confirmation_basicos():
    assert ToolIntentGate.is_short_confirmation("si")
    assert ToolIntentGate.is_short_confirmation("sí")
    assert ToolIntentGate.is_short_confirmation("vale")
    assert ToolIntentGate.is_short_confirmation("OK")
    assert ToolIntentGate.is_short_confirmation("ok, adelante")
    assert ToolIntentGate.is_short_confirmation("si, por favor")
    assert ToolIntentGate.is_short_confirmation("Vale.")
    assert ToolIntentGate.is_short_confirmation("hazlo")


def testis_short_confirmation_rechaza_largos():
    assert not ToolIntentGate.is_short_confirmation("")
    assert not ToolIntentGate.is_short_confirmation("   ")
    assert not ToolIntentGate.is_short_confirmation(
        "lee el archivo gui.py y luego escribelo"
    )
    assert not ToolIntentGate.is_short_confirmation("no gracias")


def test_confirmacion_autoriza_si_assistant_menciona_verbo():
    gate = _gate()
    # Modelo pregunto "¿Puedo leerlo?" — contiene "leer".
    assert gate.tool_is_requested(
        "leer_archivo", "si",
        last_assistant="¿Puedo leerlo ahora?",
    )


def test_confirmacion_autoriza_escribir():
    gate = _gate()
    assert gate.tool_is_requested(
        "escribir_archivo", "vale",
        last_assistant="Voy a modificar el archivo. ¿Adelante?",
    )


def test_confirmacion_sin_last_assistant_no_autoriza():
    gate = _gate()
    assert not gate.tool_is_requested("leer_archivo", "si")


def test_confirmacion_sin_verbo_en_assistant_no_autoriza():
    gate = _gate()
    assert not gate.tool_is_requested(
        "leer_archivo", "si",
        last_assistant="¿Te ayudo con algo más?",
    )


def test_confirmacion_no_afecta_a_tools_no_mencionadas():
    gate = _gate()
    # Assistant pregunto sobre leer, usuario confirma con "si".
    # Solo autoriza leer_archivo, no escribir_archivo.
    assert gate.tool_is_requested(
        "leer_archivo", "si",
        last_assistant="¿Puedo leerlo?",
    )
    assert not gate.tool_is_requested(
        "escribir_archivo", "si",
        last_assistant="¿Puedo leerlo?",
    )


def test_texto_no_confirmacion_sigue_reglas_normales():
    gate = _gate()
    # "lee el archivo" no es confirmacion corta: pasa por reglas.
    assert gate.tool_is_requested(
        "leer_archivo", "lee el archivo",
        last_assistant="cualquier cosa",
    )
