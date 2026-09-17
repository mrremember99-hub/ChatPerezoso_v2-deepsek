"""Registro de servidores MCP configurable en disco.

DECISIÓN DE SEGURIDAD — no revertir sin leer esto
─────────────────────────────────────────────────

``command`` y ``args`` se leen SIEMPRE de ``mcp_servers.json``, un archivo
estático del repositorio que solo edita el usuario a mano. NUNCA vienen
de la UI, del modelo, ni de ningún otro sitio en runtime.

Esto no es una limitación pendiente de "arreglar". Es la mitigación frente
a la vulnerabilidad de diseño de los SDKs oficiales de MCP documentada por
OX Security en abril de 2026: los valores de configuración de un servidor
MCP fluyen directamente a ``subprocess.Popen`` a través del transporte
STDIO. Si un atacante pudiera controlar ``command`` o ``args`` (por ejemplo,
a través de un prompt injection que hiciera que el modelo propusiera un
servidor MCP malicioso, o mediante una UI de "añadir servidor personalizado"
sin validación), podría ejecutar comandos arbitrarios con los privilegios
de la app.

Riesgo asumido: el usuario puede editar ``mcp_servers.json`` a mano y meter
cualquier comando. Eso es aceptable porque el usuario es el dueño de su
máquina y del archivo.

Regla: si algún día se añade una UI para editar servidores MCP, ``command``
debe validarse contra una allowlist fija (por ejemplo, solo ``npx`` y
``python3``) y ``args`` debe validarse contra patrones de nombres de
paquete. Sin esa validación, NO exponer edición de command/args.

Referencia: https://www.ox.security/blog/mcp-sdk-stdio-vulnerability
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
MCP_SERVERS_FILE = BASE_DIR / "mcp_servers.json"


@dataclass
class MCPServerEntry:
    """Entrada de configuración de un servidor MCP.

    SEGURIDAD: ``command`` y ``args`` se pasan a subprocess.Popen sin
    validación (el SDK MCP no los sanitiza). Solo se aceptan valores
    provenientes de ``mcp_servers.json``. Ver el docstring del módulo.
    """

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
