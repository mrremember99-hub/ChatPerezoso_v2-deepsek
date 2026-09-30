"""VRR-Stop: criterio de parada del bucle verificar-reparar.

Spec: docs/harness-v3.md §6.

El bucle verificar-reparar puede dañar codigo correcto si el
verificador y el reparador son ruidosos (VRR-Stop, Wu et al.
2026). En vez de "reparar hasta que pase el verificador" (bucle
abierto), estimamos si la reparacion esta convergiendo y paramos
cuando deja de aportar.

Implementacion minima (heuristica simple):
  · Sin historial          -> REPAIR (dejar que lo intente).
  · issues_count bajo      -> REPAIR (converge).
  · issues_count igual     -> COMMIT (aceptar, reparar no ayuda).
  · issues_count sube 2x   -> STOP (reparar empeora).
  · max_rounds superado    -> STOP.

El VRR-Stop completo (belief filtering + sign identifiability del
paper) es una iteracion futura si la heuristica no basta.
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
    """Una ronda del bucle verificar-reparar."""

    step_index: int
    issues_count: int
    issues_codes: list[str]
    repair_applied: bool
    verification_margin: float = 0.0


@dataclass(frozen=True)
class VRRPolicy:
    """Configuracion del criterio de parada."""

    enabled: bool = True
    max_rounds: int = 5
    # Si 2 rondas seguidas suben issues, paramos.
    worsening_limit: int = 2
    # Fallback cuando no hay historial claro.
    min_verification_margin: float = 0.3


class VRRStopCriterion:
    """Criterio de parada VRR. Heuristica simple.

    Uso:
        crit = VRRStopCriterion()
        for round in rounds:
            decision = crit.should_repair(round)
            if decision is not REPAIR: break
    """

    def __init__(self, policy: VRRPolicy | None = None) -> None:
        self.policy = policy or VRRPolicy()
        self._history: list[VRRRound] = []

    def reset(self) -> None:
        self._history.clear()

    @property
    def rounds(self) -> int:
        return len(self._history)

    def should_repair(self, current: VRRRound) -> RepairDecision:
        """Decide si continuar reparando o parar."""
        if not self.policy.enabled:
            return RepairDecision.COMMIT

        self._history.append(current)

        # Sin historial suficiente: dejar intentar.
        if len(self._history) < 2:
            return RepairDecision.REPAIR

        # Limite duro de rondas.
        if len(self._history) >= self.policy.max_rounds:
            return RepairDecision.STOP

        # Comparar con la ronda inmediatamente anterior.
        prev = self._history[-2]
        if current.issues_count < prev.issues_count:
            return RepairDecision.REPAIR

        if current.issues_count == prev.issues_count:
            # No progresa: aceptar lo que hay. Si el usuario quiere
            # insistir, puede editar a mano.
            if current.verification_margin >= self.policy.min_verification_margin:
                return RepairDecision.COMMIT
            return RepairDecision.STOP

        # issues_count subio. Ver si lleva subiendo varias rondas.
        worsening = 0
        for i in range(len(self._history) - 1, 0, -1):
            if self._history[i].issues_count > self._history[i - 1].issues_count:
                worsening += 1
            else:
                break
        if worsening >= self.policy.worsening_limit:
            return RepairDecision.STOP
        return RepairDecision.REPAIR
