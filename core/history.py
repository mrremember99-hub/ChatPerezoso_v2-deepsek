"""Persistencia de la conversación actual.

Guarda el historial de mensajes en un JSON junto a ``config.json``. El
formato es deliberadamente simple: una lista de mensajes con el mismo
esquema que usa ``OllamaClient`` (role, content) más metadatos ligeros
(modelo y workspace en el momento de guardar).

No hay múltiples conversaciones ni nombres. La idea es recordar dónde se
quedó el usuario la última vez. Cuando quiera empezar de cero, "Nueva
conversación" borra el archivo.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
HISTORY_FILE = BASE_DIR / "history.json"

# Límite defensivo: si un archivo corrupto contiene más mensajes de la
# cuenta, no lo cargamos entero.
_MAX_MESSAGES = 200


@dataclass
class History:
    messages: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    workspace: str = ""
    saved_at: str = ""


class HistoryStore:
    def __init__(self, path: Path = HISTORY_FILE):
        self.path = path

    # -- API pública ---------------------------------------------------------

    def load(self) -> History | None:
        """Devuelve la conversación guardada o None si no hay o está corrupta."""
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None

        raw_messages = data.get("messages")
        if not isinstance(raw_messages, list):
            return None

        messages: list[dict[str, Any]] = []
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if role not in {"user", "assistant"}:
                continue
            if not isinstance(content, str):
                continue
            messages.append({"role": role, "content": content})
            if len(messages) >= _MAX_MESSAGES:
                break

        if not messages:
            return None

        return History(
            messages=messages,
            model=str(data.get("model", "")),
            workspace=str(data.get("workspace", "")),
            saved_at=str(data.get("saved_at", "")),
        )

    def save(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str = "",
        workspace: str = "",
    ) -> None:
        """Escribe la conversación actual. Silencioso si falla el disco."""
        # Solo guardamos user/assistant. Los mensajes tool y los system
        # prompts internos no forman parte de la conversación visible.
        serializable = [
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if m.get("role") in {"user", "assistant"}
            and isinstance(m.get("content"), str)
        ]
        payload = {
            "messages": serializable,
            "model": model,
            "workspace": workspace,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            # La persistencia es una comodidad, no un requisito. Si el
            # disco falla, la app sigue funcionando sin recordar nada.
            pass

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass
