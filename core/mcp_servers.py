"""Registro de servidores MCP configurable en disco."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
MCP_SERVERS_FILE = BASE_DIR / "mcp_servers.json"


@dataclass
class MCPServerEntry:
    id: str
    label: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "command": self.command,
            "args": list(self.args),
            "env": dict(self.env),
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MCPServerEntry | None":
        if not isinstance(data, dict):
            return None
        server_id = str(data.get("id", "")).strip()
        command = str(data.get("command", "")).strip()
        if not server_id or not command:
            return None
        raw_args = data.get("args")
        args = [str(a) for a in raw_args] if isinstance(raw_args, list) else []
        raw_env = data.get("env")
        env = (
            {str(k): str(v) for k, v in raw_env.items()}
            if isinstance(raw_env, dict)
            else {}
        )
        return cls(
            id=server_id,
            label=str(data.get("label", server_id)) or server_id,
            command=command,
            args=args,
            env=env,
            enabled=bool(data.get("enabled", False)),
        )


def default_servers(workspace_root: str) -> list[MCPServerEntry]:
    return [
        MCPServerEntry(
            id="fs",
            label="Archivos del workspace",
            command="npx",
            args=[
                "-y",
                "@modelcontextprotocol/server-filesystem",
                workspace_root,
            ],
            enabled=False,
        ),
    ]


class MCPServerStore:
    def __init__(self, path: Path = MCP_SERVERS_FILE):
        self.path = path

    def load(self, workspace_root: str) -> list[MCPServerEntry]:
        if not self.path.exists():
            return default_servers(workspace_root)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default_servers(workspace_root)
        if not isinstance(data, dict):
            return default_servers(workspace_root)
        raw = data.get("servers")
        if not isinstance(raw, list) or not raw:
            return default_servers(workspace_root)
        entries: list[MCPServerEntry] = []
        seen: set[str] = set()
        for item in raw:
            entry = MCPServerEntry.from_dict(item)
            if entry is None or entry.id in seen:
                continue
            seen.add(entry.id)
            entries.append(entry)
        return entries or default_servers(workspace_root)

    def save(self, servers: list[MCPServerEntry]) -> None:
        payload = {"servers": [s.to_dict() for s in servers]}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass
