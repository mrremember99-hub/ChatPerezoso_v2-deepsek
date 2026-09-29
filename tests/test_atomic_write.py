"""P1#7: las 3 escrituras atomicas usan un nombre de tmp unico.

Antes: `path.with_suffix(suffix + ".tmp")` generaba siempre el mismo
nombre, asi que dos writers concurrentes al mismo target escribian en
el mismo fichero antes del replace() y podian mezclar bytes.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agents import Agent, AgentStore  # noqa: E402
from core.history import HistoryStore  # noqa: E402
from core.workspace import Workspace  # noqa: E402


@pytest.fixture
def spy_replace(monkeypatch):
    """Captura los paths que se pasan a Path.replace()."""
    captured: list[Path] = []
    orig = Path.replace

    def spy(self, target):
        captured.append(self)
        return orig(self, target)

    monkeypatch.setattr(Path, "replace", spy)
    return captured


def _assert_tmp_shape(tmp: Path, target_name: str) -> None:
    assert tmp.name.endswith(".tmp"), tmp.name
    assert tmp.name.startswith(target_name + "."), tmp.name
    assert str(os.getpid()) in tmp.name, tmp.name
    # hex[:8] entre el pid y .tmp: minimo 6 chars tras el pid.
    tail = tmp.name.rsplit(".", 2)[-2]
    assert len(tail) == 8, tmp.name
    assert all(c in "0123456789abcdef" for c in tail), tmp.name


def test_agent_store_tmp_unico(tmp_path, spy_replace):
    target = tmp_path / "agents.json"
    store = AgentStore(target)
    store.save([Agent(name="a", system_prompt="sa")])
    store.save([Agent(name="b", system_prompt="sb")])
    assert len(spy_replace) == 2
    _assert_tmp_shape(spy_replace[0], "agents.json")
    _assert_tmp_shape(spy_replace[1], "agents.json")
    assert spy_replace[0] != spy_replace[1], "tmp colisionable"
    assert target.exists()


def test_history_store_tmp_unico(tmp_path, spy_replace):
    target = tmp_path / "history.json"
    store = HistoryStore(target)
    msgs = [{"role": "user", "content": "hola"}]
    store.save(msgs, model="m", workspace="w")
    store.save(msgs, model="m", workspace="w")
    assert len(spy_replace) == 2
    _assert_tmp_shape(spy_replace[0], "history.json")
    _assert_tmp_shape(spy_replace[1], "history.json")
    assert spy_replace[0] != spy_replace[1], "tmp colisionable"
    assert target.exists()


def test_workspace_atomic_tmp_unico(tmp_path, spy_replace):
    target = tmp_path / "foo.py"
    Workspace._atomic_write_bytes(target, b"contenido 1")
    Workspace._atomic_write_bytes(target, b"contenido 2")
    assert len(spy_replace) == 2
    _assert_tmp_shape(spy_replace[0], "foo.py")
    _assert_tmp_shape(spy_replace[1], "foo.py")
    assert spy_replace[0] != spy_replace[1], "tmp colisionable"
    assert target.read_bytes() == b"contenido 2"
