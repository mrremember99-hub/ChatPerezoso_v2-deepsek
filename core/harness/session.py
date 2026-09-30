"""HarnessSession — orquestador principal del ciclo de agente.

Spec: docs/harness-v3.md §1, §4.
Estado: S0 (stub). Implementacion completa en S4.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from core.harness.events import Event
    from core.harness.policy import HarnessConfig


class HarnessSession:
    """Sesion de agente. Stub de S0."""

    def __init__(self, config: HarnessConfig, **_kwargs) -> None:
        self.config = config

    def step(self, user_message: str) -> Iterator[Event]:
        raise NotImplementedError("S4: implementar ciclo de agente")

    def resume(self) -> Iterator[Event]:
        raise NotImplementedError("S2: implementar resume desde checkpoint")

    def cancel(self) -> None:
        raise NotImplementedError("S4: implementar cancelacion")

    def health(self) -> dict:
        raise NotImplementedError("S1: implementar health monitoring")

    def events(self, since_seq: int = 0) -> Iterator[Event]:
        raise NotImplementedError("S2: implementar replay del event log")

    def pending_confirmation(self):
        raise NotImplementedError("S4: implementar confirmaciones")

    def resolve_confirmation(self, _response) -> None:
        raise NotImplementedError("S4: implementar confirmaciones")
