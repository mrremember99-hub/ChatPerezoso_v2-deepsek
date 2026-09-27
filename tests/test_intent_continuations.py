"""H1 + H19 (2026-09-27): continuacion conversacional multi-turno.

El gate solo miraba el ultimo mensaje del usuario. En flujos
"lee X" -> "arreglalo" -> "sigue" el tercer turno perdia
autorizacion porque "sigue" no matchea ningun verbo de regla.

Con H1, si el usuario responde con confirmacion ("vale") o
continuacion ("sigue", "procede", "aplicalo") y el assistant
previo propuso una accion (mencionando verbos de alguna regla),
se exponen y autorizan las tools.

Nota: los tests usan solo reglas de core/tools._RULES. Las tools
de shell/git/search vienen de sus providers, que se agregan en
CompositeToolProvider. La logica de H1 es la misma.
"""
from __future__ import annotations

import pytest

from core.intent import ToolIntentGate
from core.tools import _RULES


@pytest.fixture
def gate():
    return ToolIntentGate(_RULES)


# -- is_short_continuation ---------------------------------------------

@pytest.mark.parametrize("text", [
    "sigue", "siguelo", "continua", "continúa",
    "procede", "aplicalo", "aplícalo", "aplica",
])
def test_is_short_continuation_acepta(text):
    assert ToolIntentGate.is_short_continuation(text)


@pytest.mark.parametrize("text", [
    "no, espera",
    "para",
    "cancelalo",
    "¿qué sigue?",
    "esto es una frase muy larga que no es continuacion",
    "",
])
def test_is_short_continuation_rechaza(text):
    assert not ToolIntentGate.is_short_continuation(text)


# -- tools_for_request con continuacion --------------------------------

def test_exposure_con_confirmacion(gate):
    last = "¿Quieres que escriba el archivo main.py?"
    result = gate.tools_for_request(
        [{"type": "function", "function": {"name": "escribir_archivo"}}],
        "vale", last_assistant=last,
    )
    assert result is not None


def test_exposure_con_continuacion(gate):
    last = "He leido el archivo. ¿Continúo con la edición?"
    result = gate.tools_for_request(
        [{"type": "function", "function": {"name": "editar_archivo"}}],
        "sigue", last_assistant=last,
    )
    assert result is not None


def test_exposure_sin_last_assistant_no_expone(gate):
    result = gate.tools_for_request(
        [{"type": "function", "function": {"name": "escribir_archivo"}}],
        "sigue",
    )
    assert result is None


def test_exposure_con_last_sin_verbos_no_expone(gate):
    last = "¿Qué hora es?"
    result = gate.tools_for_request(
        [{"type": "function", "function": {"name": "escribir_archivo"}}],
        "sigue", last_assistant=last,
    )
    assert result is None


def test_exposure_texto_largo_no_es_continuacion(gate):
    last = "¿Escribo el archivo?"
    text = "sigue con lo que estabas haciendo antes por favor"
    result = gate.tools_for_request(
        [{"type": "function", "function": {"name": "escribir_archivo"}}],
        text, last_assistant=last,
    )
    assert result is None


# -- tool_is_requested con continuacion --------------------------------

def test_execution_con_confirmacion_regresion(gate):
    last = "¿Escribo el archivo main.py?"
    assert gate.tool_is_requested(
        "escribir_archivo", "vale", last_assistant=last,
    )


def test_execution_con_continuacion(gate):
    last = "¿Escribo el archivo main.py?"
    assert gate.tool_is_requested(
        "escribir_archivo", "sigue", last_assistant=last,
    )


def test_execution_continuacion_sin_last_no_autoriza(gate):
    assert not gate.tool_is_requested("escribir_archivo", "sigue")


def test_execution_continuacion_last_sin_verbo_no_autoriza(gate):
    assert not gate.tool_is_requested(
        "escribir_archivo", "sigue", last_assistant="¿Qué hora es?",
    )


# -- negaciones --------------------------------------------------------

def test_no_espera_no_autoriza(gate):
    last = "¿Escribo el archivo?"
    assert not gate.tool_is_requested(
        "escribir_archivo", "no, espera", last_assistant=last,
    )
    assert gate.tools_for_request(
        [{"type": "function", "function": {"name": "escribir_archivo"}}],
        "no, espera", last_assistant=last,
    ) is None


# -- assistant con verbo conjugado ------------------------------------

def test_assistant_verb_conjugado_matchea(gate):
    """H1: 'escribo' en el assistant matchea el lema 'escribir'."""
    last = "¿Escribo el archivo corregido?"
    assert gate.tool_is_requested(
        "escribir_archivo", "sigue", last_assistant=last,
    )


# -- multi-turno completo ---------------------------------------------

def test_flujo_multi_turno_lee_arregla_sigue(gate):
    """Caso completo: leer -> arreglar -> sigue."""
    # Turno 1: accion directa
    assert gate.tool_is_requested("leer_archivo", "lee gui.py")
    assert gate.tools_for_request(
        [{"type": "function", "function": {"name": "leer_archivo"}}],
        "lee gui.py",
    ) is not None

    # Turno 2: edicion con anafora pura ("edítalo" sin target).
    # H1-bis: el filename "gui.py" esta en el assistant previo.
    last_read = "Aqui tienes el contenido de gui.py."
    assert gate.tool_is_requested(
        "editar_archivo", "ahora edítalo",
        last_assistant=last_read,
    )

    # Turno 3: assistant propone, usuario "sigue"
    last = "Ya está escrito. ¿Edito el archivo para anadir la verificacion?"
    assert gate.tools_for_request(
        [{"type": "function", "function": {"name": "editar_archivo"}}],
        "sigue", last_assistant=last,
    ) is not None


# -- H1-bis (2026-09-27): anafora pura ----------------------------------

def test_anafora_pura_edita_con_filename_en_assistant(gate):
    """'ahora edítalo' sin target, con filename en el assistant previo."""
    last = "Aqui tienes el contenido de gui.py. ¿Lo editamos?"
    assert gate.tool_is_requested(
        "editar_archivo", "ahora edítalo", last_assistant=last,
    )


def test_anafora_pura_sin_last_no_autoriza(gate):
    """Sin assistant previo, 'edítalo' sin target no autoriza."""
    assert not gate.tool_is_requested("editar_archivo", "ahora edítalo")


def test_anafora_pura_last_sin_filename_no_autoriza(gate):
    """Assistant sin filename: no autoriza."""
    assert not gate.tool_is_requested(
        "editar_archivo", "ahora edítalo",
        last_assistant="¿Que quieres hacer ahora?",
    )


def test_anafora_pura_exposure(gate):
    """Exposure tambien se autoriza con anafora pura."""
    last = "He leido main.py. ¿Lo arreglo?"
    tools = [{"type": "function", "function": {"name": "editar_archivo"}}]
    assert gate.tools_for_request(
        tools, "hazlo", last_assistant=last,
    ) is not None


def test_anafora_pura_no_autoriza_tool_sin_verbo(gate):
    """'ahora edítalo' con last que menciona 'borrar' no autoriza editar."""
    last = "¿Borro el archivo gui.py?"
    # El assistant propone borrar, no editar. La regla de editar no
    # matchea los verbos del assistant.
    assert not gate.tool_is_requested(
        "editar_archivo", "vale", last_assistant=last,
    )


def test_anafora_pura_target_word_en_assistant(gate):
    """Target word ("archivo") en el assistant tambien sirve."""
    last = "He abierto el archivo. ¿Quieres que lo modifique?"
    assert gate.tool_is_requested(
        "editar_archivo", "sí, adelante", last_assistant=last,
    )
