"""Configuración del agente (v3).

Solo lo que el núcleo usa. Sin HarnessPolicy agregada, sin
LoopPolicy, sin HealthPolicy, sin DurablePolicy, sin VRRPolicy,
sin storage_dir.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ModelSpec:
    name: str
    temperature: float = 0.1
    num_ctx: int = 16384
    num_predict: int = 0


@dataclass(frozen=True)
class AgentSpec:
    name: str
    system_prompt: str = ""
    allowed_tools: list[str] | None = None  # None = todas


@dataclass(frozen=True)
class AgentConfig:
    """Configuración de un run del agente."""

    run_id: str
    workspace_root: Path
    model: ModelSpec
    agent: AgentSpec = field(
        default_factory=lambda: AgentSpec(name="chat"),
    )
    # Limite de rondas modelo↔tools por step.
    max_tool_rounds: int = 15
    # Si True, las tools que requieren confirmación se auto-aprueban.
    auto_approve: bool = False
    # Doble puerta para ejecutar_comando: requiere auto_approve
    # + auto_approve_shell + allowlist.
    auto_approve_shell: bool = False
    # Completion verification (fases OVERPAPER).
    completion_verification_enabled: bool = False
