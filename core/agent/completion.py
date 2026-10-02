"""Completion verification. Spec: §8.

Cuando el modelo dice "FASE N VERIFICADA", el harness no lo
acepta por fe: comprueba que (a) ejecuto las tools requeridas,
(b) la verificacion real paso, (c) los artefactos esperados
existen.

Inspirado en la guia de Anthropic (Opus 5.5, 2026) sobre
auto-continuation y completion verification.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_PHASE_HEADER = re.compile(
    r"FASE\s+(\d+)\s*[—\-–:]\s*(.+)", re.IGNORECASE,
)
_VERIFICATION = re.compile(
    r"VERIFICACI[OÓ]N\s+FASE\s+(\d+)[^\n]*\n(.+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PhaseSpec:
    """Especificacion de una fase extraida del prompt."""

    index: int
    name: str
    required_tools: list[str] = field(default_factory=list)
    expected_files: list[str] = field(default_factory=list)
    verification_command: str = ""


@dataclass(frozen=True)
class CompletionResult:
    """Resultado de verificar la completitud de una fase."""

    status: str
    message: str = ""
    missing_tools: list[str] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class CompletionPolicy:
    enabled: bool = True
    require_tool_execution: bool = True
    require_verification_pass: bool = True


def parse_phases(prompt: str) -> list[PhaseSpec]:
    """Extrae fases del prompt segun el formato OVERPAPER.

    Formato esperado:
        FASE 1 — Ventana basica
        ...
        ━━━ VERIFICACIÓN FASE 1 ━━━
        python -m py_compile gui.py

    Devuelve lista de PhaseSpec. Si el prompt no sigue el formato,
    devuelve lista vacia (el llamante decide el fallback).
    """
    if not prompt:
        return []
    phases: list[PhaseSpec] = []
    for match in _PHASE_HEADER.finditer(prompt):
        idx = int(match.group(1))
        name = match.group(2).strip()
        phases.append(PhaseSpec(index=idx, name=name))
    # Asociar verificacion (si existe) a cada fase.
    verification: dict[int, str] = {}
    for match in _VERIFICATION.finditer(prompt):
        idx = int(match.group(1))
        cmd = match.group(2).strip()
        verification[idx] = cmd
    out: list[PhaseSpec] = []
    for p in phases:
        cmd = verification.get(p.index, "")
        out.append(PhaseSpec(
            index=p.index,
            name=p.name,
            required_tools=list(p.required_tools),
            expected_files=list(p.expected_files),
            verification_command=cmd,
        ))
    return out


class CompletionVerifier:
    """Verifica que el modelo completo lo que dice haber completado."""

    def __init__(self, policy: CompletionPolicy | None = None) -> None:
        self.policy = policy or CompletionPolicy()

    def verify(
        self,
        phase: PhaseSpec,
        *,
        executed_tools: list[str] | None = None,
        verification_issues: list[dict] | None = None,
        workspace_files: set[str] | None = None,
    ) -> CompletionResult:
        """Comprueba completitud de una fase.

        - executed_tools: nombres de tools ejecutadas en el step.
          None = [] (util cuando la fase no exige tools concretas).
        - verification_issues: issues del verificador (vacio = OK).
        - workspace_files: rutas relativas presentes en disco.
        """
        executed_tools = executed_tools or []
        if not self.policy.enabled:
            return CompletionResult(status="verified")

        if self.policy.require_tool_execution:
            executed = set(executed_tools)
            missing = [
                t for t in phase.required_tools if t not in executed
            ]
            if missing:
                return CompletionResult(
                    status="incomplete",
                    missing_tools=missing,
                    message=(
                        f"Faltan tools para fase {phase.index}: "
                        f"{', '.join(missing)}"
                    ),
                )

        if self.policy.require_verification_pass:
            issues = verification_issues or []
            if issues:
                return CompletionResult(
                    status="verification_failed",
                    issues=list(issues),
                    message=(
                        f"Verificacion de fase {phase.index} con "
                        f"{len(issues)} issue(s)"
                    ),
                )

        if phase.expected_files:
            present = workspace_files or set()
            missing_files = [
                f for f in phase.expected_files if f not in present
            ]
            if missing_files:
                return CompletionResult(
                    status="missing_artifact",
                    message=(
                        f"Faltan artefactos: "
                        f"{', '.join(missing_files)}"
                    ),
                )

        return CompletionResult(status="verified")

