"""S0: verificacion de las dataclasses de policy."""
from __future__ import annotations

from pathlib import Path

from core.harness.policy import (
    AgentSpec,
    HarnessConfig,
    HarnessPolicy,
    LoopPolicy,
    ModelSpec,
)


def test_loop_policy_defaults():
    p = LoopPolicy()
    assert p.enabled is True
    assert p.generic_repeat == (3, 5, 8)
    assert p.max_corrective_attempts == 3


def test_harness_config_minimo():
    cfg = HarnessConfig(
        run_id="r1",
        workspace_root=Path("/tmp/ws"),
        storage_dir=Path("/tmp/store"),
        model=ModelSpec(name="qwen3:30b-a3b"),
        agent=AgentSpec(name="Programador"),
    )
    assert cfg.run_id == "r1"
    assert cfg.loop_detection_enabled is False
    assert cfg.durable_enabled is False
    assert isinstance(cfg.policy, HarnessPolicy)


def test_flags_por_slice_off_por_defecto():
    cfg = HarnessConfig(
        run_id="r1",
        workspace_root=Path("/tmp/ws"),
        storage_dir=Path("/tmp/store"),
        model=ModelSpec(name="x"),
        agent=AgentSpec(name="y"),
    )
    assert cfg.schema_compilation_enabled is False
    assert cfg.completion_verification_enabled is False
