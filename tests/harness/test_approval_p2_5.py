"""P2#5: el gate de session respeta las reglas de ChatWorker."""
import pathlib

import pytest

from core.approval import is_auto_approved
from core.harness.model import ModelDelta
from core.harness.policy import AgentSpec, HarnessConfig, ModelSpec
from core.harness.session import HarnessSession

ALLOW = lambda c: c.startswith("pytest")  # noqa: E731


def _a(name, args=None, **kw):
    kw.setdefault("auto_approve", True)
    return is_auto_approved(name, args or {}, **kw)


def test_off_when_auto_approve_off() -> None:
    assert _a("escribir_archivo", auto_approve=False) is False


def test_write_auto_approved() -> None:
    assert _a("escribir_archivo") is True


def test_delete_never_auto_approved() -> None:
    assert _a("borrar_archivo") is False


def test_shell_needs_second_gate() -> None:
    args = {"command": "pytest -q"}
    assert _a("ejecutar_comando", args, command_allowed=ALLOW) is False
    assert _a(
        "ejecutar_comando", args,
        auto_approve_shell=True, command_allowed=ALLOW,
    ) is True


def test_shell_outside_allowlist_degrades() -> None:
    assert _a(
        "ejecutar_comando", {"command": "rm -rf x"},
        auto_approve_shell=True, command_allowed=ALLOW,
    ) is False


def test_shell_without_allowlist_is_conservative() -> None:
    assert _a(
        "ejecutar_comando", {"command": "pytest"},
        auto_approve_shell=True,
    ) is False


@pytest.mark.parametrize(
    "args",
    [{}, {"command": ""}, {"command": None}, {"command": 3}, "no-dict"],
)
def test_shell_malformed_passes_to_tool(args) -> None:
    assert _a(
        "ejecutar_comando", args,
        auto_approve_shell=True, command_allowed=ALLOW,
    ) is True


def test_allowlist_exception_is_denied() -> None:
    def boom(_c):
        raise RuntimeError

    assert _a(
        "ejecutar_comando", {"command": "x"},
        auto_approve_shell=True, command_allowed=boom,
    ) is False


# -- integracion con HarnessSession --------------------------------------


class _Model:
    def __init__(self, calls):
        self.calls = list(calls)

    def chat(
        self, messages, *, tools=None, stream=True, cancel_event=None,
    ):
        if self.calls:
            n, a = self.calls.pop(0)
            yield ModelDelta(
                "tool_call", tool_call={"name": n, "arguments": a},
            )
        else:
            yield ModelDelta("text", "fin")
            yield ModelDelta("done")


class _Reg:
    def __init__(self):
        self.executed = []

    def requires_confirmation(self, n):
        return n in {
            "borrar_archivo", "ejecutar_comando", "escribir_archivo",
        }

    def call(
        self, name, args, *, allow_destructive=False,
        cancel_event=None,
    ):
        self.executed.append(name)
        return "ok"


def _run(calls, *, handler=None, **cfg):
    config = HarnessConfig(
        run_id="r", workspace_root=pathlib.Path("/tmp"),
        storage_dir=pathlib.Path("/tmp"), model=ModelSpec("m"),
        agent=AgentSpec("a"), **cfg,
    )
    reg = _Reg()
    s = HarnessSession(
        config, model_client=_Model(calls), tool_registry=reg,
        confirmation_handler=handler, command_allowed=ALLOW,
    )
    list(s.step("x"))
    return reg.executed


def test_session_autopilot_never_runs_delete() -> None:
    assert _run(
        [("borrar_archivo", {"path": "a"})], auto_approve=True,
    ) == []


def test_session_delete_runs_only_if_handler_approves() -> None:
    ex = _run(
        [("borrar_archivo", {"path": "a"})],
        handler=lambda n, a: True, auto_approve=True,
    )
    assert ex == ["borrar_archivo"]


def test_session_shell_double_gate_and_allowlist() -> None:
    ok = ("ejecutar_comando", {"command": "pytest -q"})
    bad = ("ejecutar_comando", {"command": "rm -rf x"})
    assert _run([ok], auto_approve=True) == []
    assert _run(
        [ok], auto_approve=True, auto_approve_shell=True,
    ) == ["ejecutar_comando"]
    assert _run(
        [bad], auto_approve=True, auto_approve_shell=True,
    ) == []
