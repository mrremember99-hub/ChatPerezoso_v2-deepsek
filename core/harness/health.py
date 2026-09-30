"""Health monitoring: 4 senales (loop/stuck/thrash/runaway). Spec: §7.
Estado: S0 (stub). Implementacion en S1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.harness.policy import HealthPolicy


@dataclass(frozen=True)
class VitalsSnapshot:
    step_index: int
    findings_count: int
    coverage_score: float
    total_tokens: int
    error_count: int


@dataclass(frozen=True)
class HealthResult:
    step_index: int
    signals: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


class HealthMonitor:
    """Monitor de salud. Stub de S0."""

    def __init__(self, policy: HealthPolicy) -> None:
        self.policy = policy

    def step(self, **_kwargs) -> HealthResult:
        raise NotImplementedError("S1: implementar monitor")
