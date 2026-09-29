"""Tests de validacion de argparse en benchmarks (P3#7).

Sin red, sin Qt. Los guards de --repeats/--chunk-size se disparan
ANTES de _setup_qt(), asi que ningun test aqui inicializa Qt.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.mark.parametrize("modname,argv,bad_flag", [
    ("scripts.benchmark_context",   ["--repeats", "0"],     "--repeats"),
    ("scripts.benchmark_context",   ["--repeats", "-1"],    "--repeats"),
    ("scripts.benchmark_renderer",  ["--repeats", "0"],     "--repeats"),
    ("scripts.benchmark_renderer",  ["--chunk-size", "0"],  "--chunk-size"),
    ("scripts.benchmark_renderer",  ["--chunk-size", "-3"], "--chunk-size"),
    ("scripts.benchmark_streaming", ["--model", "x", "--repeats", "0"], "--repeats"),
])
def test_repeats_y_chunk_size_invalidos(monkeypatch, capsys, modname, argv, bad_flag):
    """Valores invalidos deben abortar con SystemExit(2) antes de Qt."""
    mod = importlib.import_module(modname)
    monkeypatch.setattr(sys, "argv", [modname, *argv])
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert bad_flag in err
