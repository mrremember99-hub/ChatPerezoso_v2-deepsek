"""P1#8: _truncate_by_lines nunca debe exceder max_tokens.

La nota "[... truncado ...]" se estimaba con estimate_tokens y se
descontaba del presupuesto, pero cuando note_tokens > max_tokens el
early-return devolvia la nota entera sin comprobar. Ahora devuelve ""
en ese caso degenerado. La rama best>0 y el fallback por caracteres
tienen loop de ajuste porque estimate_tokens no es aditivo.
"""
from __future__ import annotations

from core.context_window import ContextWindow


def _w() -> ContextWindow:
    return ContextWindow(limit_tokens=8192)


def test_max_tokens_cero_devuelve_vacio():
    assert _w()._truncate_by_lines("hola\nmundo\n", 0) == ""


def test_max_tokens_negativo_devuelve_vacio():
    assert _w()._truncate_by_lines("hola\nmundo\n", -5) == ""


def test_texto_corto_se_preserva_como_prefijo():
    """La funcion SIEMPRE anade la nota; el texto original va delante."""
    w = _w()
    text = "a\nb\nc\nd\ne\n"
    out = w._truncate_by_lines(text, 8192)
    assert out.startswith(text)


def test_nota_no_cabe_devuelve_vacio():
    """max_tokens < note_tokens: la nota sola excede. Contrato: ""."""
    w = _w()
    text = "contenido\n" * 100
    out = w._truncate_by_lines(text, 1)
    assert w.estimate_tokens(out) <= 1


def test_texto_largo_respeta_presupuesto():
    w = _w()
    text = ("linea de codigo con parentesis() y llaves{}\n" * 500)
    for budget in (10, 50, 200, 1000):
        out = w._truncate_by_lines(text, budget)
        assert w.estimate_tokens(out) <= budget, f"budget={budget}"


def test_nunca_devuelve_mas_que_max_tokens():
    """Barrido: cualquier presupuesto razonable debe respetarse."""
    w = _w()
    text = ("palabra " * 50 + "\n") * 100
    for budget in range(1, 300, 7):
        out = w._truncate_by_lines(text, budget)
        assert w.estimate_tokens(out) <= budget, f"budget={budget}"
