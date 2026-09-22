"""Regresión TR-1 y TR-2 sobre ToolRegistry.

TR-1: las excepciones inesperadas de _dispatch se logean con traza
completa y devuelven un mensaje opaco al modelo. Los WorkspaceError
esperados siguen devolviendo el mensaje limpio.

TR-2: _matches_type valida array y object y rechaza tipos desconocidos
en vez de aceptarlos a ciegas.
"""
from __future__ import annotations

import logging

import pytest

from core.tools import ToolRegistry, _matches_type
from core.workspace import Workspace, WorkspaceError


# ── TR-1: dispatch de errores ────────────────────────────────────────

def test_workspace_error_returns_clean_message(tmp_path):
    """Un WorkspaceError no se logea como 'inesperado'."""
    registry = ToolRegistry(Workspace(tmp_path))

    result = registry.call("leer_archivo", {"path": "no-existe.txt"})
    assert result.startswith("ERROR: ")
    # No debe contener "ERROR interno en".
    assert "ERROR interno" not in result


def test_unexpected_exception_is_logged_and_opaque(
    tmp_path, monkeypatch, caplog,
):
    """Un ValueError en _dispatch se logea y devuelve mensaje opaco."""
    registry = ToolRegistry(Workspace(tmp_path))

    def exploding_dispatch(name, arguments):
        raise ValueError("mensaje interno que no debe llegar al modelo")

    monkeypatch.setattr(registry, "_dispatch", exploding_dispatch)

    with caplog.at_level(logging.ERROR):
        result = registry.call("listar_carpeta", {"path": "."})

    # El modelo ve un mensaje opaco, sin el texto de la excepción.
    assert result.startswith("ERROR interno en listar_carpeta")
    assert "ValueError" in result
    assert "mensaje interno que no debe llegar al modelo" not in result

    # Y en los logs aparece la traza completa.
    assert any(
        "Error inesperado en tool listar_carpeta" in rec.message
        for rec in caplog.records
    )
    assert caplog.records[-1].exc_info is not None


def test_workspace_error_is_not_logged_as_unexpected(
    tmp_path, monkeypatch, caplog,
):
    """Un WorkspaceError no genera logger.exception."""
    registry = ToolRegistry(Workspace(tmp_path))

    def raising_workspace_error(name, arguments):
        raise WorkspaceError("ruta fuera del workspace")

    monkeypatch.setattr(registry, "_dispatch", raising_workspace_error)

    with caplog.at_level(logging.ERROR):
        result = registry.call("leer_archivo", {"path": "x"})

    assert result == "ERROR: ruta fuera del workspace"
    assert not caplog.records, (
        f"WorkspaceError no deberia logear; logs: {caplog.records}"
    )


# ── TR-2: validación de tipos ────────────────────────────────────────

def test_matches_type_array_accepts_list():
    assert _matches_type([], "array")
    assert _matches_type([1, 2, 3], "array")


def test_matches_type_array_rejects_non_list():
    assert not _matches_type("string", "array")
    assert not _matches_type(42, "array")
    assert not _matches_type({"a": 1}, "array")
    assert not _matches_type(True, "array")


def test_matches_type_object_accepts_dict():
    assert _matches_type({}, "object")
    assert _matches_type({"a": 1}, "object")


def test_matches_type_object_rejects_non_dict():
    assert not _matches_type([], "object")
    assert not _matches_type("string", "object")
    assert not _matches_type(42, "object")


def test_matches_type_rejects_unknown_type():
    """Un tipo desconocido no debe pasar silenciosamente."""
    assert not _matches_type("cualquier cosa", "tipo-inventado")
    assert not _matches_type(42, "unknown-type")
    assert not _matches_type(None, "")