from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config.json"
DEFAULT_WORKSPACE = BASE_DIR / "workspace"


@dataclass
class AppConfig:
    ollama_host: str = "http://localhost:11434"
    model: str = ""
    workspace: str = str(DEFAULT_WORKSPACE)
    width: int = 1100
    height: int = 720
    temperature: float = 0.7
    num_ctx: int = 0
    # Nombre del agente activo. Cadena vacía significa "usa el primer
    # agente disponible".
    current_agent: str = ""
    # Modo piloto automático: salta el diálogo de confirmación para
    # todas las herramientas EXCEPTO `ejecutar_comando`. El shell
    # siempre confirma: es la única garantía frente a comandos
    # destructivos que el modelo pudiera decidir ejecutar.
    auto_approve_tools: bool = False
    # Extensión del piloto automático: si está activo Y el principal
    # también, `ejecutar_comando` (shell) se auto-aprueba sin diálogo.
    # Opt-in explícito: el shell ejecuta binarios arbitrarios del PATH
    # con los argumentos que el modelo decida.
    auto_approve_shell: bool = False

    @classmethod
    def load(cls) -> "AppConfig":
        if not CONFIG_FILE.exists():
            return cls()
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()

        values: dict = {}
        for key, value in data.items():
            field = cls.__dataclass_fields__.get(key)
            if field is None:
                continue
            coerced = _coerce(value, type(field.default))
            if coerced is None:
                continue
            values[key] = coerced

        config = cls(**values)
        config.width = max(600, min(config.width, 4000))
        config.height = max(400, min(config.height, 3000))
        config.temperature = max(0.0, min(config.temperature, 2.0))
        config.num_ctx = max(0, min(config.num_ctx, 512_000))
        return config

    def save(self) -> None:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def workspace_path(self) -> Path:
        return Path(self.workspace).expanduser().resolve()

    def ollama_options(self) -> dict:
        options: dict = {"temperature": self.temperature}
        if self.num_ctx > 0:
            options["num_ctx"] = self.num_ctx
        return options


def _coerce(value, expected_type: type):
    if expected_type is bool:
        return value if isinstance(value, bool) else None
    if expected_type is int:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return None
    if expected_type is float:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return None
    if expected_type is str:
        return value if isinstance(value, str) else None
    return value if isinstance(value, expected_type) else None
