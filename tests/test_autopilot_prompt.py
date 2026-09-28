"""Tests del bloque de piloto automatico en el system prompt."""
from __future__ import annotations

from ui.autopilot_prompt import BLOCK, inject


# -- auto_approve=False: prompt sin cambios ------------------------------

def test_off_returns_prompt_unchanged():
    assert inject("base", False) == "base"


def test_off_with_empty_prompt_returns_empty():
    assert inject("", False) == ""


def test_off_with_long_prompt_returns_exact_copy():
    base = "A" * 500 + "\n" + "B" * 500
    assert inject(base, False) == base


# -- auto_approve=True: bloque inyectado ---------------------------------

def test_on_appends_block_after_base():
    out = inject("base", True)
    assert out == "base\n\n" + BLOCK


def test_on_with_empty_prompt_returns_block_only():
    assert inject("", True) == BLOCK


def test_on_with_whitespace_prompt_returns_block_only():
    assert inject("   \n\t  ", True) == BLOCK


def test_on_does_not_mutate_off_case():
    """Ida y vuelta: el inject no deja estado."""
    assert inject("base", False) == "base"
    assert inject("base", True) != "base"


# -- Contenido del bloque -----------------------------------------------

def test_block_mentions_piloto_automatico():
    assert "PILOTO AUTOMATICO" in BLOCK


def test_block_forbids_asking_permission():
    assert "No pidas permiso" in BLOCK


def test_block_forbids_announcing_plan():
    assert "No anuncies el plan" in BLOCK


def test_block_tells_to_report_after():
    assert "reporta el resultado" in BLOCK
