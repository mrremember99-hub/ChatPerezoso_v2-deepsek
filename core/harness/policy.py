"""Politicas de configuracion del harness.

Spec: docs/harness-v3.md §3, §4, §5, §6, §7, §8.
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
    allowed_tools: list[str] | None = None   # None = todas


@dataclass(frozen=True)
class LoopPolicy:
    enabled: bool = True
    window_size: int = 30
    generic_repeat: tuple[int, int, int] = (3, 5, 8)
    ping_pong: tuple[int, int, int] = (2, 3, 4)
    poll_no_progress: tuple[int, int, int] = (2, 3, 4)
    post_compaction: tuple[int, int] = (1, 3)
    max_corrective_attempts: int = 3


@dataclass(frozen=True)
class DurablePolicy:
    enabled: bool = True
    checkpoint_after_every_step: bool = True
    checkpoint_after_corrective: bool = True
    checkpoint_after_confirmation: bool = True
    keep_last_checkpoints: int = 3
    events_retention_days: int = 30
    events_retention_min_runs: int = 50
    resume_on_startup: bool = True
    max_resume_age_days: int = 7


@dataclass(frozen=True)
class HealthPolicy:
    enabled: bool = True
    loop_window: int = 3
    stuck_window: int = 5
    stuck_coverage_threshold: float = 0.05
    thrash_error_threshold: int = 3
    runaway_token_threshold: int = 5000
    on_loop: str = "corrective"
    on_stuck: str = "corrective"
    on_thrash: str = "confirm"
    on_runaway: str = "warn"


@dataclass(frozen=True)
class VRRPolicy:
    enabled: bool = True
    max_rounds: int = 5
    min_verification_margin: float = 0.3
    calibration_calls: int = 3


@dataclass(frozen=True)
class CompletionPolicy:
    enabled: bool = True
    require_tool_execution: bool = True
    require_verification_pass: bool = True
    require_evidence: bool = True
    max_auto_continuations: int = 3


@dataclass(frozen=True)
class HarnessPolicy:
    """Politica agregada del harness."""
    loop: LoopPolicy = field(default_factory=LoopPolicy)
    durable: DurablePolicy = field(default_factory=DurablePolicy)
    health: HealthPolicy = field(default_factory=HealthPolicy)
    vrr: VRRPolicy = field(default_factory=VRRPolicy)
    completion: CompletionPolicy = field(default_factory=CompletionPolicy)


@dataclass(frozen=True)
class HarnessConfig:
    """Configuracion de un run del harness."""
    run_id: str
    workspace_root: Path
    storage_dir: Path
    model: ModelSpec
    agent: AgentSpec
    policy: HarnessPolicy = field(default_factory=HarnessPolicy)

    # Flags por slice (S0: todos False excepto estructura).
    loop_detection_enabled: bool = False
    health_monitoring_enabled: bool = False
    durable_enabled: bool = False
    schema_compilation_enabled: bool = False
    completion_verification_enabled: bool = False
