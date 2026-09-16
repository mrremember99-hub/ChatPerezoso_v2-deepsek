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

    @classmethod
    def load(cls) -> "AppConfig":
        if not CONFIG_FILE.exists():
            return cls()
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            values = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
            return cls(**values)
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self) -> None:
        CONFIG_FILE.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def workspace_path(self) -> Path:
        path = Path(self.workspace).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path
