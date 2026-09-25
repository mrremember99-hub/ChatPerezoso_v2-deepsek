"""Tests del parser de fases (core/prompt_phases.py)."""
from __future__ import annotations

from core.prompt_phases import (
    build_phase_prompt,
    detect_phases,
)


OVERPAPER = """REGLAS GLOBALES: Aplica siempre.

━━━ FASE 1 — Crear gui.py ━━━
Crea gui.py con Tkinter.

━━━ FASE 2 — Añadir carga ━━━
Edita gui.py para añadir load_image_a.

━━━ FASE 3 — Añadir slider ━━━
Edita gui.py para añadir on_slats_changed.
"""


def test_detecta_overpaper_con_box_drawing():
    d = detect_phases(OVERPAPER)
    assert d is not None
    assert d.count == 3
    assert d.preamble == "REGLAS GLOBALES: Aplica siempre."
    assert d.phases[0].startswith("━━━ FASE 1")
    assert d.phases[2].startswith("━━━ FASE 3")


def test_detecta_markdown_h2():
    d = detect_phases("## FASE 1 — X\n## FASE 2 — Y")
    assert d is not None
    assert d.count == 2


def test_detecta_formato_simple():
    d = detect_phases("FASE 1: X\n\nFASE 2: Y")
    assert d is not None
    assert d.count == 2


def test_detecta_bold():
    d = detect_phases("**FASE 1** X\n**FASE 2** Y")
    assert d is not None
    assert d.count == 2


def test_detecta_separador_doble():
    d = detect_phases("════ FASE 1 ════\n════ FASE 2 ════")
    assert d is not None
    assert d.count == 2


def test_rechaza_numeros_no_consecutivos():
    assert detect_phases("FASE 1\n\nFASE 3") is None


def test_rechaza_una_sola_fase():
    assert detect_phases("solo una FASE 1") is None


def test_rechaza_texto_vacio():
    assert detect_phases("") is None
    assert detect_phases(None) is None  # type: ignore[arg-type]


def test_rechaza_sin_fases():
    assert detect_phases("Esto es un texto normal sin fases.") is None


def test_preamble_vacio_si_fase_al_inicio():
    d = detect_phases("FASE 1\nA\n\nFASE 2\nB")
    assert d is not None
    assert d.preamble == ""


def test_build_phase_prompt_orden():
    prompt = build_phase_prompt(
        preamble="REGLAS",
        phase_body="FASE 1 — algo",
        workspace_snapshot="[ESTADO DEL WORKSPACE]\n- a.py",
    )
    assert prompt.index("REGLAS") < prompt.index("ESTADO DEL")
    assert prompt.index("ESTADO DEL") < prompt.index("FASE 1")


def test_build_phase_prompt_sin_snapshot():
    prompt = build_phase_prompt("R", "F", "")
    assert prompt == "R\n\n---\n\nF"


def test_build_phase_prompt_sin_preamble():
    prompt = build_phase_prompt("", "F", "")
    assert prompt == "F"
    