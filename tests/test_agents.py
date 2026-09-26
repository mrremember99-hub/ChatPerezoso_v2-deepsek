from __future__ import annotations

import json

from core.agents import Agent, AgentStore, default_agents


# -- Agent -------------------------------------------------------------------

def test_agent_options_minimal():
    agent = Agent(name="A", temperature=0.3, num_ctx=0)
    assert agent.options() == {"temperature": 0.3}


def test_agent_options_with_num_ctx():
    agent = Agent(name="A", temperature=0.5, num_ctx=8192)
    opts = agent.options()
    assert opts["temperature"] == 0.5
    assert opts["num_ctx"] == 8192


def test_agent_roundtrip_dict():
    agent = Agent(
        name="Analista",
        system_prompt="Sé preciso.",
        temperature=0.3,
        num_ctx=8192,
        allowed_tools=["leer_archivo"],
    )
    data = agent.to_dict()
    restored = Agent.from_dict(data)
    assert restored == agent


def test_agent_from_dict_rejects_empty_name():
    assert Agent.from_dict({"name": ""}) is None
    assert Agent.from_dict({"name": "  "}) is None
    assert Agent.from_dict({"name": 42}) is None
    assert Agent.from_dict({}) is None


def test_agent_from_dict_tolerates_invalid_fields():
    agent = Agent.from_dict({
        "name": "X",
        "temperature": "alta",
        "num_ctx": True,
        "allowed_tools": "no-es-lista",
    })
    assert agent is not None
    assert agent.temperature == 0.7
    assert agent.num_ctx == 0
    assert agent.allowed_tools is None


def test_agent_from_dict_clamps_temperature():
    high = Agent.from_dict({"name": "X", "temperature": 5.0})
    low = Agent.from_dict({"name": "X", "temperature": -1.0})
    assert high is not None
    assert low is not None
    assert high.temperature == 2.0
    assert low.temperature == 0.0


def test_agent_from_dict_dedupes_tool_list():
    agent = Agent.from_dict({"name": "X", "allowed_tools": ["a", 1, "b"]})
    assert agent is not None
    assert agent.allowed_tools == ["a", "b"]


# -- AgentStore --------------------------------------------------------------

def test_store_returns_defaults_when_missing(tmp_path):
    store = AgentStore(tmp_path / "no-existe.json")
    agents = store.load()
    names = [a.name for a in agents]
    assert "Asistente" in names
    assert "Analista" in names
    assert "Conversación" in names


def test_store_saves_and_loads(tmp_path):
    path = tmp_path / "agents.json"
    store = AgentStore(path)
    original = [Agent(name="Custom", system_prompt="Hola", temperature=1.0)]
    store.save(original)
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].name == "Custom"
    assert loaded[0].system_prompt == "Hola"
    assert loaded[0].temperature == 1.0


def test_store_corrupted_json_returns_defaults(tmp_path):
    path = tmp_path / "agents.json"
    path.write_text("{no es json", encoding="utf-8")
    agents = AgentStore(path).load()
    assert any(a.name == "Asistente" for a in agents)


def test_store_skips_duplicates_by_name(tmp_path):
    path = tmp_path / "agents.json"
    path.write_text(json.dumps({
        "agents": [
            {"name": "X", "temperature": 0.1},
            {"name": "X", "temperature": 0.9},
        ],
    }), encoding="utf-8")
    agents = AgentStore(path).load()
    assert [a.name for a in agents].count("X") == 1


def test_store_save_ignores_blank_names(tmp_path):
    path = tmp_path / "agents.json"
    store = AgentStore(path)
    store.save([Agent(name="OK"), Agent(name="  ")])
    loaded = store.load()
    assert [a.name for a in loaded] == ["OK"]


def test_default_agents_conversacion_has_no_tools():
    agents = {a.name: a for a in default_agents()}
    assert agents["Conversación"].allowed_tools == []


def test_default_agents_analista_is_read_only():
    agents = {a.name: a for a in default_agents()}
    allowed = agents["Analista"].allowed_tools
    assert allowed is not None
    assert "leer_archivo" in allowed
    assert "escribir_archivo" not in allowed
    assert "ejecutar_comando" not in allowed


# -- num_predict (investigacion 2026-09-26) ------------------------------

def test_agent_options_sin_num_predict_por_defecto():
    agent = Agent(name="A")
    assert "num_predict" not in agent.options()


def test_agent_options_con_num_predict():
    agent = Agent(name="A", num_predict=4096)
    assert agent.options().get("num_predict") == 4096


def test_agent_options_num_predict_cero_no_envia():
    agent = Agent(name="A", num_predict=0)
    assert "num_predict" not in agent.options()


def test_agent_to_dict_incluye_num_predict():
    agent = Agent(name="A", num_predict=8192)
    assert agent.to_dict()["num_predict"] == 8192


def test_agent_from_dict_parsea_num_predict():
    agent = Agent.from_dict({"name": "A", "num_predict": 4096})
    assert agent.num_predict == 4096


def test_agent_from_dict_default_cero_si_ausente():
    agent = Agent.from_dict({"name": "A"})
    assert agent.num_predict == 0


def test_agent_from_dict_rechaza_invalidos():
    # bool, string, negativo: todos caen a 0 o al rango defensivo.
    assert Agent.from_dict({"name": "A", "num_predict": True}).num_predict == 0
    assert Agent.from_dict({"name": "A", "num_predict": "alto"}).num_predict == 0
    assert Agent.from_dict({"name": "A", "num_predict": -1}).num_predict == 0
    # Valor enorme: clampado a 65536.
    assert Agent.from_dict({"name": "A", "num_predict": 999_999}).num_predict == 65_536


def test_agent_roundtrip_num_predict():
    a = Agent(name="A", num_predict=4096)
    b = Agent.from_dict(a.to_dict())
    assert b.num_predict == 4096
