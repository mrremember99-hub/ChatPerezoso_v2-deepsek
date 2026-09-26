"""H5 (auditoria 2026-09-26): thinking shrink preserva la decision.

Antes: head=2500, tail=1200. El medio (derivacion real) se perdia
y la cabeza (setup) se preservaba. Ahora: head=800, tail=3000,
priorizando la conclusion final (que decide la siguiente ronda).
"""
from __future__ import annotations

from core.ollama import (
    _THINKING_MAX_CHARS,
    _THINKING_KEEP_HEAD,
    _THINKING_KEEP_TAIL,
    _shrink_thinking,
)


def test_tail_preserves_more_than_head():
    """La cola (conclusion) debe pesar mas que la cabeza (setup)."""
    assert _THINKING_KEEP_TAIL > _THINKING_KEEP_HEAD
    assert _THINKING_KEEP_TAIL >= 2000


def test_shrink_preserves_tail_decision():
    """Un thinking con decision al final conserva la decision."""
    setup = "x" * 3000
    derivacion = "y" * 5000
    decision = "VOY A LLAMAR A leer_archivo CON path=main.py"
    text = setup + derivacion + decision
    assert len(text) > _THINKING_MAX_CHARS
    out = _shrink_thinking(text)
    assert decision in out, "la decision final se perdio"
    assert "caracteres de razonamiento omitidos" in out


def test_shrink_no_op_below_cap():
    text = "razonamiento corto"
    assert _shrink_thinking(text) == text
