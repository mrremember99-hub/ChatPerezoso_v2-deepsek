"""Tests del state incremental de code-fence (O(n) vs O(n²))."""
from __future__ import annotations

import time

from core.ollama import _advance_fence_state, _in_code_fence


def test_advance_abre_fence():
    assert _advance_fence_state(False, "```") is True
    assert _advance_fence_state(False, "texto ```json\n") is True


def test_advance_cierra_fence():
    assert _advance_fence_state(True, "```") is False
    assert _advance_fence_state(True, "```\n") is False


def test_advance_doble_fence_net_zero():
    # Dos fences en el mismo delta: paridad se mantiene.
    assert _advance_fence_state(False, "```py\ncode\n```") is False
    assert _advance_fence_state(True, "```py\ncode\n```") is True


def test_advance_sin_fence():
    assert _advance_fence_state(False, "hola mundo") is False
    assert _advance_fence_state(True, "hola mundo") is True


def test_advance_vacio():
    assert _advance_fence_state(False, "") is False
    assert _advance_fence_state(True, "") is True


def test_equivalencia_con_in_code_fence():
    """La version incremental debe coincidir con la acumulativa
    para una secuencia de deltas en los que el ``` no se parte."""
    deltas = [
        "Mira este codigo:\n",
        "```python\n",
        "def f():\n",
        "    return {",
        "        'a': 1\n",
        "    }\n",
        "```\n",
        "Y aqui un JSON: ",
        '{"name": "x"}',
    ]
    state = False
    accumulated = ""
    for d in deltas:
        state = _advance_fence_state(state, d)
        accumulated += d
        assert state == _in_code_fence(accumulated), (
            f"Desincronizacion en {d!r}\n"
            f"incremental={state}, acumulativo={_in_code_fence(accumulated)}"
        )


def test_rendimiento_lineal_en_respuesta_larga():
    """Con miles de `{` fuera de fence, el coste debe ser ~lineal.

    El bug antiguo hacia `"".join(emitted_so_far)` en cada `{`,
    lo cual daba O(n²). Con el fix, cada delta se procesa O(len).
    """
    delta = "a" * 50 + "{" + "b" * 50
    n = 2000
    state = False
    t0 = time.perf_counter()
    for _ in range(n):
        state = _advance_fence_state(state, delta)
    elapsed = time.perf_counter() - t0
    # 2000 * 100 chars = 200k chars procesados. Si fuera O(n²)
    # serian ~20 millones de operaciones y tardaria cientos de ms.
    # Aceptamos <100 ms como margen amplio (tipico <10 ms).
    assert elapsed < 0.1, f"Tardo {elapsed*1000:.1f} ms, parece O(n²)"
