"""Completion verification. Spec: §8.
Estado: S0 (stub). Implementacion en S5.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PhaseSpec:
    index: int
    name: str
    required_tools: list[str] = field(default_factory=list)
    expected_files: list[str] = field(default_factory=list)
    verification_command: str = ""


@dataclass(frozen=True)
class CompletionResult:
    status: str
    message: str = ""
    missing_tools: list[str] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)


class CompletionVerifier:
    """Verificador de completitud. Stub de S0."""

    def verify(self, *_args, **_kwargs) -> CompletionResult:
        raise NotImplementedError("S5: implementar verifier")
