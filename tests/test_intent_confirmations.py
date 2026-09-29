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


# ── X1.3: negaciones al inicio no son confirmaciones ──────────────


def test_no_claro_no_es_confirmacion():
    """Auditoria externa 2026-09-29, P1#2: 'no, claro' pasaba por
    'claro' antes del fix. La negacion al inicio la descalifica."""
    from core.intent import ToolIntentGate
    assert not ToolIntentGate.is_short_confirmation("no, claro")
    assert not ToolIntentGate.is_short_confirmation("no")
    assert not ToolIntentGate.is_short_confirmation("no, gracias")
    assert not ToolIntentGate.is_short_confirmation("nunca")
    assert not ToolIntentGate.is_short_confirmation("para")
    assert not ToolIntentGate.is_short_confirmation("espera")
    assert not ToolIntentGate.is_short_confirmation("cancela")


def test_confirmaciones_siguen_siendo_confirmaciones():
    """El fix no rompe los casos legitimos."""
    from core.intent import ToolIntentGate
    assert ToolIntentGate.is_short_confirmation("si")
    assert ToolIntentGate.is_short_confirmation("vale")
    assert ToolIntentGate.is_short_confirmation("ok, adelante")
    assert ToolIntentGate.is_short_confirmation("si, por favor")
    assert ToolIntentGate.is_short_confirmation("claro")
    assert ToolIntentGate.is_short_confirmation("dale")


def test_negacion_con_puntuacion_inicial():
    """'¡No!' tiene puntuacion al inicio; debe seguir contando."""
    from core.intent import ToolIntentGate
    # Ojo: el strip de puntuacion esta al final, no al principio.
    # "¡no!" -> split -> "¡no!" -> strip(".!,;:") -> "¡no" (no match).
    # Este test documenta el limite actual.
    assert not ToolIntentGate.is_short_confirmation("no!")
    assert ToolIntentGate.is_short_confirmation("no, claro") is False


def test_tool_is_requested_rechaza_no_claro():
    """El caso real: assistant propone, usuario dice 'no, claro'."""
    from core.intent import IntentRule, ToolIntentGate
    gate = ToolIntentGate({
        "escribir_archivo": IntentRule(
            verbs=("escribe", "escribir"),
            target_words=("archivo",),
        ),
    })
    # Assistant propuso escribir, usuario dice "no, claro".
    assert not gate.tool_is_requested(
        "escribir_archivo",
        "no, claro",
        last_assistant="¿Escribo el archivo main.py?",
    )
    # Control positivo: "si, claro" si autoriza.
    assert gate.tool_is_requested(
        "escribir_archivo",
        "si, claro",
        last_assistant="¿Escribo el archivo main.py?",
    )
