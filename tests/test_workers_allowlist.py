"""E.2: integracion de la allowlist shell en ChatWorker._call_tool.

Con autopilot+shell ON:
  - comando en allowlist  -> tools.call(allow_destructive=True), sin dialogo.
  - comando fuera         -> _request_confirmation, allow_destructive=False.
Con autopilot+shell OFF:
  - la logica de allowlist no interviene; se pide confirmacion igual.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _FakeTools:
    """Registra cada call() con sus kwargs y su requires_confirmation."""

    def __init__(self, *, requires: bool = True) -> None:
        self.calls: list[tuple[str, dict, dict]] = []
        self._requires = requires

    def requires_confirmation(self, name: str) -> bool:
        return self._requires

    def call(self, name, arguments, **kwargs):
        self.calls.append((name, dict(arguments), dict(kwargs)))
        return "ok"


class _FakeClient:
    pass


def _make_worker(*, auto_approve: bool, auto_approve_shell: bool):
    from ui.workers import ChatWorker

    tools = _FakeTools(requires=True)
    w = ChatWorker(
        client=_FakeClient(),
        model="test",
        messages=[],
        tools=tools,
        auto_approve=auto_approve,
        auto_approve_shell=auto_approve_shell,
    )
    return w, tools


def test_comando_en_allowlist_se_auto_aprueba(qapp):
    w, tools = _make_worker(auto_approve=True, auto_approve_shell=True)
    result = w._call_tool("ejecutar_comando", {"command": "ls -la"})
    assert result == "ok"
    assert len(tools.calls) == 1
    name, _args, kwargs = tools.calls[0]
    assert name == "ejecutar_comando"
    assert kwargs.get("allow_destructive") is True


def test_comando_fuera_de_allowlist_degrada_a_confirmacion(qapp, monkeypatch):
    w, tools = _make_worker(auto_approve=True, auto_approve_shell=True)

    calls: list = []

    def fake_confirm(name, arguments):
        calls.append((name, dict(arguments)))
        return ("confirmado por test", 0)

    monkeypatch.setattr(w, "_request_confirmation", fake_confirm)

    result = w._call_tool("ejecutar_comando", {"command": "rm -rf /"})
    assert result == "confirmado por test"
    assert len(calls) == 1
    # No se llamo tools.call directamente (no auto-aprobado).
    assert len(tools.calls) == 0


def test_sin_auto_shell_la_allowlist_no_interviene(qapp, monkeypatch):
    """Con auto_shell OFF, la logica E.2 no aplica: se confirma igual."""
    w, tools = _make_worker(auto_approve=True, auto_approve_shell=False)

    calls: list = []

    def fake_confirm(name, arguments):
        calls.append((name, dict(arguments)))
        return ("bloqueado", 0)

    monkeypatch.setattr(w, "_request_confirmation", fake_confirm)

    result = w._call_tool("ejecutar_comando", {"command": "ls"})
    assert result == "bloqueado"
    assert len(calls) == 1
    assert len(tools.calls) == 0


def test_otras_tools_auto_aprobadas_intactas(qapp):
    """crear_archivo sigue auto-aprobada con autopilot (no la toca E.2)."""
    w, tools = _make_worker(auto_approve=True, auto_approve_shell=True)
    result = w._call_tool("crear_archivo", {"path": "x.txt", "content": "y"})
    assert result == "ok"
    assert len(tools.calls) == 1
    name, _args, kwargs = tools.calls[0]
    assert name == "crear_archivo"
    assert kwargs.get("allow_destructive") is True


def test_metacaracteres_degrada_a_confirmacion(qapp, monkeypatch):
    """ls; rm -rf / no pasa _validate_command -> degrada."""
    w, tools = _make_worker(auto_approve=True, auto_approve_shell=True)

    calls: list = []

    def fake_confirm(name, arguments):
        calls.append((name, dict(arguments)))
        return ("bloqueado por metacaracteres", 0)

    monkeypatch.setattr(w, "_request_confirmation", fake_confirm)

    result = w._call_tool(
        "ejecutar_comando", {"command": "ls; rm -rf /"},
    )
    assert result == "bloqueado por metacaracteres"
    assert len(calls) == 1
    assert len(tools.calls) == 0
