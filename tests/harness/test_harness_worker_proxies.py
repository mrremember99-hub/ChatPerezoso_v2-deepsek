"""2026-10-01: properties del worker delegan a la session."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from core.harness.policy import (
    AgentSpec,
    HarnessConfig,
    ModelSpec,
)
from core.harness.session import HarnessSession


class _NoopModel:
    def chat(self, *a, **kw):
        return iter([])


def _s(tmp_path):
    cfg = HarnessConfig(
        run_id="r1",
        workspace_root=tmp_path / "ws",
        storage_dir=tmp_path / "store",
        model=ModelSpec(name="fake"),
        agent=AgentSpec(name="test"),
        auto_approve=False,
        auto_approve_shell=False,
    )
    return HarnessSession(cfg, model_client=_NoopModel())


def test_auto_approve_property_delega(tmp_path):
    from ui.harness_worker import HarnessWorker

    s = _s(tmp_path)
    w = HarnessWorker(s, "hola")
    assert w.auto_approve is False
    w.auto_approve = True
    assert s.config.auto_approve is True
    assert w.auto_approve is True
    w.auto_approve = False
    assert s.config.auto_approve is False


def test_auto_approve_shell_property_delega(tmp_path):
    from ui.harness_worker import HarnessWorker

    s = _s(tmp_path)
    w = HarnessWorker(s, "hola")
    assert w.auto_approve_shell is False
    w.auto_approve_shell = True
    assert s.config.auto_approve_shell is True


def test_auto_approve_preserva_otros_campos(tmp_path):
    """dataclasses.replace no toca el resto del config."""
    from ui.harness_worker import HarnessWorker

    s = _s(tmp_path)
    w = HarnessWorker(s, "hola")
    old_cfg = s.config
    w.auto_approve = True
    new_cfg = s.config
    assert new_cfg.auto_approve is True
    assert new_cfg.auto_approve_shell == old_cfg.auto_approve_shell
    assert new_cfg.run_id == old_cfg.run_id
    assert new_cfg.model == old_cfg.model
