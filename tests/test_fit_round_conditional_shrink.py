"""H17 (auditoria 2026-09-26): shrink condicional de tool results.

Antes, todo tool result > 100 lineas se cortaba SIEMPRE antes de
fit(), aunque hubiera presupuesto. Ahora, fit() corre primero sin
shrink; solo se shrinkea si hay overflow o dropped_messages.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from core.ollama import OllamaClient
from core.context_window import ContextBudget


def _client():
    return OllamaClient.__new__(OllamaClient)


def _tool_result_msg(content: str) -> dict:
    return {"role": "tool", "content": content}


def test_no_shrink_when_everything_fits():
    """Con presupuesto holgado, no se trunca el tool result."""
    client = _client()
    cw = MagicMock()
    cw.fit.return_value = ([], ContextBudget(
        limit_tokens=32000, output_reserve=1024, prompt_budget=27000,
        estimated_prompt=2000, dropped_messages=0, overflow=False,
    ))
    history = [
        {"role": "system", "content": "sys"},
        _tool_result_msg("\n".join(f"linea {i}" for i in range(200))),
    ]
    client._fit_round_history(cw, history, [], cache=None)
    assert cw.fit.call_count == 1
    passed = cw.fit.call_args.kwargs["messages"]
    assert len(passed) == 1
    assert passed[0]["content"].count("\n") == 199


def test_shrink_applied_when_overflow():
    """Con overflow, se shrinkea y se reintenta fit()."""
    client = _client()
    cw = MagicMock()
    cw.fit.side_effect = [
        ([], ContextBudget(
            limit_tokens=32000, output_reserve=1024, prompt_budget=27000,
            estimated_prompt=30000, dropped_messages=0, overflow=True,
        )),
        ([], ContextBudget(
            limit_tokens=32000, output_reserve=1024, prompt_budget=27000,
            estimated_prompt=25000, dropped_messages=0, overflow=False,
        )),
    ]
    history = [
        {"role": "system", "content": "sys"},
        _tool_result_msg("\n".join(f"linea {i}" for i in range(200))),
    ]
    client._fit_round_history(cw, history, [], cache=None)
    assert cw.fit.call_count == 2
    passed2 = cw.fit.call_args_list[1].kwargs["messages"]
    assert "lineas omitidas por tamano" in passed2[0]["content"]


def test_shrink_applied_when_dropped_messages():
    """Con dropped_messages > 0 tambien se shrinkea."""
    client = _client()
    cw = MagicMock()
    cw.fit.side_effect = [
        ([], ContextBudget(
            limit_tokens=32000, output_reserve=1024, prompt_budget=27000,
            estimated_prompt=28000, dropped_messages=3, overflow=False,
        )),
        ([], ContextBudget(
            limit_tokens=32000, output_reserve=1024, prompt_budget=27000,
            estimated_prompt=25000, dropped_messages=0, overflow=False,
        )),
    ]
    history = [
        {"role": "system", "content": "sys"},
        _tool_result_msg("\n".join(f"linea {i}" for i in range(200))),
    ]
    client._fit_round_history(cw, history, [], cache=None)
    assert cw.fit.call_count == 2
