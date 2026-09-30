"""S5: tests del criterio de parada VRR."""
from __future__ import annotations

from core.harness.vrr import (
    RepairDecision,
    VRRPolicy,
    VRRRound,
    VRRStopCriterion,
)


def _round(issues: int, *, margin: float = 0.5) -> VRRRound:
    return VRRRound(
        step_index=0,
        issues_count=issues,
        issues_codes=["F821"] if issues else [],
        repair_applied=True,
        verification_margin=margin,
    )


def test_sin_historial_deja_reparar():
    c = VRRStopCriterion()
    assert c.should_repair(_round(5)) is RepairDecision.REPAIR


def test_issues_bajan_reparar():
    c = VRRStopCriterion()
    c.should_repair(_round(5))
    assert c.should_repair(_round(2)) is RepairDecision.REPAIR


def test_issues_iguales_commit_si_margen_suficiente():
    c = VRRStopCriterion(VRRPolicy(min_verification_margin=0.3))
    c.should_repair(_round(3, margin=0.5))
    assert c.should_repair(_round(3, margin=0.5)) is RepairDecision.COMMIT


def test_issues_iguales_stop_si_margen_insuficiente():
    c = VRRStopCriterion(VRRPolicy(min_verification_margin=0.5))
    c.should_repair(_round(3, margin=0.1))
    assert c.should_repair(_round(3, margin=0.1)) is RepairDecision.STOP


def test_issues_suben_una_vez_reparar():
    c = VRRStopCriterion()
    c.should_repair(_round(2))
    assert c.should_repair(_round(3)) is RepairDecision.REPAIR


def test_issues_suben_dos_veces_stop():
    c = VRRStopCriterion(VRRPolicy(worsening_limit=2))
    c.should_repair(_round(1))
    c.should_repair(_round(2))
    assert c.should_repair(_round(3)) is RepairDecision.STOP


def test_max_rounds_alcanzado():
    c = VRRStopCriterion(VRRPolicy(max_rounds=3))
    c.should_repair(_round(5))
    c.should_repair(_round(4))
    # En la 3ra ronda se supera max_rounds=3.
    assert c.should_repair(_round(3)) is RepairDecision.STOP


def test_disabled_siempre_commit():
    c = VRRStopCriterion(VRRPolicy(enabled=False))
    c.should_repair(_round(10))
    assert c.should_repair(_round(10)) is RepairDecision.COMMIT


def test_reset_limpia_historial():
    c = VRRStopCriterion()
    c.should_repair(_round(5))
    c.should_repair(_round(4))
    assert c.rounds == 2
    c.reset()
    assert c.rounds == 0
