"""VRR-Stop: criterio de parada del bucle verificar-reparar. Spec: §6.
Estado: S0 (stub). Implementacion en S5.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RepairDecision(Enum):
    COMMIT = "commit"
    REPAIR = "repair"
    STOP = "stop"


@dataclass(frozen=True)
class VRRRound:
    step_index: int
    issues_count: int
    issues_codes: list[str]
    repair_applied: bool
    verification_margin: float


class VRRStopCriterion:
    """Criterio de parada VRR. Stub de S0."""

    def should_repair(self, _round: VRRRound) -> RepairDecision:
        raise NotImplementedError("S5: implementar VRR-Stop")
