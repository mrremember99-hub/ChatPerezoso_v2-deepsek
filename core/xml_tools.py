"""Prompt-guided XML tool calling."""
from __future__ import annotations

import json
import logging
import re


logger = logging.getLogger(__name__)

_TOOL_CALL_XML = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL | re.IGNORECASE)
_TOOL_USE_XML = re.compile(r"<tool_use>\s*(\{.*?\})\s*</tool_use>", re.DOTALL | re.IGNORECASE)
_TOOL_CALL_MD = re.compile(r"```(?:tool_call|tool|function)\s*\n(\{.*?\})\s*\n```", re.DOTALL | re.IGNORECASE)


def parse_tool_calls(text, known_tools=None):
    if not text:
        return []
    candidates = []
    for pattern in (_TOOL_CALL_XML, _TOOL_USE_XML, _TOOL_CALL_MD):
        candidates.extend(pattern.findall(text))
    if not candidates:
        return []
    calls = []
    for raw in candidates:
        parsed = _parse_json_call(raw, known_tools)
        if parsed is not None:
            calls.append(parsed)
    return calls


def strip_tool_call_blocks(text):
    if not text:
        return text
    for pattern in (_TOOL_CALL_XML, _TOOL_USE_XML, _TOOL_CALL_MD):
        text = pattern.sub("", text)
    return text.strip()


def _parse_json_call(raw, known_tools):
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("name") or data.get("tool") or data.get("tool_name")
    if not isinstance(name, str) or not name.strip():
        return None
    name = name.strip()
    args = data.get("arguments")
    if args is None:
        args = data.get("input")
    if args is None:
        args = data.get("parameters")
    if not isinstance(args, dict):
        args = {}
    if known_tools is not None and name not in known_tools:
        return None
    return name, args


def build_tools_prompt(tools):
    if not tools:
        return ""
    lines = [
        "## USO DE HERRAMIENTAS",
        "",
        "Para ejecutar una herramienta, responde EXCLUSIVAMENTE con:",
        '<tool_call>{"name": "NOMBRE", "arguments": {ARGS}}</tool_call>',
        "",
        "Sin texto antes ni despues. Espera el resultado antes de continuar.",
        "",
        "### Herramientas disponibles",
        "",
    ]
    for tool in tools:
        fn = tool.get("function", {})
        name = fn.get("name", "").strip()
        if not name:
            continue
        desc = fn.get("description", "").strip().replace("\n", " ")
        if len(desc) > 140:
            desc = desc[:137] + "..."
        params = fn.get("parameters", {})
        props = params.get("properties", {})
        required = set(params.get("required", []))
        sig_parts = []
        for pname, pspec in props.items():
            ptype = pspec.get("type", "string")
            mark = "" if pname in required else "?"
            sig_parts.append(f"{pname}{mark}: {ptype}")
        sig = ", ".join(sig_parts) if sig_parts else ""
        lines.append(f"- **{name}**({sig}) - {desc}")
    return "\n".join(lines)
