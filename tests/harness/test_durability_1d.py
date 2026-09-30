"""Grupo 1d: P2#14 (append tolera fallo) + P2#17 (from_dict)."""
from __future__ import annotations

import pathlib
from collections.abc import Iterator

from core.harness import events as ev
from core.harness.model import ModelDelta
from core.harness.policy import AgentSpec, HarnessConfig, ModelSpec
from core.harness.session import HarnessSession


class _Model:
    def chat(
        self, messages, *, tools=None, stream=True, cancel_event=None,
    ) -> Iterator[ModelDelta]:
        yield ModelDelta(kind="text", text="ok")
        yield ModelDelta(kind="done")


class _BrokenLog:
    """EventLog que falla siempre al append."""

    def __init__(self) -> None:
        self.attempts = 0

    def append(self, _event) -> int:
        self.attempts += 1
        raise RuntimeError("DB caida")


def _cfg(tmp_path: pathlib.Path) -> HarnessConfig:
    return HarnessConfig(
        run_id="r1",
        workspace_root=tmp_path / "ws",
        storage_dir=tmp_path / "store",
        model=ModelSpec(name="m"),
        agent=AgentSpec(name="a"),
    )


# ── P2#14: append falla → _emit no propaga ──────────────────────


def test_emit_no_propaga_si_append_falla(tmp_path) -> None:
    """Un fallo de append no debe romper step().

    El evento se emite en memoria (la app sigue viendo las
    señales), aunque no quede persistido. Antes, el except de
    _emit re-emitia HarnessError, volvia a fallar append, y la
    excepcion salia sin capturar.
    """
    broken = _BrokenLog()
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_Model(),
        event_log=broken,
    )
    # No debe lanzar.
    events = list(s.step("hola"))
    # El log intento persistir todos los eventos.
    assert broken.attempts > 0
    # La sesion siguio emitiendo eventos en memoria.
    kinds = [e.kind for e in events]
    assert "run_started" in kinds
    assert "step_ended" in kinds


def test_emit_devuelve_evento_si_append_falla(tmp_path) -> None:
    """El seq del evento cae al local cuando el log falla."""
    broken = _BrokenLog()
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_Model(),
        event_log=broken,
    )
    events = list(s.step("hola"))
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)
    assert all(s_ > 0 for s_ in seqs)


# ── P2#17: from_dict tolerante ──────────────────────────────────


def test_from_dict_kind_desconocido() -> None:
    out = ev.Event.from_dict({
        "kind": "inventado", "seq": 1, "run_id": "r", "ts": "t",
    })
    assert isinstance(out, ev.UnknownEvent)
    assert out.original_kind == "inventado"


def test_from_dict_sin_kind() -> None:
    out = ev.Event.from_dict({"seq": 1, "run_id": "r", "ts": "t"})
    assert isinstance(out, ev.UnknownEvent)
    assert out.original_kind == ""


def test_from_dict_campos_extra_ignorados() -> None:
    e = ev.StepStarted(seq=1, run_id="r", ts="t", step_index=5)
    d = e.to_dict()
    d["campo_de_version_futura"] = "x"
    out = ev.Event.from_dict(d)
    assert isinstance(out, ev.StepStarted)
    assert out.step_index == 5


def test_registry_devuelve_copia() -> None:
    r1 = ev.Event.registry()
    r2 = ev.Event.registry()
    assert r1 == r2
    r1["fake"] = ev.UnknownEvent  # type: ignore[assignment]
    assert "fake" not in ev.Event.registry()
