"""Tests del modulo de resumen rolling (Hueco 2, paso 1)."""
from __future__ import annotations

from core.session_summary import (
    SUMMARY_HEADER,
    SUMMARY_MAX_CHARS,
    SUMMARY_SECTIONS,
    SessionSummary,
    build_summary_prompt,
    format_summary_block,
)


def test_summary_inicial_vacio():
    s = SessionSummary()
    assert s.text == ""
    assert s.last_message_count == 0
    assert s.cycles == 0


def test_should_update_pocos_mensajes():
    s = SessionSummary()
    assert not s.should_update(10)


def test_should_update_justo_en_umbral():
    s = SessionSummary()
    assert s.should_update(20)


def test_should_update_tras_un_ciclo():
    s = SessionSummary()
    s.apply("resumen 1", 20)
    assert not s.should_update(30)
    assert s.should_update(40)


def test_should_update_cap_ciclos():
    s = SessionSummary()
    s.apply("r1", 20)
    s.apply("r2", 40)
    assert s.cycles == 2
    assert not s.should_update(140)


def test_reset_vuelve_a_cero():
    s = SessionSummary()
    s.apply("r1", 20)
    s.reset()
    assert s.text == ""
    assert s.last_message_count == 0
    assert s.cycles == 0
    assert s.should_update(20)


def test_prompt_ignora_ultimos_n():
    messages = [
        {"role": "user", "content": "viejo 1"},
        {"role": "assistant", "content": "respuesta vieja 1"},
        {"role": "user", "content": "reciente 1"},
        {"role": "assistant", "content": "respuesta reciente 1"},
    ]
    prompt = build_summary_prompt(messages, keep_recent=2)
    assert "viejo 1" in prompt
    assert "respuesta vieja 1" in prompt
    assert "reciente 1" not in prompt


def test_prompt_ignora_rol_tool():
    messages = [
        {"role": "user", "content": "hola"},
        {"role": "tool", "content": "contenido de tool"},
        {"role": "assistant", "content": "ok"},
    ]
    prompt = build_summary_prompt(messages, keep_recent=0)
    assert "contenido de tool" not in prompt
    assert "hola" in prompt
    assert "ok" in prompt


def test_prompt_vacio_si_no_hay_nada_que_resumir():
    assert build_summary_prompt([]) == ""
    msgs = [{"role": "user", "content": "x"}] * 6
    assert build_summary_prompt(msgs, keep_recent=10) == ""


def test_prompt_incluye_secciones_solicitadas():
    msgs = [{"role": "user", "content": "hola"}]
    prompt = build_summary_prompt(msgs, keep_recent=0)
    for name in SUMMARY_SECTIONS:
        assert name in prompt
    assert SUMMARY_HEADER in prompt


def test_prompt_ignora_mensajes_vacios():
    msgs = [
        {"role": "user", "content": ""},
        {"role": "user", "content": "real"},
    ]
    prompt = build_summary_prompt(msgs, keep_recent=0)
    assert "real" in prompt


def test_prompt_ignora_content_no_str():
    msgs = [
        {"role": "user", "content": None},
        {"role": "user", "content": 42},
        {"role": "user", "content": "bueno"},
    ]
    prompt = build_summary_prompt(msgs, keep_recent=0)
    assert "bueno" in prompt
    assert "42" not in prompt


def test_format_vacio():
    assert format_summary_block("") == ""
    assert format_summary_block(None) == ""


def test_format_sin_cabecera_la_anade():
    raw = "· Progreso: hemos hecho X"
    out = format_summary_block(raw)
    assert out.startswith(SUMMARY_HEADER)


def test_format_respeta_cabecera_existente():
    raw = SUMMARY_HEADER + "\n· Progreso: X"
    out = format_summary_block(raw)
    assert out.count(SUMMARY_HEADER) == 1


def test_format_quita_fences():
    raw = "```\n" + SUMMARY_HEADER + "\n· Progreso: X\n```"
    out = format_summary_block(raw)
    assert not out.startswith("```")
    assert not out.endswith("```")
    assert SUMMARY_HEADER in out


def test_format_quita_fences_con_lang():
    raw = "```markdown\n" + SUMMARY_HEADER + "\n· Progreso: X\n```"
    out = format_summary_block(raw)
    assert "```" not in out


def test_format_capa_longitud():
    raw = "\n".join(["· Progreso: " + "x" * 200] * 100)
    out = format_summary_block(raw)
    assert len(out) <= SUMMARY_MAX_CHARS
    assert "truncado" in out


def test_format_no_capa_si_cabe():
    raw = "· Progreso: corto"
    out = format_summary_block(raw)
    assert "truncado" not in out
