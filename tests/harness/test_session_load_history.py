"""S6-b-2: HarnessSession.load_history precarga historico."""
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


def _cfg(tmp_path: Path) -> HarnessConfig:
    return HarnessConfig(
        run_id="r1",
        workspace_root=tmp_path / "ws",
        storage_dir=tmp_path / "store",
        model=ModelSpec(name="fake"),
        agent=AgentSpec(name="test"),
    )


def test_load_history_copia_user_assistant_tool(tmp_path):
    s = HarnessSession(_cfg(tmp_path), model_client=_NoopModel())
    s.load_history([
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "que tal"},
        {"role": "tool", "content": "ok"},
    ])
    assert s._messages == [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "que tal"},
        {"role": "tool", "content": "ok"},
    ]


def test_load_history_ignora_system(tmp_path):
    s = HarnessSession(_cfg(tmp_path), model_client=_NoopModel())
    s.load_history([
        {"role": "system", "content": "no debe ir"},
        {"role": "user", "content": "hola"},
    ])
    roles = [m["role"] for m in s._messages]
    assert roles == ["user"]


def test_load_history_copia_defensiva(tmp_path):
    s = HarnessSession(_cfg(tmp_path), model_client=_NoopModel())
    original = {"role": "user", "content": "hola"}
    s.load_history([original])
    original["content"] = "mutado"
    assert s._messages[0]["content"] == "hola"


def test_load_history_ignora_entradas_no_dict(tmp_path):
    s = HarnessSession(_cfg(tmp_path), model_client=_NoopModel())
    s.load_history([
        "no-dict",  # type: ignore[list-item]
        {"role": "user", "content": "ok"},
    ])
    assert len(s._messages) == 1
