"""Grupo 2c: P2#11 — multiples tool_calls por turno."""
from __future__ import annotations

import pathlib
from collections.abc import Iterator

from core.harness.model import ModelDelta
from core.harness.policy import AgentSpec, HarnessConfig, ModelSpec
from core.harness.session import HarnessSession


class _MultiModel:
    """Emite N tool_calls en un turno y luego para."""

    def __init__(self, calls: list[tuple[str, dict]]) -> None:
        self._calls = list(calls)
        self._done = False

    def chat(
        self, messages, *, tools=None, stream=True, cancel_event=None,
    ) -> Iterator[ModelDelta]:
        if self._done:
            yield ModelDelta(kind="text", text="fin")
            yield ModelDelta(kind="done")
            return
        self._done = True
        yield ModelDelta(kind="text", text="pensando")
        for name, args in self._calls:
            yield ModelDelta(
                kind="tool_call",
                tool_call={"name": name, "arguments": args},
            )
        yield ModelDelta(kind="done")


class _Reg:
    def __init__(self, *, results: dict[str, str] | None = None) -> None:
        self._results = results or {}
        self.called: list[tuple[str, dict]] = []

    def requires_confirmation(self, _n: str) -> bool:
        return False

    def call(
        self, name, args, *, allow_destructive=False, cancel_event=None,
    ) -> str:
        self.called.append((name, dict(args)))
        return self._results.get(name, f"ok:{name}")


def _cfg(tmp_path: pathlib.Path) -> HarnessConfig:
    return HarnessConfig(
        run_id="r1",
        workspace_root=tmp_path / "ws",
        storage_dir=tmp_path / "store",
        model=ModelSpec(name="m"),
        agent=AgentSpec(name="a"),
    )


# ── Multiples tools en un turno ─────────────────────────────────


def test_dos_tools_en_un_turno_se_ejecutan(tmp_path) -> None:
    reg = _Reg()
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_MultiModel([
            ("leer_archivo", {"path": "a.py"}),
            ("leer_archivo", {"path": "b.py"}),
        ]),
        tool_registry=reg,
    )
    events = list(s.step("lee ambos"))
    completed = [
        e for e in events if e.kind == "tool_call_completed"
    ]
    assert len(completed) == 2
    assert reg.called == [
        ("leer_archivo", {"path": "a.py"}),
        ("leer_archivo", {"path": "b.py"}),
    ]


def test_tres_tools_en_un_turno_se_ejecutan(tmp_path) -> None:
    reg = _Reg()
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_MultiModel([
            ("t", {"i": 1}),
            ("t", {"i": 2}),
            ("t", {"i": 3}),
        ]),
        tool_registry=reg,
    )
    list(s.step("x"))
    assert len(reg.called) == 3


# ── Mensaje assistant con tool_calls en historial ───────────────


def test_assistant_con_tool_calls_en_messages(tmp_path) -> None:
    """La siguiente ronda debe ver que el modelo pidio N tools.

    El historial gana un mensaje assistant con content + tool_calls
    (formato Ollama).
    """
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_MultiModel([
            ("a", {"x": 1}),
            ("b", {"y": 2}),
        ]),
        tool_registry=_Reg(),
    )
    list(s.step("hola"))

    assistants = [
        m for m in s._messages
        if m["role"] == "assistant" and "tool_calls" in m
    ]
    assert len(assistants) == 1
    tc = assistants[0]["tool_calls"]
    assert len(tc) == 2
    assert tc[0]["function"]["name"] == "a"
    assert tc[0]["function"]["arguments"] == {"x": 1}
    assert tc[1]["function"]["name"] == "b"


def test_assistant_sin_tools_no_gana_tool_calls(tmp_path) -> None:
    """Respuesta normal: assistant con content, sin tool_calls."""
    class _NoTools:
        def chat(self, *_a, **_kw) -> Iterator[ModelDelta]:
            yield ModelDelta(kind="text", text="hola")
            yield ModelDelta(kind="done")

    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_NoTools(),
        tool_registry=_Reg(),
    )
    list(s.step("x"))
    assistants = [
        m for m in s._messages if m["role"] == "assistant"
    ]
    assert len(assistants) == 1
    assert "tool_calls" not in assistants[0]


# ── Ordinal en call_id ──────────────────────────────────────────


def test_call_ids_con_ordinal_distinto(tmp_path) -> None:
    """Cada tool del turno tiene un call_id distinto (ordinal 0..N)."""
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_MultiModel([
            ("t", {"i": 1}),
            ("t", {"i": 2}),
            ("t", {"i": 3}),
        ]),
        tool_registry=_Reg(),
    )
    events = list(s.step("x"))
    requested = [
        e for e in events if e.kind == "tool_call_requested"
    ]
    call_ids = [e.call_id for e in requested]
    assert len(call_ids) == 3
    assert len(set(call_ids)) == 3


# ── Orden y parada ante abort ───────────────────────────────────


def test_orden_de_ejecucion(tmp_path) -> None:
    """Las tools se ejecutan en el orden que las emitio el modelo."""
    reg = _Reg()
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_MultiModel([
            ("a", {}),
            ("b", {}),
            ("c", {}),
        ]),
        tool_registry=reg,
    )
    list(s.step("x"))
    assert [n for n, _ in reg.called] == ["a", "b", "c"]


def test_tool_error_no_para_las_siguientes(tmp_path) -> None:
    """Un error de tool no cancela el turno: las siguientes se
    ejecutan (a menos que el loop detector aborte)."""
    reg = _Reg(results={"a": "ERROR: fallo"})
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_MultiModel([
            ("a", {}),
            ("b", {}),
        ]),
        tool_registry=reg,
    )
    list(s.step("x"))
    assert [n for n, _ in reg.called] == ["a", "b"]


def test_cancel_a_mitad_del_turno_para(tmp_path) -> None:
    """Si cancel() se setea tras la primera tool, la segunda no se
    ejecuta."""
    class _CancelAfterFirst(_Reg):
        def call(
            self, name, args, *, allow_destructive=False,
            cancel_event=None,
        ):
            self.called.append((name, dict(args)))
            if len(self.called) == 1:
                s_ref[0].cancel()
            return "ok"

    s_ref: list = []
    ctrl = _CancelAfterFirst()
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_MultiModel([("a", {}), ("b", {})]),
        tool_registry=ctrl,
    )
    s_ref.append(s)
    list(s.step("x"))
    assert [n for n, _ in ctrl.called] == ["a"]
