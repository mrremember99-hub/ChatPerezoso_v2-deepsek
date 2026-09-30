"""P2#7: ping_pong debe poder alcanzar corrective (3 ciclos) y abort (4).

Auditoria externa, HEAD 75a2240. Sin el fix de detect_ping_pong
(_trailing_cycles sobre todo el buffer), corrective y abort eran
inalcanzables: la ventana acotada por period*min_repeats nunca
veia mas de 2 ciclos.

Opcion (b): ademas de contar ciclos por firma, exigimos que los
resultados tambien ciclen (strict) para escalar mas alla de
warning. Spec 3.7: mismos args con resultados distintos es
progreso, no bucle.
"""
from __future__ import annotations

from core.harness.loop import LoopDetector, detect_ping_pong
from core.harness.policy import LoopPolicy


def _feed(
    n: int, period: int = 2, *, same_result: bool = True,
) -> list[tuple[str, str, int]]:
    ld = LoopDetector(LoopPolicy())
    actions = []
    for i in range(n):
        res = f"r{i % period}" if same_result else f"r{i}"
        d = ld.observe(f"T{i % period}", {"x": 0}, result=res)
        actions.append((d.action, d.detector, d.count))
    return actions


def test_cycles_count_beyond_window() -> None:
    sigs = ["A", "B"] * 4
    assert detect_ping_pong(sigs, min_repeats=2) == ("A|B", 4)


def test_no_pattern_returns_none() -> None:
    assert detect_ping_pong(["A", "B", "C", "D", "E", "F"]) is None


def test_broken_tail_counts_only_trailing_cycles() -> None:
    assert detect_ping_pong(["X", "A", "B", "A", "B"]) == ("A|B", 2)


def test_ping_pong_escalates_to_abort() -> None:
    acts = [a for a in _feed(8) if a[1] == "ping_pong"]
    kinds = [a[0] for a in acts]
    assert kinds[0] == "warning"
    assert "corrective" in kinds
    assert kinds[-1] == "abort"


def test_period_three_escalates() -> None:
    acts = [a for a in _feed(12, period=3) if a[1] == "ping_pong"]
    assert acts and acts[-1][0] in {"corrective", "abort"}


def test_progress_with_different_results_never_escalates() -> None:
    """Spec 3.7: mismas llamadas pero resultados distintos = progreso."""
    acts = [a for a in _feed(16, same_result=False) if a[1] == "ping_pong"]
    assert acts
    assert {a[0] for a in acts} == {"warning"}
