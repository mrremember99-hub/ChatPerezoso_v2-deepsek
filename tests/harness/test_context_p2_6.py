"""P2#6: el modelo recibe system prompt y tools filtradas."""
import pathlib

from core.harness.model import ModelDelta
from core.harness.policy import AgentSpec, HarnessConfig, ModelSpec
from core.harness.session import HarnessSession


def _defs(*names):
    return [
        {"type": "function", "function": {"name": n}}
        for n in names
    ]


class _Model:
    def __init__(self, script=()):
        self.script = list(script)
        self.calls = []

    def chat(
        self, messages, *, tools=None, stream=True, cancel_event=None,
    ):
        self.calls.append((list(messages), tools))
        if self.script:
            n = self.script.pop(0)
            yield ModelDelta(
                "tool_call", tool_call={"name": n, "arguments": {}},
            )
        else:
            yield ModelDelta("text", "fin")
            yield ModelDelta("done")


class _Reg:
    def __init__(self, names, *, broken=False):
        self.names = names
        self.broken = broken
        self.executed: list[str] = []

    def definitions(self):
        if self.broken:
            raise RuntimeError("x")
        return _defs(*self.names)

    def requires_confirmation(self, n):
        return False

    def call(
        self, name, args, *, allow_destructive=False,
        cancel_event=None,
    ):
        self.executed.append(name)
        return "ok"


def _s(model, reg=None, *, prompt="SYS", allowed=None):
    cfg = HarnessConfig(
        run_id="r", workspace_root=pathlib.Path("/tmp"),
        storage_dir=pathlib.Path("/tmp"), model=ModelSpec("m"),
        agent=AgentSpec(
            "a", system_prompt=prompt, allowed_tools=allowed,
        ),
    )
    return HarnessSession(cfg, model_client=model, tool_registry=reg)


def _names(tools):
    return [t["function"]["name"] for t in tools or []]


def test_system_prompt_first_and_not_accumulated() -> None:
    m = _Model(["leer_archivo"])
    s = _s(m, _Reg(["leer_archivo"]))
    list(s.step("hola"))
    assert len(m.calls) == 2
    for msgs, _ in m.calls:
        assert msgs[0] == {"role": "system", "content": "SYS"}
        assert sum(x["role"] == "system" for x in msgs) == 1
    assert all(x["role"] != "system" for x in s._messages)


def test_no_system_message_when_prompt_empty() -> None:
    m = _Model()
    list(_s(m, prompt="").step("hola"))
    assert [x["role"] for x in m.calls[0][0]] == ["user"]


def test_tools_sent_and_filtered() -> None:
    m = _Model()
    reg = _Reg(["leer_archivo", "borrar_archivo", "mcp__git_status"])
    list(_s(m, reg, allowed=["leer_archivo"]).step("x"))
    assert _names(m.calls[0][1]) == ["leer_archivo"]


def test_all_tools_when_allowed_none_and_mcp_wildcard() -> None:
    m = _Model()
    reg = _Reg(["a", "mcp__b"])
    list(_s(m, reg).step("x"))
    assert _names(m.calls[0][1]) == ["a", "mcp__b"]
    m2 = _Model()
    list(_s(m2, reg, allowed=["mcp__*"]).step("x"))
    assert _names(m2.calls[0][1]) == ["mcp__b"]


def test_no_tools_when_registry_missing_broken_or_empty_allow() -> None:
    for reg, allowed in [
        (None, None),
        (_Reg(["a"], broken=True), None),
        (_Reg(["a"]), []),
    ]:
        m = _Model()
        list(_s(m, reg, allowed=allowed).step("x"))
        assert m.calls[0][1] is None


def test_disallowed_tool_is_not_executed() -> None:
    reg = _Reg(["leer_archivo", "borrar_archivo"])
    m = _Model(["borrar_archivo"])
    s = _s(m, reg, allowed=["leer_archivo"])
    evs = list(s.step("x"))
    assert reg.executed == []
    done = [e for e in evs if e.kind == "tool_call_completed"]
    assert done and done[0].status == "error"
    assert "no permitida" in done[0].detail
