"""Snapshot del payload enviado a Ollama, para diagnostico.

Se construye solo cuando el logger tiene DEBUG activo, asi
que en produccion no tiene coste. El objetivo es poder ver,
para una respuesta concreta, que informacion recibio el
modelo: system prompt, tools expuestas, opciones, override
de thinking, etc.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RequestSnapshot:
    """Datos estaticos de la peticion, por ronda.

    Los campos se fijan en _prepare_context. round_number se
    actualiza al inicio de cada ronda del bucle de chat().
    """
    model: str
    system_prompt_chars: int
    system_prompt_preview: str
    history_messages: int
    active_tools: list[str]
    options: dict | None
    thinking_override: bool | None
    round_number: int = 0

    def to_log(self) -> str:
        """String compacto multi-linea para logger.debug."""
        tools = ", ".join(self.active_tools) or "(ninguna)"
        opts = "(defaults)" if not self.options else str(
            self.options
        )
        think = (
            f"think={self.thinking_override}"
            if self.thinking_override is not None
            else "think=(auto)"
        )
        return (
            f"[snapshot ronda={self.round_number}]\n"
            f"  modelo:   {self.model}\n"
            f"  tools:    {tools}\n"
            f"  history:  {self.history_messages} mensajes\n"
            f"  sys:      {self.system_prompt_chars} chars\n"
            f"  options:  {opts}\n"
            f"  {think}\n"
            f"  sys_head: {self.system_prompt_preview!r}"
        )
