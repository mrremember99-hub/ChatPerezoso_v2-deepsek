"""S5: tests de completion verification."""
from __future__ import annotations

from core.harness.completion import (
    AutoContinuation,
    CompletionPolicy,
    CompletionResult,
    CompletionVerifier,
    PhaseSpec,
    parse_phases,
)

# ── parse_phases ──────────────────────────────────────────────


def test_parse_phases_basico():
    prompt = (
        "FASE 1 — Ventana basica\n"
        "algo\n"
        "FASE 2 — Layout\n"
        "mas\n"
    )
    phases = parse_phases(prompt)
    assert len(phases) == 2
    assert phases[0].index == 1
    assert phases[0].name == "Ventana basica"
    assert phases[1].index == 2


def test_parse_phases_con_verificacion():
    prompt = (
        "FASE 1 — Intro\n"
        "━━━ VERIFICACIÓN FASE 1 ━━━\n"
        "python -m py_compile gui.py\n"
    )
    phases = parse_phases(prompt)
    assert len(phases) == 1
    assert phases[0].verification_command == "python -m py_compile gui.py"


def test_parse_phases_vacio():
    assert parse_phases("") == []
    assert parse_phases("texto sin fases") == []


# ── CompletionVerifier ────────────────────────────────────────


def _phase(
    *, req_tools: list[str] | None = None,
    files: list[str] | None = None,
) -> PhaseSpec:
    return PhaseSpec(
        index=1, name="Test",
        required_tools=req_tools or [],
        expected_files=files or [],
    )


def test_verifier_ok_sin_requisitos():
    v = CompletionVerifier()
    r = v.verify(_phase())
    assert r.status == "verified"


def test_verifier_falta_tool():
    v = CompletionVerifier()
    r = v.verify(
        _phase(req_tools=["escribir_archivo"]),
        executed_tools=["leer_archivo"],
    )
    assert r.status == "incomplete"
    assert "escribir_archivo" in r.missing_tools


def test_verifier_verificacion_falla():
    v = CompletionVerifier()
    r = v.verify(
        _phase(),
        executed_tools=[],
        verification_issues=[{"code": "F821", "line": 5}],
    )
    assert r.status == "verification_failed"
    assert r.issues[0]["code"] == "F821"


def test_verifier_falta_artefacto():
    v = CompletionVerifier()
    r = v.verify(
        _phase(files=["gui.py"]),
        workspace_files={"core.py"},
    )
    assert r.status == "missing_artifact"


def test_verifier_disabled_acepta():
    v = CompletionVerifier(CompletionPolicy(enabled=False))
    r = v.verify(
        _phase(req_tools=["x"]),
        executed_tools=[],
    )
    assert r.status == "verified"


# ── AutoContinuation ──────────────────────────────────────────


def _res(status: str) -> CompletionResult:
    return CompletionResult(status=status, message="falta algo")


def test_autocontinue_se_resetea_con_verified():
    a = AutoContinuation()
    assert a.should_continue(_res("incomplete")) is True
    assert a.count == 1
    assert a.should_continue(_res("verified")) is False
    assert a.count == 0


def test_autocontinue_limite():
    a = AutoContinuation(CompletionPolicy(max_auto_continuations=2))
    assert a.should_continue(_res("incomplete")) is True
    assert a.should_continue(_res("incomplete")) is True
    assert a.should_continue(_res("incomplete")) is False
    assert a.count == 2


def test_build_prompt():
    a = AutoContinuation()
    p = a.build_prompt(_phase(), _res("incomplete"))
    assert "Auto-continuacion" in p
    assert "Fase 1" in p
    assert "falta algo" in p
