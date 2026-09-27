"""H4 (2026-09-27): calibracion con _MIN_OBSERVATIONS=2.

Antes, has_calibration() requeria 3 observaciones. Con el cambio,
2 bastan, acortando la ventana en que se aplican los defaults
conservadores.
"""
from __future__ import annotations

from core import token_calibration as tc


def setup_function(_fn):
    tc.reset()


def test_no_calibration_con_0_observaciones():
    assert not tc.has_calibration("m")


def test_no_calibration_con_1_observacion():
    tc.observe("m", chars=1000, actual_tokens=250)
    assert not tc.has_calibration("m")


def test_calibration_activa_con_2_observaciones():
    tc.observe("m", chars=1000, actual_tokens=250)  # ratio 4.0
    tc.observe("m", chars=2000, actual_tokens=500)  # ratio 4.0
    assert tc.has_calibration("m")
    # EWMA alpha=0.20: primer obs fija 4.0, segunda mezcla
    # 0.8*4.0 + 0.2*4.0 = 4.0
    ratio = tc.chars_per_token("m")
    assert 3.9 <= ratio <= 4.1


def test_calibration_converge_a_ratio_real():
    """Varias observaciones con mismo ratio -> converge."""
    for _ in range(10):
        tc.observe("m", chars=4900, actual_tokens=1000)  # ratio 4.9
    assert tc.has_calibration("m")
    ratio = tc.chars_per_token("m")
    assert abs(ratio - 4.9) < 0.1


def test_default_es_4_2():
    """_DEFAULT_CHARS_PER_TOKEN alineado con context_window."""
    assert tc._DEFAULT_CHARS_PER_TOKEN == 4.2
