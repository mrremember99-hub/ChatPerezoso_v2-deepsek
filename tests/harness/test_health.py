"""Tests de core/harness/health.py — S1-b."""
from __future__ import annotations

from core.harness.health import HealthMonitor
from core.harness.policy import HealthPolicy


def _monitor(**kw) -> HealthMonitor:
    return HealthMonitor(HealthPolicy(**kw))


# ── Loop ───────────────────────────────────────────────────


def test_loop_findings_plateau():
    m = _monitor(loop_window=3)
    m.step(findings_count=5, coverage_score=0.5,
           total_tokens=100, error_count=0)
    m.step(findings_count=5, coverage_score=0.5,
           total_tokens=200, error_count=0)
    r = m.step(findings_count=5, coverage_score=0.5,
               total_tokens=300, error_count=0)
    assert "loop" in r.signals


def test_loop_no_dispara_si_findings_suben():
    m = _monitor(loop_window=3)
    m.step(findings_count=1, coverage_score=0.1,
           total_tokens=100, error_count=0)
    m.step(findings_count=2, coverage_score=0.2,
           total_tokens=200, error_count=0)
    r = m.step(findings_count=3, coverage_score=0.3,
               total_tokens=300, error_count=0)
    assert "loop" not in r.signals


def test_loop_necesita_ventana_completa():
    m = _monitor(loop_window=3)
    m.step(findings_count=1, coverage_score=0.1,
           total_tokens=100, error_count=0)
    r = m.step(findings_count=1, coverage_score=0.1,
               total_tokens=200, error_count=0)
    assert "loop" not in r.signals


# ── Stuck ──────────────────────────────────────────────────


def test_stuck_coverage_baja():
    m = _monitor(stuck_window=5, stuck_coverage_threshold=0.05)
    for i in range(4):
        m.step(findings_count=i, coverage_score=0.01,
               total_tokens=100 * i, error_count=0)
    r = m.step(findings_count=4, coverage_score=0.02,
               total_tokens=500, error_count=0)
    assert "stuck" in r.signals


def test_stuck_no_dispara_si_coverage_sube():
    m = _monitor(stuck_window=5, stuck_coverage_threshold=0.05)
    for i in range(5):
        m.step(findings_count=i, coverage_score=0.1 + 0.05 * i,
               total_tokens=100 * i, error_count=0)
    r = m.step(findings_count=5, coverage_score=0.4,
               total_tokens=600, error_count=0)
    assert "stuck" not in r.signals


# ── Thrash ─────────────────────────────────────────────────


def test_thrash_errores_excesivos():
    m = _monitor(thrash_error_threshold=3)
    r = m.step(findings_count=0, coverage_score=0.1,
               total_tokens=100, error_count=5)
    assert "thrash" in r.signals


def test_thrash_no_dispara_con_pocos_errores():
    m = _monitor(thrash_error_threshold=3)
    r = m.step(findings_count=0, coverage_score=0.1,
               total_tokens=100, error_count=2)
    assert "thrash" not in r.signals


# ── Runaway cost ───────────────────────────────────────────


def test_runaway_tokens_sin_findings():
    m = _monitor(runaway_token_threshold=5000)
    m.step(findings_count=1, coverage_score=0.1,
           total_tokens=1000, error_count=0)
    r = m.step(findings_count=1, coverage_score=0.1,
               total_tokens=7000, error_count=0)
    assert "runaway_cost" in r.signals


def test_runaway_no_dispara_si_findings_crecen():
    m = _monitor(runaway_token_threshold=5000)
    m.step(findings_count=1, coverage_score=0.1,
           total_tokens=1000, error_count=0)
    r = m.step(findings_count=5, coverage_score=0.3,
               total_tokens=7000, error_count=0)
    assert "runaway_cost" not in r.signals


def test_runaway_necesita_dos_steps():
    m = _monitor(runaway_token_threshold=5000)
    r = m.step(findings_count=0, coverage_score=0.0,
               total_tokens=10000, error_count=0)
    assert "runaway_cost" not in r.signals


# ── Compuesto ──────────────────────────────────────────────


def test_multiples_senales_a_la_vez():
    m = _monitor(
        loop_window=3,
        stuck_window=3,
        stuck_coverage_threshold=0.05,
        thrash_error_threshold=3,
        runaway_token_threshold=5000,
    )
    for _ in range(3):
        m.step(findings_count=5, coverage_score=0.01,
               total_tokens=100, error_count=0)
    r = m.step(findings_count=5, coverage_score=0.01,
               total_tokens=7000, error_count=5)
    assert "loop" in r.signals
    assert "stuck" in r.signals
    assert "thrash" in r.signals


# ── Disabled ───────────────────────────────────────────────


def test_disabled_sin_senales():
    m = _monitor(enabled=False)
    for _ in range(10):
        r = m.step(findings_count=0, coverage_score=0.0,
                   total_tokens=10000, error_count=10)
    assert r.signals == []


# ── Reset ──────────────────────────────────────────────────


def test_reset_limpia_historial():
    m = _monitor(loop_window=3)
    m.step(findings_count=1, coverage_score=0.1,
           total_tokens=100, error_count=0)
    m.step(findings_count=1, coverage_score=0.1,
           total_tokens=200, error_count=0)
    m.reset()
    r = m.step(findings_count=1, coverage_score=0.1,
               total_tokens=300, error_count=0)
    assert r.step_index == 0
    assert "loop" not in r.signals


# ── Metrics en el result ───────────────────────────────────


def test_result_lleva_metricas():
    m = _monitor()
    r = m.step(findings_count=3, coverage_score=0.5,
               total_tokens=1234, error_count=1)
    assert r.metrics["findings"] == 3
    assert r.metrics["coverage"] == 0.5
    assert r.metrics["tokens"] == 1234
    assert r.metrics["errors"] == 1
