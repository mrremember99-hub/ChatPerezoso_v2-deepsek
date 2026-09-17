"""Protocolo que debe cumplir cualquier renderer de chat."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ChatRenderer(Protocol):
    """Contrato mínimo que los controladores esperan de un renderer."""

    @property
    def response_text(self) -> str: ...

    @property
    def response_start(self) -> int | None: ...

    def reset(self) -> None: ...

    def reset_response_segment(self) -> None: ...

    def insert_user_message(self, text: str) -> None: ...

    def on_text(self, text: str) -> None: ...

    def insert_tool_event(self, text: str, color: str) -> None: ...

    def insert_tool_result(self, text: str) -> None: ...

    def insert_error(self, message: str) -> None: ...

    def final_text(self, fallback: str) -> str: ...

    def restore_assistant_message(self, text: str) -> None:
        """Inserta una respuesta completa sin pasar por el flujo de
        streaming. Se usa al restaurar una conversación guardada."""
        ...

    def remove_from_last_user(self) -> None:
        """Elimina del chat todo lo que va desde el último mensaje del
        usuario (inclusive) hasta el final. Se usa al regenerar."""
        ...
