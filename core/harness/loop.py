"""Loop detection multi-patron. Spec: docs/harness-v3.md §3.
Estado: S0 (stub). Implementacion en S1.
"""
from __future__ import annotations


class LoopDetector:
    """Detector de bucles. Stub de S0."""

    def observe(self, tool: str, args: dict, result: dict) -> dict:
        raise NotImplementedError("S1: implementar detectores")

    def reset(self) -> None:
        raise NotImplementedError("S1: implementar reset")
