"""S3-mini: tests del compilador de tool schemas."""
from __future__ import annotations

import json

from core.harness.schemas import (
    AGGRESSIVE,
    BALANCED,
    CONSERVATIVE,
    CompilerProfile,
    ToolSchemaCompiler,
)


def _leer_archivo() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "leer_archivo",
            "description": "Lee un archivo de texto UTF-8.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Ruta relativa.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Primera linea.",
                    },
                    "numbered": {
                        "type": "boolean",
                        "description": "Incluir numeros.",
                    },
                },
                "required": ["path"],
            },
        },
    }


def _c(profile: CompilerProfile = CONSERVATIVE) -> ToolSchemaCompiler:
    return ToolSchemaCompiler(profile)


# ── Formato basico ─────────────────────────────────────────────


def test_compila_tool_minimo():
    tool = {"name": "t", "description": "desc", "parameters": {}}
    out = _c().compile(tool)
    assert "## t" in out
    assert "desc" in out


def test_compila_leer_archivo():
    out = _c().compile(_leer_archivo())
    assert "## leer_archivo" in out
    assert "· path: s *" in out  # required
    assert "· start_line: i" in out
    assert "· numbered: b" in out


def test_required_marcado_con_asterisco():
    out = _c().compile(_leer_archivo())
    assert "· path: s *" in out
    assert "· start_line: i \u2014" in out  # no required


def test_descripcion_en_misma_linea():
    out = _c().compile(_leer_archivo())
    # DRO: una linea por parametro.
    lines = [ln for ln in out.split("\n") if ln.startswith("· ")]
    assert len(lines) == 3


def test_colapsa_whitespace_de_descripcion():
    tool = {
        "name": "t", "description": "  desc  con\n  saltos  ",
        "parameters": {
            "properties": {
                "x": {"type": "string", "description": "  a\n  b  "},
            },
        },
    }
    out = _c().compile(tool)
    assert "desc con saltos" in out
    assert "a b" in out


# ── Tipos (TAS) ────────────────────────────────────────────────


def test_siglas_de_tipos():
    tool = {
        "name": "t",
        "parameters": {
            "properties": {
                "a": {"type": "string"},
                "b": {"type": "integer"},
                "c": {"type": "number"},
                "d": {"type": "boolean"},
                "e": {"type": "array"},
                "f": {"type": "object"},
            },
        },
    }
    out = _c().compile(tool)
    assert "· a: s" in out
    assert "· b: i" in out
    assert "· c: n" in out
    assert "· d: b" in out
    assert "· e: a" in out
    assert "· f: o" in out


def test_tipo_desconocido_se_pasa_tal_cual():
    tool = {
        "name": "t",
        "parameters": {"properties": {"x": {"type": "custom"}}},
    }
    out = _c().compile(tool)
    assert "· x: custom" in out


def test_union_nullable():
    tool = {
        "name": "t",
        "parameters": {
            "properties": {"x": {"type": ["string", "null"]}},
        },
    }
    out = _c().compile(tool)
    assert "· x: s?" in out


def test_enum_inline():
    tool = {
        "name": "t",
        "parameters": {
            "properties": {
                "mode": {"type": "string", "enum": ["a", "b", "c"]},
            },
        },
    }
    out = _c().compile(tool)
    assert "· mode: s [a|b|c]" in out


def test_param_sin_type():
    tool = {
        "name": "t",
        "parameters": {"properties": {"x": {"description": "sin tipo"}}},
    }
    out = _c().compile(tool)
    assert "· x: ?" in out


# ── Defaults ──────────────────────────────────────────────────


def test_default_en_linea():
    tool = {
        "name": "t",
        "parameters": {
            "properties": {
                "n": {"type": "integer", "default": 5},
            },
        },
    }
    out = _c().compile(tool)
    assert "(def: 5)" in out


def test_default_string():
    tool = {
        "name": "t",
        "parameters": {
            "properties": {
                "s": {"type": "string", "default": "auto"},
            },
        },
    }
    out = _c().compile(tool)
    assert "(def: 'auto')" in out


# ── compile_all ───────────────────────────────────────────────


def test_compile_all_separa_por_linea_doble():
    a = {"name": "a", "parameters": {}}
    b = {"name": "b", "parameters": {}}
    out = _c().compile_all([a, b])
    assert out.count("## a") == 1
    assert out.count("## b") == 1
    assert "\n\n" in out


def test_compile_all_vacio():
    assert _c().compile_all([]) == ""


# ── Perfiles ──────────────────────────────────────────────────


def test_perfiles_existen():
    assert CONSERVATIVE.name == "conservative"
    assert BALANCED.name == "balanced"
    assert AGGRESSIVE.name == "aggressive"
    assert "sdm" in CONSERVATIVE.operators


# ── Ahorro de tokens (informal) ───────────────────────────────


def test_ahorro_vs_json():
    """El texto compilado ocupa menos que el JSON serializado."""
    tool = _leer_archivo()
    json_repr = json.dumps(tool, ensure_ascii=False)
    out = _c().compile(tool)
    # Sin cuantificar exacto: el compilado debe ser menor.
    assert len(out) < len(json_repr)
