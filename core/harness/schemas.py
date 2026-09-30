"""Tool schema compilation (TSCG-style). Spec: §5.
Estado: S0 (stub). Implementacion en S3.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CompilerProfile:
    """Perfil de operadores TSCG."""

    name: str
    operators: tuple[str, ...]


CONSERVATIVE = CompilerProfile(
    name="conservative",
    operators=("sdm", "tas", "dro"),
)
BALANCED = CompilerProfile(
    name="balanced",
    operators=("sdm", "tas", "dro", "cfl", "cfo", "cas"),
)
AGGRESSIVE = CompilerProfile(
    name="aggressive",
    operators=("sdm", "tas", "dro", "cfl", "cfo", "cas", "sadf", "ccp"),
)


class ToolSchemaCompiler:
    """Compilador de schemas. Stub de S0."""

    def __init__(self, profile: CompilerProfile = CONSERVATIVE) -> None:
        self.profile = profile

    def compile(self, tool: dict) -> dict:
        raise NotImplementedError("S3: implementar compilador")
