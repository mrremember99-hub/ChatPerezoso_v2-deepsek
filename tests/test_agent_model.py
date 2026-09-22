"""Tests del campo Agent.model y su integración con la UI."""
from __future__ import annotations

import json

from core.agents import Agent, AgentStore


def test_agent_default_model_is_empty():
    a = Agent(name="X")
    assert a.model == ""


def test_agent_roundtrip_with_model():
    a = Agent(
        name="Programador",
        system_prompt="eres programador",
        temperature=0.2,
        num_ctx=16384,
        allowed_tools=None,
        model="qwen3-coder:30b",
    )
    data = a.to_dict()
    assert data["model"] == "qwen3-coder:30b"
    restored = Agent.from_dict(data)
    # `is not None` explícito para que Pylance estreche el tipo: un
    # `assert restored == a` no basta (== es compatible con None).
    assert restored is not None
    assert restored == a
    assert restored.model == "qwen3-coder:30b"


def test_agent_from_dict_without_model_defaults_to_empty():
    a = Agent.from_dict({"name": "X"})
    assert a is not None
    assert a.model == ""


def test_agent_from_dict_rejects_non_string_model():
    a = Agent.from_dict({"name": "X", "model": 42})
    assert a is not None
    assert a.model == ""


def test_agent_from_dict_strips_model_whitespace():
    a = Agent.from_dict({"name": "X", "model": "  llama3.1  "})
    assert a is not None
    assert a.model == "llama3.1"


def test_store_persists_model(tmp_path):
    path = tmp_path / "agents.json"
    store = AgentStore(path)
    original = [Agent(name="Prog", model="llama3.1:latest")]
    store.save(original)
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].model == "llama3.1:latest"


def test_store_loads_legacy_agents_without_model(tmp_path):
    """Un agents.json antiguo sin el campo 'model' debe cargarse bien."""
    path = tmp_path / "agents.json"
    path.write_text(json.dumps({
        "agents": [
            {"name": "Viejo", "temperature": 0.5},
        ],
    }), encoding="utf-8")
    store = AgentStore(path)
    agents = store.load()
    assert len(agents) == 1
    assert agents[0].name == "Viejo"
    assert agents[0].model == ""
