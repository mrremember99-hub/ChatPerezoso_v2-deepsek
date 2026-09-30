"""Durable execution: event log + checkpoints. Spec: §4.
Estado: S0 (stub). Implementacion en S2.
"""
from __future__ import annotations


class EventLog:
    """Event log append-only. Stub de S0."""

    def __init__(self, db_path) -> None:
        self.db_path = db_path

    def append(self, event) -> None:
        raise NotImplementedError("S2: implementar append")

    def read(self, run_id: str, since_seq: int = 0):
        raise NotImplementedError("S2: implementar read")


class CheckpointManager:
    """Gestor de checkpoints. Stub de S0."""

    def save(self, state) -> str:
        raise NotImplementedError("S2: implementar save")

    def load(self, checkpoint_id: str):
        raise NotImplementedError("S2: implementar load")
