"""Cobertura de la rama rule=None + nombre mcp__ en tool_is_requested.

Un provider MCP que declara tools sin pasar por intent_rules() debe
autorizarse vía la regla automática mcp_explicit_name_required en vez
de caer por la rama 'return False'. Cierra el hueco de cobertura de
core/intent.py línea ~195.
"""
from __future__ import annotations

from core.intent import ToolIntentGate


def test_mcp_sin_regla_autoriza_con_nombre_explicito():
    gate = ToolIntentGate(rules={})
    assert gate.tool_is_requested(
        "mcp__fs__read",
        "usa mcp__fs__read para listar el directorio",
    ) is True


def test_mcp_sin_regla_no_autoriza_sin_nombre():
    gate = ToolIntentGate(rules={})
    assert gate.tool_is_requested(
        "mcp__fs__read",
        "lista el directorio por favor",
    ) is False
