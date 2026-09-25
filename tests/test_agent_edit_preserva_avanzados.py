"""Regresión #8 y #6.

#8 — Editar un agente no debe perder top_p/top_k/repeat_penalty.
#6 — from_dict no debe colapsar 0/0.0 a None.
"""
from __future__ import annotations


from core.agents import Agent


def test_from_dict_preserva_top_k_cero():
    """top_k=0 es válido (desactivar top_k). No debe volverse None."""
    a = Agent.from_dict({"name": "test", "top_k": 0})
    assert a is not None
    assert a.top_k == 0


def test_from_dict_preserva_repeat_penalty_cero():
    """repeat_penalty=0.0 es válido. No debe volverse None."""
    a = Agent.from_dict({"name": "test", "repeat_penalty": 0.0})
    assert a is not None
    assert a.repeat_penalty == 0.0


def test_from_dict_preserva_top_k_positivo():
    a = Agent.from_dict({"name": "test", "top_k": 40})
    assert a is not None
    assert a.top_k == 40


def test_from_dict_preserva_repeat_penalty_positivo():
    a = Agent.from_dict({"name": "test", "repeat_penalty": 1.1})
    assert a is not None
    assert a.repeat_penalty == 1.1


def test_from_dict_sin_top_k_es_none():
    a = Agent.from_dict({"name": "test"})
    assert a is not None
    assert a.top_k is None
    assert a.repeat_penalty is None
    