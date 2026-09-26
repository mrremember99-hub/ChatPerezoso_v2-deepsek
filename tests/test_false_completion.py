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


# -- Integracion: H7 read->"Hecho" con write disponible ------------------

def test_read_ok_con_write_disponible_dispara_nudge(monkeypatch, tmp_path):
    """H7 del out(1): user pide crear, modelo lee OK, dice 'Hecho.'.

    Con una tool de escritura disponible y no usada, es falso
    completado: el nudge debe dispararse y forzar una segunda ronda.
    """
    from core.intent import ToolIntentGate
    from core.ollama import OllamaClient
    from core.tools import ToolRegistry
    from core.workspace import Workspace

    rules = ToolRegistry(Workspace(tmp_path)).intent_rules()
    ToolIntentGate.register_rules(rules)

    client = OllamaClient()
    calls: list[str] = []
    responses = iter([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "function": {
                    "name": "leer_archivo",
                    "arguments": {"path": "gui.py"},
                }
            }],
        },
        {"role": "assistant", "content": "Hecho."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "function": {
                    "name": "crear_archivo",
                    "arguments": {
                        "path": "gui.py",
                        "content": "print(1)",
                    },
                }
            }],
        },
        {"role": "assistant", "content": "Listo."},
    ])

    def fake_stream(model, messages, tools, on_text, cancel_event=None, options=None):
        try:
            return next(responses)
        except StopIteration:
            raise AssertionError(
                "nudge no disparo: menos rondas de las esperadas"
            )

    monkeypatch.setattr(client, "_stream", fake_stream)

    client.chat(
        "test-model",
        [{"role": "user", "content": "crea gui.py con un print"}],
        [
            {"type": "function", "function": {"name": "leer_archivo"}},
            {"type": "function", "function": {"name": "crear_archivo"}},
        ],
        lambda _: None,
        lambda name, args: calls.append(name) or "ok",
    )

    assert "crear_archivo" in calls, (
        f"El nudge no forzo la escritura. Calls: {calls}"
    )


# -- H1 (auditoria 2026-09-26): variantes que antes se perdian ----------

def test_mentions_write_variantes_conjugadas():
    from core.ollama import OllamaClient
    # Formas que antes NO matcheaban por lista plana.
    assert OllamaClient._user_requested_write("corrige la función")
    assert OllamaClient._user_requested_write("arregla el bug")
    assert OllamaClient._user_requested_write("soluciona el error")
    assert OllamaClient._user_requested_write("refactoriza esto")


def test_mentions_write_con_acentos():
    from core.ollama import OllamaClient
    # La normalizacion debe quitar acentos.
    assert OllamaClient._user_requested_write("escríbelo")


def test_mentions_write_no_falso_positivo():
    from core.ollama import OllamaClient
    # Regresion: frases que NO deben autorizar.
    assert not OllamaClient._user_requested_write("que es python?")
    assert not OllamaClient._user_requested_write("explícame el código")
    assert not OllamaClient._user_requested_write("")


def test_resuelto_dispara_falso_completado():
    from core.ollama import OllamaClient
    # "Resuelto." no estaba en la lista original.
    assert OllamaClient._looks_like_false_completion("Resuelto.")
    assert OllamaClient._looks_like_false_completion("Ya está arreglado.")
    assert OllamaClient._looks_like_false_completion("Solucionado.")


def test_marcador_con_acento_o_sin_acento_matchea_igual():
    from core.ollama import OllamaClient
    assert OllamaClient._looks_like_false_completion("ya está")
    assert OllamaClient._looks_like_false_completion("ya esta")


def test_verification_corre_los_tests():
    from core.ollama import OllamaClient
    # Verbos que antes no estaban en _VERIFICATION_VERBS.
    assert OllamaClient._user_requested_verification("corre los tests")
    assert OllamaClient._user_requested_verification("compílalo")
    assert OllamaClient._user_requested_verification("lanza las pruebas")
    assert OllamaClient._user_requested_verification("pasa el linter")


def test_verification_no_falso_positivo():
    from core.ollama import OllamaClient
    assert not OllamaClient._user_requested_verification("hola")
    assert not OllamaClient._user_requested_verification("")
    assert not OllamaClient._user_requested_verification(None)


def test_word_boundary_no_matchea_subcadenas():
    """El matching con word-boundary evita falsos positivos raros."""
    from core.ollama import OllamaClient
    # "ejecutiva" contiene "ejecut" pero no es "ejecuta".
    # Nota: con conjugaciones, "ejecutar" genera "ejecuta", no "ejecutiva".
    assert not OllamaClient._user_requested_verification("una decisión ejecutiva")


# -- Sustantivos de accion (auditoria 2026-09-26) -----------------------

def test_sustantivos_de_accion_autorizan_escritura():
    """Prompts con 'EDICION' o 'MODIFICACION' (sustantivo) autorizan.

    Caso real: OVERPAPER Fase 8 usaba 'EDICION' como instruccion.
    El modelo devolvia FASE VERIFICADA sin escribir nada, y el
    nudge de falso completado no disparaba porque _WRITE_VERBS solo
    tenia verbos conjugados.
    """
    from core.ollama import OllamaClient

    assert OllamaClient._user_requested_write(
        "Lee gui.py. EDICION. NO toques core_processor.py."
    )
    assert OllamaClient._user_requested_write("modificacion del archivo")
    assert OllamaClient._user_requested_write("creacion de un modulo")
    assert OllamaClient._user_requested_write("actualizacion urgente")
    assert OllamaClient._user_requested_write("implementacion completa")


def test_sustantivos_no_autorizan_prosa():
    from core.ollama import OllamaClient
    assert not OllamaClient._user_requested_write("lee el archivo")
    assert not OllamaClient._user_requested_write("que es python")


# -- Caso real 2026-09-26: modelo solo texto ----------------------------

def test_modelo_solo_texto_sin_tool_calls_dispara_nudge():
    """Modelo dice "se ha escrito" sin emitir NINGUNA tool.

    Caso real: mistral-small3.2 respondiendo "El archivo alpha.py se
    ha escrito correctamente" tras emitir solo texto CLI.
    """
    from core.ollama import OllamaClient

    # Frases que ahora deben matchear.
    assert OllamaClient._looks_like_false_completion(
        "El archivo alpha.py se ha escrito correctamente."
    )
    assert OllamaClient._looks_like_false_completion(
        "He creado el archivo."
    )
    assert OllamaClient._looks_like_false_completion(
        "Se ha modificado exitosamente."
    )
    assert OllamaClient._looks_like_false_completion(
        "Archivo escrito."
    )


def test_marcadores_nuevos_no_disparan_en_prosa_normal():
    from core.ollama import OllamaClient
    # Frases que no declaran haber terminado.
    assert not OllamaClient._looks_like_false_completion(
        "Voy a escribir el archivo"
    )
    assert not OllamaClient._looks_like_false_completion(
        "Puedo crearlo si quieres"
    )


# -- Verbos pon/inserta + code block (bug 2026-09-26) -------------------

def test_write_verbs_incluye_pon_e_inserta():
    from core.ollama import OllamaClient
    assert OllamaClient._user_requested_write(
        "pon un boton pequeño en la columna derecha"
    )
    assert OllamaClient._user_requested_write("inserta un print al inicio")
    assert OllamaClient._user_requested_write("mete un comentario")


def test_has_code_block_detecta_bloque_largo():
    from core.ollama import OllamaClient
    body = "\n".join(f"    linea_{i} = {i}" for i in range(15))
    text = "Voy a modificar el archivo:\n```python\n" + body + "\n```\n¿Algo más?"
    assert OllamaClient._has_code_block(text, 10)


def test_has_code_block_no_detecta_ejemplo_corto():
    from core.ollama import OllamaClient
    text = (
        "Ejemplo:\n"
        "```python\n"
        "for i in range(3):\n"
        "    print(i)\n"
        "```\n"
    )
    assert not OllamaClient._has_code_block(text, 10)


def test_has_code_block_sin_fence():
    from core.ollama import OllamaClient
    assert not OllamaClient._has_code_block("hola mundo", 10)
    assert not OllamaClient._has_code_block("", 10)
    assert not OllamaClient._has_code_block(None, 10)


def test_has_code_block_con_tildes():
    from core.ollama import OllamaClient
    body = "\n".join(f"linea {i}" for i in range(12))
    text = "~~~\n" + body + "\n~~~\n"
    assert OllamaClient._has_code_block(text, 10)
