"""C: system prompt incluye lista de tools disponibles."""
from __future__ import annotations

import threading
from pathlib import Path

from core.harness.policy import (
    AgentSpec,
    HarnessConfig,
    ModelSpec,
)
from core.harness.session import HarnessSession


class _NoopModel:
    def chat(self, *a, **kw):
        return iter([])


class _FakeRegistry:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    def definitions(self):
        return [
            {"type": "function",
             "function": {"name": n, "description": "", "parameters": {}}}
            for n in self._names
        ]

    def requires_confirmation(self, name: str) -> bool:
        return False

    def call(self, *a, **kw):
        return ""


def _cfg(tmp_path: Path, system_prompt: str = "") -> HarnessConfig:
    return HarnessConfig(
        run_id="r1",
        workspace_root=tmp_path / "ws",
        storage_dir=tmp_path / "store",
        model=ModelSpec(name="fake"),
        agent=AgentSpec(name="test", system_prompt=system_prompt),
    )


def test_tools_block_aparece_al_final_del_system(tmp_path):
    s = HarnessSession(
        _cfg(tmp_path, "Eres un asistente."),
        model_client=_NoopModel(),
        tool_registry=_FakeRegistry(["crear_archivo", "leer_archivo"]),
    )
    msgs = s._build_messages()
    assert msgs[0]["role"] == "system"
    content = msgs[0]["content"]
    assert content.startswith("Eres un asistente.")
    assert "TOOLS DISPONIBLES" in content
    assert "crear_archivo" in content
    assert "leer_archivo" in content


def test_tools_block_sin_system_previo(tmp_path):
    s = HarnessSession(
        _cfg(tmp_path, ""),
        model_client=_NoopModel(),
        tool_registry=_FakeRegistry(["t1"]),
    )
    msgs = s._build_messages()
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"].startswith("TOOLS DISPONIBLES")


def test_sin_tools_no_hay_block(tmp_path):
    s = HarnessSession(
        _cfg(tmp_path, "Hola"),
        model_client=_NoopModel(),
        tool_registry=_FakeRegistry([]),
    )
    msgs = s._build_messages()
    assert msgs[0]["content"] == "Hola"


def test_sin_system_ni_tools_no_hay_primer_system(tmp_path):
    s = HarnessSession(
        _cfg(tmp_path, ""),
        model_client=_NoopModel(),
    )
    msgs = s._build_messages()
    assert msgs == [] or msgs[0]["role"] != "system"
