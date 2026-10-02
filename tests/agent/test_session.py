"""Tests del núcleo del agente (v3).

~30 tests sobre AgentSession. Sin Qt. Modelo scripted.
"""
from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from core.agent.events import (
    AgentError,
    ConfirmationRequested,
    ConfirmationResolved,
    MessageCompleted,
    MessageDelta,
    RunEnded,
    RunStarted,
    StepEnded,
    StepStarted,
    ToolCallCompleted,
    ToolCallRequested,
    VerificationRun,
)
from core.agent.model import ModelDelta
from core.agent.policy import AgentConfig, AgentSpec, ModelSpec
from core.agent.session import AgentSession


# ── Helpers ────────────────────────────────────────────────────

def _cfg(**overrides: Any) -> AgentConfig:
    defaults = {
        "run_id": "r1",
        "workspace_root": Path("/tmp"),
        "model": ModelSpec(name="q"),
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


class _ScriptedModel:
    """Devuelve una lista fija de deltas por cada chat()."""

    def __init__(self, rounds: list[list[ModelDelta]]) -> None:
        self._rounds = list(rounds)
        self.calls: list[dict] = []

    def chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        stream: bool = True,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[ModelDelta]:
        self.calls.append({"messages": list(messages), "tools": tools})
        if not self._rounds:
            yield ModelDelta(kind="done")
            return
        for d in self._rounds.pop(0):
            yield d


def _text(s: str) -> ModelDelta:
    return ModelDelta(kind="text", text=s)


def _tool(name: str, args: dict | None = None) -> ModelDelta:
    return ModelDelta(
        kind="tool_call",
        tool_call={"name": name, "arguments": args or {}},
    )


def _done() -> ModelDelta:
    return ModelDelta(kind="done")


class _FakeRegistry:
    def __init__(
        self,
        *,
        requires: set[str] | None = None,
        results: dict[str, str] | None = None,
        definitions: list[dict] | None = None,
    ) -> None:
        self._requires = requires or set()
        self._results = results or {}
        self._definitions = definitions or []
        self.calls: list[tuple[str, dict]] = []

    def requires_confirmation(self, name: str) -> bool:
        return name in self._requires

    def call(
        self,
        name: str,
        args: dict,
        *,
        allow_destructive: bool = False,
        cancel_event: Any = None,
    ) -> str:
        self.calls.append((name, dict(args)))
        return self._results.get(name, f"ok:{name}")

    def definitions(self) -> list[dict]:
        return list(self._definitions)


def _collect(gen) -> list:
    return list(gen)


def _events_of(events, cls) -> list:
    return [e for e in events if isinstance(e, cls)]


# ── Tests: step básico ────────────────────────────────────────

def test_step_sin_tools_emite_secuencia_completa():
    model = _ScriptedModel([[_text("hola "), _text("mundo"), _done()]])
    s = AgentSession(_cfg(), model_client=model)
    events = _collect(s.step("hey"))

    kinds = [type(e).__name__ for e in events]
    assert kinds[0] == "RunStarted"
    assert "StepStarted" in kinds
    assert "MessageDelta" in kinds
    assert "MessageCompleted" in kinds
    assert kinds[-1] == "StepEnded"
    assert _events_of(events, StepEnded)[-1].outcome == "ok"

    deltas = _events_of(events, MessageDelta)
    assert "".join(d.content for d in deltas) == "hola mundo"


def test_step_encadena_segundo_step_sin_runstarted():
    model = _ScriptedModel([
        [_text("uno"), _done()],
        [_text("dos"), _done()],
    ])
    s = AgentSession(_cfg(), model_client=model)
    e1 = _collect(s.step("a"))
    e2 = _collect(s.step("b"))
    assert len(_events_of(e1, RunStarted)) == 1
    assert len(_events_of(e2, RunStarted)) == 0
    assert _events_of(e2, StepStarted)[0].step_index == 1


def test_step_no_reentrante():
    s = AgentSession(_cfg(), model_client=_ScriptedModel([]))
    gen1 = s.step("a")
    next(gen1)
    with pytest.raises(RuntimeError, match="no es reentrante"):
        list(s.step("b"))


# ── Tests: tool calls ─────────────────────────────────────────

def test_una_tool_call():
    model = _ScriptedModel([
        [_tool("leer_archivo", {"path": "x.py"}), _done()],
        [_text("listo"), _done()],
    ])
    reg = _FakeRegistry(results={"leer_archivo": "contenido"})
    s = AgentSession(_cfg(), model_client=model, tool_registry=reg)

    events = _collect(s.step("lee x"))
    req = _events_of(events, ToolCallRequested)
    done = _events_of(events, ToolCallCompleted)
    assert len(req) == 1
    assert len(done) == 1
    assert done[0].status == "ok"
    assert done[0].summary.startswith("contenido")
    assert reg.calls == [("leer_archivo", {"path": "x.py"})]


def test_dos_tools_en_una_ronda():
    model = _ScriptedModel([
        [
            _tool("leer_archivo", {"path": "a"}),
            _tool("leer_archivo", {"path": "b"}),
            _done(),
        ],
        [_text("fin"), _done()],
    ])
    reg = _FakeRegistry()
    s = AgentSession(_cfg(), model_client=model, tool_registry=reg)
    events = _collect(s.step("lee dos"))
    assert len(_events_of(events, ToolCallRequested)) == 2
    assert len(reg.calls) == 2


def test_tool_call_id_distinto_por_ordinal():
    model = _ScriptedModel([
        [_tool("a"), _tool("a"), _done()],
        [_text("ok"), _done()],
    ])
    s = AgentSession(_cfg(), model_client=model, tool_registry=_FakeRegistry())
    events = _collect(s.step("x"))
    ids = [e.call_id for e in _events_of(events, ToolCallRequested)]
    assert len(ids) == 2
    assert ids[0] != ids[1]


def test_tool_error_marca_step_failed():
    model = _ScriptedModel([
        [_tool("rompe"), _done()],
    ])
    reg = _FakeRegistry(results={"rompe": "ERROR: fallo interno"})
    s = AgentSession(_cfg(), model_client=model, tool_registry=reg)
    events = _collect(s.step("x"))
    step_ended = _events_of(events, StepEnded)[-1]
    assert step_ended.outcome == "ok"  # el error lo ve el modelo
    done = _events_of(events, ToolCallCompleted)[0]
    assert done.status == "error"


def test_tool_fuera_de_allowed_tools():
    cfg = _cfg(agent=AgentSpec(
        name="chat", allowed_tools=["leer_archivo"],
    ))
    model = _ScriptedModel([
        [_tool("escribir_archivo", {"path": "x"}), _done()],
    ])
    reg = _FakeRegistry()
    s = AgentSession(cfg, model_client=model, tool_registry=reg)
    events = _collect(s.step("x"))
    assert reg.calls == []
    done = _events_of(events, ToolCallCompleted)[0]
    assert done.status == "error"
    assert "no permitida" in done.detail


def test_tool_call_sin_name_emite_error():
    model = _ScriptedModel([
        [ModelDelta(kind="tool_call", tool_call={"arguments": {}}), _done()],
    ])
    s = AgentSession(_cfg(), model_client=model, tool_registry=_FakeRegistry())
    events = _collect(s.step("x"))
    errors = _events_of(events, AgentError)
    assert any("sin 'name'" in e.message for e in errors)


def test_tool_call_args_invalidos_emite_error():
    model = _ScriptedModel([
        [ModelDelta(kind="tool_call", tool_call={"name": "x", "arguments": "no-dict"}), _done()],
    ])
    s = AgentSession(_cfg(), model_client=model, tool_registry=_FakeRegistry())
    events = _collect(s.step("x"))
    errors = _events_of(events, AgentError)
    assert any("arguments invalido" in e.message for e in errors)


def test_max_tool_rounds_excedido():
    rounds = [
        [_tool("a"), _done()] for _ in range(3)
    ]
    model = _ScriptedModel(rounds)
    reg = _FakeRegistry()
    s = AgentSession(
        _cfg(max_tool_rounds=2),
        model_client=model,
        tool_registry=reg,
    )
    events = _collect(s.step("x"))
    errors = _events_of(events, AgentError)
    assert any("max_tool_rounds=2" in e.message for e in errors)


# ── Tests: gate de confirmación ───────────────────────────────

def test_tool_sin_confirmacion_auto_approved_true():
    model = _ScriptedModel([
        [_tool("leer"), _done()],
        [_text("ok"), _done()],
    ])
    reg = _FakeRegistry(requires=set())
    s = AgentSession(_cfg(), model_client=model, tool_registry=reg)
    events = _collect(s.step("x"))
    req = _events_of(events, ToolCallRequested)[0]
    assert req.auto_approved is True


def test_tool_requiere_confirmacion_handler_aprueba():
    model = _ScriptedModel([
        [_tool("borrar_archivo", {"path": "x"}), _done()],
        [_text("ok"), _done()],
    ])
    reg = _FakeRegistry(requires={"borrar_archivo"})
    calls: list = []

    def handler(name, args, *, reason=""):
        calls.append((name, args, reason))
        return True

    s = AgentSession(
        _cfg(),
        model_client=model,
        tool_registry=reg,
        confirmation_handler=handler,
    )
    events = _collect(s.step("x"))
    assert calls and calls[0][0] == "borrar_archivo"
    assert len(_events_of(events, ConfirmationRequested)) == 1
    resolved = _events_of(events, ConfirmationResolved)[0]
    assert resolved.approved is True
    assert reg.calls == [("borrar_archivo", {"path": "x"})]


def test_tool_requiere_confirmacion_handler_deniega():
    model = _ScriptedModel([
        [_tool("borrar_archivo", {"path": "x"}), _done()],
    ])
    reg = _FakeRegistry(requires={"borrar_archivo"})

    def handler(name, args, *, reason=""):
        return False

    s = AgentSession(
        _cfg(),
        model_client=model,
        tool_registry=reg,
        confirmation_handler=handler,
    )
    events = _collect(s.step("x"))
    assert reg.calls == []
    done = _events_of(events, ToolCallCompleted)[0]
    assert done.status == "cancelled"


def test_handler_lanza_excepcion_se_trata_como_denegado():
    model = _ScriptedModel([
        [_tool("borrar_archivo", {"path": "x"}), _done()],
    ])
    reg = _FakeRegistry(requires={"borrar_archivo"})

    def handler(name, args, *, reason=""):
        raise RuntimeError("boom")

    s = AgentSession(
        _cfg(),
        model_client=model,
        tool_registry=reg,
        confirmation_handler=handler,
    )
    events = _collect(s.step("x"))
    assert reg.calls == []
    done = _events_of(events, ToolCallCompleted)[0]
    assert done.status == "error"
    assert "fallo" in done.detail


def test_sin_handler_deniega_por_defecto():
    model = _ScriptedModel([
        [_tool("borrar_archivo", {"path": "x"}), _done()],
    ])
    reg = _FakeRegistry(requires={"borrar_archivo"})
    s = AgentSession(_cfg(), model_client=model, tool_registry=reg)
    events = _collect(s.step("x"))
    assert reg.calls == []
    done = _events_of(events, ToolCallCompleted)[0]
    assert done.status == "cancelled"
    assert "confirmation_handler" in done.detail


def test_auto_approve_evita_handler_tool_normal():
    model = _ScriptedModel([
        [_tool("escribir_archivo", {"path": "x"}), _done()],
        [_text("ok"), _done()],
    ])
    reg = _FakeRegistry(requires={"escribir_archivo"})
    handler_calls: list = []

    def handler(name, args, *, reason=""):
        handler_calls.append(name)
        return True

    s = AgentSession(
        _cfg(auto_approve=True),
        model_client=model,
        tool_registry=reg,
        confirmation_handler=handler,
    )
    _collect(s.step("x"))
    assert handler_calls == []
    assert reg.calls == [("escribir_archivo", {"path": "x"})]


def test_borrar_archivo_nunca_auto_aprobado():
    """Convencion P3: operacion destructiva, nunca auto-aprobada."""
    model = _ScriptedModel([
        [_tool("borrar_archivo", {"path": "x"}), _done()],
        [_text("ok"), _done()],
    ])
    reg = _FakeRegistry(requires={"borrar_archivo"})
    handler_calls: list = []

    def handler(name, args, *, reason=""):
        handler_calls.append(name)
        return True

    s = AgentSession(
        _cfg(auto_approve=True),
        model_client=model,
        tool_registry=reg,
        confirmation_handler=handler,
    )
    _collect(s.step("x"))
    assert handler_calls == ["borrar_archivo"]
    assert reg.calls == [("borrar_archivo", {"path": "x"})]


def test_ejecutar_comando_sin_auto_approve_shell_deniega():
    model = _ScriptedModel([
        [_tool("ejecutar_comando", {"command": "ls"}), _done()],
    ])
    reg = _FakeRegistry(requires={"ejecutar_comando"})
    s = AgentSession(
        _cfg(auto_approve=True, auto_approve_shell=False),
        model_client=model,
        tool_registry=reg,
    )
    events = _collect(s.step("x"))
    assert reg.calls == []
    done = _events_of(events, ToolCallCompleted)[0]
    assert done.status == "cancelled"


def test_ejecutar_comando_con_allowlist_pasa():
    model = _ScriptedModel([
        [_tool("ejecutar_comando", {"command": "ls -la"}), _done()],
        [_text("ok"), _done()],
    ])
    reg = _FakeRegistry(requires={"ejecutar_comando"})
    s = AgentSession(
        _cfg(auto_approve=True, auto_approve_shell=True),
        model_client=model,
        tool_registry=reg,
        command_allowed=lambda cmd: cmd.startswith("ls"),
    )
    _collect(s.step("x"))
    assert reg.calls == [("ejecutar_comando", {"command": "ls -la"})]


def test_ejecutar_comando_fuera_de_allowlist_deniega():
    model = _ScriptedModel([
        [_tool("ejecutar_comando", {"command": "rm -rf /"}), _done()],
    ])
    reg = _FakeRegistry(requires={"ejecutar_comando"})
    s = AgentSession(
        _cfg(auto_approve=True, auto_approve_shell=True),
        model_client=model,
        tool_registry=reg,
        command_allowed=lambda cmd: cmd.startswith("ls"),
    )
    events = _collect(s.step("x"))
    assert reg.calls == []
    done = _events_of(events, ToolCallCompleted)[0]
    assert done.status == "cancelled"


# ── Tests: cancelación ────────────────────────────────────────

def test_cancel_antes_del_step_marca_cancelled():
    s = AgentSession(_cfg(), model_client=_ScriptedModel([]))
    s.cancel()
    events = _collect(s.step("x"))
    step_ended = _events_of(events, StepEnded)[-1]
    assert step_ended.outcome == "cancelled"


def test_cancel_entre_steps_afecta_al_siguiente():
    """Semantica P2#10: cancel() entre steps se honra en el siguiente."""
    model = _ScriptedModel([[_text("ok"), _done()]])
    s = AgentSession(_cfg(), model_client=model)
    _collect(s.step("a"))
    s.cancel()
    model2 = _ScriptedModel([[_text("ok"), _done()]])
    s.model_client = model2
    events = _collect(s.step("b"))
    step_ended = _events_of(events, StepEnded)[-1]
    assert step_ended.outcome == "cancelled"


# ── Tests: verificador hook ───────────────────────────────────

def test_verificador_hook_se_invoca_tras_escritura():
    model = _ScriptedModel([
        [_tool("escribir_archivo", {"path": "x.py"}), _done()],
        [_text("ok"), _done()],
    ])
    reg = _FakeRegistry(results={"escribir_archivo": "guardado"})
    calls: list[str] = []

    def hook(rel: str) -> str:
        calls.append(rel)
        return "issue en x.py"

    s = AgentSession(
        _cfg(),
        model_client=model,
        tool_registry=reg,
        verificador_hook=hook,
    )
    events = _collect(s.step("escribe"))
    assert calls == ["x.py"]
    done = _events_of(events, ToolCallCompleted)[0]
    assert "[VERIFICACIÓN]" in done.detail


def test_verificador_hook_no_en_tool_lectura():
    model = _ScriptedModel([
        [_tool("leer_archivo", {"path": "x.py"}), _done()],
        [_text("ok"), _done()],
    ])
    reg = _FakeRegistry()
    calls: list[str] = []
    s = AgentSession(
        _cfg(),
        model_client=model,
        tool_registry=reg,
        verificador_hook=lambda rel: (calls.append(rel), "x")[1],
    )
    _collect(s.step("x"))
    assert calls == []


# ── Tests: completion verification ────────────────────────────

def test_completion_verification_off_no_emite_evento():
    model = _ScriptedModel([[_text("ok"), _done()]])
    s = AgentSession(_cfg(), model_client=model)
    events = _collect(s.step("FASE 1 — x\nVERIFICACIÓN FASE 1\necho"))
    assert _events_of(events, VerificationRun) == []


def test_completion_verification_on_con_prompt_overpaper():
    prompt = (
        "FASE 1 — Ventana basica\n"
        "haz algo\n\n"
        "━━━ VERIFICACIÓN FASE 1 ━━━\n"
        "python -m py_compile gui.py\n"
    )
    model = _ScriptedModel([[_text("ok"), _done()]])
    s = AgentSession(
        _cfg(completion_verification_enabled=True),
        model_client=model,
    )
    events = _collect(s.step(prompt))
    vrs = _events_of(events, VerificationRun)
    assert len(vrs) == 1
    assert "phase-1" in vrs[0].target


def test_completion_verification_on_sin_fases_no_emite():
    model = _ScriptedModel([[_text("ok"), _done()]])
    s = AgentSession(
        _cfg(completion_verification_enabled=True),
        model_client=model,
    )
    events = _collect(s.step("hola"))
    assert _events_of(events, VerificationRun) == []


# ── Tests: close, load_history, health ────────────────────────

def test_close_emite_run_ended_una_vez():
    s = AgentSession(_cfg(), model_client=_ScriptedModel([]))
    e1 = _collect(s.close(reason="completed", summary="fin"))
    e2 = _collect(s.close())
    assert len(_events_of(e1, RunEnded)) == 1
    assert e2 == []


def test_load_history_filtra_roles_invalidos():
    s = AgentSession(_cfg(), model_client=_ScriptedModel([]))
    s.load_history([
        {"role": "user", "content": "a"},
        {"role": "system", "content": "ignorar"},
        {"role": "assistant", "content": "b"},
        {"role": "tool", "content": "c"},
        {"role": "raro", "content": "d"},
        "no-dict",
    ])
    msgs = s._messages
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant", "tool"]


def test_load_history_copia_defensiva():
    s = AgentSession(_cfg(), model_client=_ScriptedModel([]))
    src = [{"role": "user", "content": "a"}]
    s.load_history(src)
    src[0]["content"] = "mutado"
    assert s._messages[0]["content"] == "a"


def test_health_basico():
    s = AgentSession(_cfg(), model_client=_ScriptedModel([]))
    h = s.health()
    assert h == {
        "run_id": "r1",
        "messages": 0,
        "steps": 0,
        "errors": 0,
    }


# ── Tests: allowed_tools / definiciones ──────────────────────

def test_is_tool_allowed_none_permite_todo():
    s = AgentSession(_cfg(), model_client=_ScriptedModel([]))
    assert s._is_tool_allowed("cualquiera") is True


def test_is_tool_allowed_lista():
    cfg = _cfg(agent=AgentSpec(name="chat", allowed_tools=["a", "b"]))
    s = AgentSession(cfg, model_client=_ScriptedModel([]))
    assert s._is_tool_allowed("a") is True
    assert s._is_tool_allowed("c") is False


def test_is_tool_allowed_wildcard_mcp():
    cfg = _cfg(agent=AgentSpec(name="chat", allowed_tools=["mcp__*"]))
    s = AgentSession(cfg, model_client=_ScriptedModel([]))
    assert s._is_tool_allowed("mcp__fs__read") is True
    assert s._is_tool_allowed("leer_archivo") is False


def test_tools_block_con_tools():
    defs = [
        {"function": {"name": "leer_archivo"}},
        {"function": {"name": "escribir_archivo"}},
    ]
    reg = _FakeRegistry(definitions=defs)
    s = AgentSession(_cfg(), model_client=_ScriptedModel([]), tool_registry=reg)
    block = s._tools_block()
    assert "leer_archivo" in block
    assert "escribir_archivo" in block


def test_tools_block_sin_registry():
    s = AgentSession(_cfg(), model_client=_ScriptedModel([]))
    assert s._tools_block() == ""
