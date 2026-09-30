"""Health monitoring: 4 senales (loop/stuck/thrash/runaway).

Spec: docs/harness-v3.md §7.

Un agente puede estar stuck sin estar en loop. Este monitor caza
los 3 modos de fallo que el loop detector no ve.
"""
from __future__ import annotations

from dataclasses import dataclass, field

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
    """Monitor de salud con 4 detectores."""

    def __init__(self, policy: HealthPolicy) -> None:
        self.policy = policy
        self._history: list[VitalsSnapshot] = []

    def reset(self) -> None:
        self._history.clear()

    def step(
        self,
        *,
        findings_count: int,
        coverage_score: float,
        total_tokens: int,
        error_count: int,
    ) -> HealthResult:
        snap = VitalsSnapshot(
            step_index=len(self._history),
            findings_count=findings_count,
            coverage_score=coverage_score,
            total_tokens=total_tokens,
            error_count=error_count,
        )
        self._history.append(snap)

        signals: list[str] = []
        if self.policy.enabled:
            if self._detect_loop():
                signals.append("loop")
            if self._detect_stuck():
                signals.append("stuck")
            if self._detect_thrash(snap):
                signals.append("thrash")
            if self._detect_runaway(snap):
                signals.append("runaway_cost")

        return HealthResult(
            step_index=snap.step_index,
            signals=signals,
            metrics={
                "findings": findings_count,
                "coverage": coverage_score,
                "tokens": total_tokens,
                "errors": error_count,
            },
        )

    # ── Detectores ──────────────────────────────────────

    def _detect_loop(self) -> bool:
        """findings_count en plateau por loop_window steps."""
        if len(self._history) < self.policy.loop_window:
            return False
        window = self._history[-self.policy.loop_window:]
        return len({s.findings_count for s in window}) == 1

    def _detect_stuck(self) -> bool:
        """Cobertura sin subir por stuck_window steps."""
        if len(self._history) < self.policy.stuck_window:
            return False
        window = self._history[-self.policy.stuck_window:]
        peak = max(s.coverage_score for s in window)
        return peak < self.policy.stuck_coverage_threshold

    def _detect_thrash(self, current: VitalsSnapshot) -> bool:
        return current.error_count >= self.policy.thrash_error_threshold

    def _detect_runaway(self, current: VitalsSnapshot) -> bool:
        if len(self._history) < 2:
            return False
        prev = self._history[-2]
        tokens_delta = current.total_tokens - prev.total_tokens
        findings_delta = current.findings_count - prev.findings_count
        return (
            tokens_delta > self.policy.runaway_token_threshold
            and findings_delta == 0
        )
