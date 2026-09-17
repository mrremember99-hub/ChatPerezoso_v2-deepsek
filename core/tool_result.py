"""Resultado estructurado de una herramienta."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    tool_name: str
    summary: str = ""
    detail: str = ""
    is_error: bool = False
    is_cancelled: bool = False
    duration_ms: int = 0
    truncated: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        if self.is_cancelled:
            return "cancelled"
        if self.is_error:
            return "error"
        return "ok"

    @property
    def status_label(self) -> str:
        return {
            "ok": "Resultado",
            "error": "Error",
            "cancelled": "Cancelado",
        }[self.status]

    def to_text(self) -> str:
        parts: list[str] = []
        if self.summary and self.summary != self.detail:
            parts.append(self.summary)
        if self.detail:
            parts.append(self.detail)
        text = "\n\n".join(parts) if parts else "(sin resultado)"
        if self.truncated:
            text += "\n\n[resultado truncado: hay más información disponible]"
        return text

    @classmethod
    def error(cls, tool_name: str, message: str) -> "ToolResult":
        return cls(tool_name=tool_name, summary=message, detail="", is_error=True)

    @classmethod
    def cancelled(cls, tool_name: str) -> "ToolResult":
        return cls(
            tool_name=tool_name,
            summary="Operación cancelada por el usuario.",
            is_cancelled=True,
        )
