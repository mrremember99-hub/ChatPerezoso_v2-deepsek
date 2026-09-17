"""Agentes como perfiles reutilizables.

Un agente agrupa:
  · un system prompt (instrucciones persistentes),
  · los parámetros del modelo (temperature, num_ctx),
  · una lista opcional de herramientas permitidas (None = todas).

Se persisten en un JSON junto a config.json. El agente activo determina
el system prompt del chat, los parámetros de cada llamada y el catálogo
de herramientas que se ofrece al modelo.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
AGENTS_FILE = BASE_DIR / "agents.json"


@dataclass
class Agent:
    name: str
    system_prompt: str = ""
    temperature: float = 0.7
    num_ctx: int = 0
    # None → todas las herramientas disponibles.
    # Lista (posiblemente vacía) → solo esas.
    allowed_tools: list[str] | None = None

    def options(self) -> dict[str, Any]:
        options: dict[str, Any] = {"temperature": self.temperature}
        if self.num_ctx > 0:
            options["num_ctx"] = self.num_ctx
        return options

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "system_prompt": self.system_prompt,
            "temperature": self.temperature,
            "num_ctx": self.num_ctx,
            "allowed_tools": (
                None if self.allowed_tools is None else list(self.allowed_tools)
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Agent | None":
        if not isinstance(data, dict):
            return None
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            return None

        system_prompt = data.get("system_prompt", "")
        if not isinstance(system_prompt, str):
            system_prompt = ""

        temperature = data.get("temperature", 0.7)
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
            temperature = 0.7
        temperature = max(0.0, min(float(temperature), 2.0))

        num_ctx = data.get("num_ctx", 0)
        if isinstance(num_ctx, bool) or not isinstance(num_ctx, int):
            num_ctx = 0
        num_ctx = max(0, min(num_ctx, 512_000))

        raw_allowed = data.get("allowed_tools")
        if raw_allowed is None:
            allowed: list[str] | None = None
        elif isinstance(raw_allowed, list):
            allowed = [t for t in raw_allowed if isinstance(t, str)]
        else:
            allowed = None

        return cls(
            name=name.strip(),
            system_prompt=system_prompt,
            temperature=temperature,
            num_ctx=num_ctx,
            allowed_tools=allowed,
        )


def default_agents() -> list[Agent]:
    """Conjunto inicial de agentes que se ofrece al arrancar sin config.

    El usuario puede editarlos o renombrarlos. La idea es que la app sea
    útil desde el primer minuto sin obligar a configurar nada.
    """
    # Lista EXPLÍCITA de herramientas de solo lectura. No se usa el
    # comodín "mcp__*" porque expondría también las herramientas MCP de
    # escritura (write_file, edit_file, create_directory, move_file), y
    # un agente llamado "Analista" no debería poder destruir archivos.
    #
    # Si añades un servidor MCP nuevo con herramientas de solo lectura,
    # enuméralas aquí para que este agente las reciba.
    read_only_tools = [
        # Núcleo
        "listar_carpeta",
        "leer_archivo",
        "buscar_en_workspace",
        # Git (solo lectura)
        "git_status",
        "git_diff",
        "git_log",
        "git_show",
        # MCP server-filesystem: solo lectura
        "mcp__fs__read_file",
        "mcp__fs__read_text_file",
        "mcp__fs__read_media_file",
        "mcp__fs__read_multiple_files",
        "mcp__fs__list_directory",
        "mcp__fs__list_directory_with_sizes",
        "mcp__fs__directory_tree",
        "mcp__fs__search_files",
        "mcp__fs__get_file_info",
        "mcp__fs__list_allowed_directories",
    ]
    return [
        Agent(
            name="Asistente",
            system_prompt="",
            temperature=0.7,
            num_ctx=0,
            allowed_tools=None,
        ),
        Agent(
            name="Analista",
            system_prompt=(
                "Eres un analista técnico. Respondes con precisión, sin "
                "adornos innecesarios. Cuando consultas archivos o código, "
                "citas la ruta concreta de donde proviene cada afirmación. "
                "Si no encuentras algo, lo dices sin inventar."
            ),
            temperature=0.3,
            num_ctx=8192,
            allowed_tools=read_only_tools,
        ),
        Agent(
            name="Conversación",
            system_prompt=(
                "Eres un asistente conversacional. No tienes acceso a "
                "archivos ni herramientas. Responde a preguntas generales, "
                "explica conceptos y conversa con naturalidad."
            ),
            temperature=0.8,
            num_ctx=0,
            allowed_tools=[],
        ),
    ]


class AgentStore:
    def __init__(self, path: Path = AGENTS_FILE):
        self.path = path

    def load(self) -> list[Agent]:
        """Carga la lista de agentes. Si no hay archivo o está corrupto,
        devuelve los defaults sin escribir en disco."""
        if not self.path.exists():
            return default_agents()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default_agents()
        if not isinstance(data, dict):
            return default_agents()

        raw_agents = data.get("agents")
        if not isinstance(raw_agents, list):
            return default_agents()

        # Una lista vacía explícita significa "el usuario no quiere agentes".
        # Devolvemos lista vacía para no resucitar los defaults.
        # El AgentController decide qué hacer en ese caso.
        if not raw_agents:
            return []

        agents: list[Agent] = []
        seen_names: set[str] = set()
        for raw in raw_agents:
            agent = Agent.from_dict(raw)
            if agent is None:
                continue
            # Descartamos duplicados por nombre para no tener dos agentes
            # con el mismo identificador.
            if agent.name in seen_names:
                continue
            seen_names.add(agent.name)
            agents.append(agent)

        return agents

    def save(self, agents: list[Agent]) -> None:
        payload = {
            "agents": [agent.to_dict() for agent in agents if agent.name.strip()],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass
