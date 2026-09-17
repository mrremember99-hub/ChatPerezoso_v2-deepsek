"""Estadísticas de sesión para el panel de diagnóstico.

Es un dataclass puro, sin Qt. Los controladores lo actualizan; la vista
lo renderiza. Mantenerlo separado facilita los tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# Aproximación de tokens: 1 token ≈ 4 caracteres. Es el ratio estándar
# para texto en español/inglés sin tokenizer real. No es exacto pero es
# suficiente para que el usuario vea si el contexto se está llenando.
_CHARS_PER_TOKEN = 4


@dataclass
class SessionStats:
    model: str = ""
    temperature: float = 0.7
    num_ctx: int = 0
    responses: int = 0
    total_response_seconds: float = 0.0
    context_tokens: int = 0
    # Cuantas veces el modelo intento usar una herramienta escribiendo
    # el JSON como texto en lugar de usar tool calling nativo. Si sube,
    # el modelo no soporta tools o lo esta haciendo mal.
    textual_tool_attempts: int = 0
    # Modo de tool calling del modelo actual (native/xml/unknown),
    # solo informativo.
    tool_mode: str = "unknown"

    # -- métricas derivadas --------------------------------------------------

    @property
    def average_response_seconds(self) -> float:
        if self.responses == 0:
            return 0.0
        return self.total_response_seconds / self.responses

    def add_response(self, seconds: float) -> None:
        self.responses += 1
        self.total_response_seconds += max(0.0, seconds)

    def note_textual_tool(self) -> None:
        """Registra que el modelo intento usar tool calling textual."""
        self.textual_tool_attempts += 1

    def reset_metrics(self) -> None:
        """Reinicia los contadores sin borrar la config del modelo."""
        self.responses = 0
        self.total_response_seconds = 0.0
        self.context_tokens = 0
        self.textual_tool_attempts = 0

    # -- contexto ------------------------------------------------------------

    def update_context(self, messages: list[dict]) -> None:
        """Recalcula los tokens estimados del contexto actual."""
        total_chars = 0
        for message in messages:
            content = message.get("content")
            if isinstance(content, str):
                total_chars += len(content)
        self.context_tokens = total_chars // _CHARS_PER_TOKEN


def estimate_tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN
