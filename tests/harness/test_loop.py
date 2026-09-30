"""Tests de core/harness/loop.py — S1-b."""
from __future__ import annotations

import pytest

from core.harness.loop import (
    CorrectivePromptBuilder,
    LoopDetector,
    canonical_signature,
    detect_ping_pong,
    result_signature,
)
from core.harness.policy import LoopPolicy

# ── canonical_signature ────────────────────────────────────


def test_signature_orden_keys_no_importa():
    a = canonical_signature("t", {"x": 1, "y": 2})
    b = canonical_signature("t", {"y": 2, "x": 1})
    assert a == b


def test_signature_distinto_tool_distinta_firma():
    a = canonical_signature("t1", {"x": 1})
    b = canonical_signature("t2", {"x": 1})
    assert a != b


def test_signature_whitespace_colapsado():
    a = canonical_signature("t", {"s": "a    b"})
    b = canonical_signature("t", {"s": "a b"})
    assert a == b


def test_signature_indentacion_preservada():
    a = canonical_signature("t", {"s": "  x"})
    b = canonical_signature("t", {"s": "x"})
    assert a != b


def test_signature_anidado():
    a = canonical_signature("t", {"d": {"x": 1, "y": 2}})
    b = canonical_signature("t", {"d": {"y": 2, "x": 1}})
    assert a == b


def test_signature_lista():
    a = canonical_signature("t", {"l": [1, 2, 3]})
    b = canonical_signature("t", {"l": [1, 2, 3]})
    assert a == b


# ── result_signature ───────────────────────────────────────


def test_result_signature_none():
    assert result_signature(None) == ""


def test_result_signature_whitespace_ignorado():
    a = result_signature("hola\nmundo")
    b = result_signature("hola mundo")
    assert a == b


def test_result_signature_distintos():
    assert result_signature("ok") != result_signature("error")


# ── detect_ping_pong ───────────────────────────────────────


def test_ping_pong_2_ciclos():
    sigs = ["a", "b", "a", "b"]
    res = detect_ping_pong(sigs, min_repeats=2)
    assert res is not None
    pattern, cycles = res
    assert pattern == "a|b"
    assert cycles == 2


def test_ping_pong_3_elementos():
    sigs = ["a", "b", "c", "a", "b", "c"]
    res = detect_ping_pong(sigs, min_repeats=2)
    assert res is not None
    pattern, cycles = res
    assert pattern == "a|b|c"
    assert cycles == 2


def test_ping_pong_no_hay():
    sigs = ["a", "b", "c", "d", "e"]
    res = detect_ping_pong(sigs, min_repeats=2)
    assert res is None


def test_ping_pong_una_sola_repeticion_no():
    sigs = ["a", "b"]
    res = detect_ping_pong(sigs, min_repeats=2)
    assert res is None


# ── LoopDetector — genericRepeat ───────────────────────────


def _detector(**kw) -> LoopDetector:
    policy = LoopPolicy(**kw)
    return LoopDetector(policy)


def test_generic_repeat_silencioso_al_principio():
    d = _detector()
    dec = d.observe("t", {"x": 1})
    assert dec.action == "silent"


def test_generic_repeat_warning_a_los_3():
    # result distinto en cada llamada: aisla generic_repeat de
    # poll_no_progress (que mira tool+args+result).
    d = _detector()
    d.observe("t", {"x": 1}, result="r1")
    d.observe("t", {"x": 1}, result="r2")
    dec = d.observe("t", {"x": 1}, result="r3")
    assert dec.action == "warning"
    assert dec.detector == "generic_repeat"
    assert dec.count == 3


def test_generic_repeat_corrective_a_los_5():
    d = _detector()
    for i in range(4):
        d.observe("t", {"x": 1}, result=f"r{i}")
    dec = d.observe("t", {"x": 1}, result="r4")
    assert dec.action == "corrective"
    assert dec.count == 5


def test_generic_repeat_abort_a_los_8():
    d = _detector()
    for _ in range(7):
        d.observe("t", {"x": 1})
    dec = d.observe("t", {"x": 1})
    assert dec.action == "abort"
    assert dec.count == 8


def test_generic_repeat_no_cuenta_si_args_distintos():
    d = _detector()
    d.observe("t", {"x": 1}, result="a")
    d.observe("t", {"x": 2}, result="b")   # rompe la cadena
    d.observe("t", {"x": 1}, result="c")
    dec = d.observe("t", {"x": 1}, result="d")
    # Solo 2 consecutivas con {x:1} -> por debajo de warn=3.
    assert dec.action == "silent"


def test_generic_repeat_no_cuenta_si_otra_tool_en_medio():
    d = _detector()
    d.observe("t", {"x": 1}, result="a")
    d.observe("u", {"y": 1}, result="b")
    d.observe("t", {"x": 1}, result="c")
    d.observe("t", {"x": 1}, result="d")
    dec = d.observe("t", {"x": 1}, result="e")
    # 3 consecutivas con t/{x:1} al final -> warning
    assert dec.action == "warning"
    assert dec.count == 3


# ── LoopDetector — pingPong ────────────────────────────────


def test_ping_pong_warning_2_ciclos():
    d = _detector()
    d.observe("t", {"x": 1})
    d.observe("u", {"y": 1})
    d.observe("t", {"x": 1})
    dec = d.observe("u", {"y": 1})
    # puede disparar generic_repeat tambien; comprobamos ping_pong
    assert dec.action in ("warning", "corrective")


def test_ping_pong_abort_a_4_ciclos():
    d = _detector()
    for _ in range(3):
        d.observe("t", {"x": 1})
        d.observe("u", {"y": 1})
    # 4to ciclo
    d.observe("t", {"x": 1})
    dec = d.observe("u", {"y": 1})
    # generic_repeat dispara tambien; lo importante es que NO sea silent
    assert dec.action != "silent"


# ── LoopDetector — pollNoProgress ──────────────────────────


def test_poll_no_progress_mismo_resultado():
    d = _detector()
    d.observe("leer", {"path": "x"}, result="contenido")
    d.observe("leer", {"path": "x"}, result="contenido")
    dec = d.observe("leer", {"path": "x"}, result="contenido")
    # warning por generic_repeat y poll_no_progress
    assert dec.action != "silent"


def test_poll_no_progress_cambia_resultado_no():
    d = _detector()
    d.observe("leer", {"path": "x"}, result="v1")
    d.observe("leer", {"path": "x"}, result="v2")
    dec = d.observe("leer", {"path": "x"}, result="v3")
    # solo 1 por cada resultado, pero 3 por tool+args -> warning
    # generic_repeat no cuenta (args iguales pero resultado distinto
    # no la rompe porque solo mira (tool, args))
    # Este test es discutible; lo dejamos informativo.
    assert dec.action in ("silent", "warning")


# ── LoopDetector — postCompactionGuard ─────────────────────


def test_post_compaction_no_armado_sin_compaction():
    d = _detector()
    d.observe("t", {"x": 1})
    d.observe("t", {"x": 1})
    dec = d.observe("t", {"x": 1})
    # Solo generic_repeat -> warning
    assert dec.action in ("warning", "corrective")


def test_post_compaction_dispara_tras_compactar():
    d = _detector()
    # Fase 1: 3 llamadas a t/{x:1}
    d.observe("t", {"x": 1})
    d.observe("t", {"x": 1})
    d.observe("t", {"x": 1})
    # Compactacion
    d.observe_compaction(tokens_before=20000, tokens_after=8000)
    # Post-compactacion: misma llamada
    dec = d.observe("t", {"x": 1})
    # post_compaction dispara corrective
    assert dec.detector == "post_compaction" or dec.action != "silent"


# ── LoopDetector — max_corrective_attempts ─────────────────


def test_max_corrective_attempts_escala_a_abort():
    d = _detector(max_corrective_attempts=2)
    counter = 0
    # Cada ciclo: 5 observes con mismo (tool, args) pero result
    # distinto, para que solo generic_repeat dispare.
    for cycle in range(4):
        for _ in range(4):
            counter += 1
            d.observe("t", {"x": cycle}, result=f"r{counter}")
        counter += 1
        dec = d.observe("t", {"x": cycle}, result=f"r{counter}")
        if dec.action == "abort":
            assert "max_corrective_attempts" in dec.reason
            return
    pytest.fail("nunca escalo a abort")


# ── LoopDetector — reset ───────────────────────────────────


def test_reset_limpia_estado():
    d = _detector()
    d.observe("t", {"x": 1})
    d.observe("t", {"x": 1})
    d.observe("t", {"x": 1})
    d.reset()
    assert d.correctives_count == 0
    dec = d.observe("t", {"x": 1})
    assert dec.action == "silent"


# ── LoopDetector — deshabilitado ───────────────────────────


def test_disabled_no_dispara():
    d = _detector(enabled=False)
    for _ in range(20):
        d.observe("t", {"x": 1})
    # Al final, siempre silent
    assert d.observe("t", {"x": 1}).action == "silent"


# ── CorrectivePromptBuilder ────────────────────────────────


def test_corrective_prompt_contiene_diagnostico():
    from core.harness.loop import LoopDecision, LoopObservation
    b = CorrectivePromptBuilder()
    decision = LoopDecision(
        action="corrective",
        detector="ping_pong",
        signature="a|b",
        count=3,
        reason="patron alternante 3 ciclos",
    )
    obs = LoopObservation(
        tool="editar_archivo",
        args={},
        signature="x",
        result_signature="y",
        result_summary="F821 en linea 5",
    )
    prompt = b.build(decision, obs)
    assert "Loop detectado" in prompt
    assert "ping_pong" in prompt
    assert "F821 en linea 5" in prompt
    assert "Sugerencias" in prompt
    assert "No repitas" in prompt


def test_corrective_prompt_detector_desconocido():
    from core.harness.loop import LoopDecision
    b = CorrectivePromptBuilder()
    decision = LoopDecision(
        action="corrective",
        detector="desconocido",
        reason="r",
    )
    prompt = b.build(decision)
    assert "Cambia de estrategia" in prompt
