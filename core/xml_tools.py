"""Prompt-guided XML tool calling."""
from __future__ import annotations

import json
import logging
import re


logger = logging.getLogger(__name__)

_TOOL_CALL_XML = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL | re.IGNORECASE)
_TOOL_USE_XML = re.compile(r"<tool_use>\s*(\{.*?\})\s*</tool_use>", re.DOTALL | re.IGNORECASE)
_TOOL_CALL_MD = re.compile(r"```(?:tool_call|tool|function)\s*\n(\{.*?\})\s*\n```", re.DOTALL | re.IGNORECASE)

# Dialecto "<function=NAME>...<parameter=K>V</parameter>...</function>"
# que emiten algunos modelos (qwen3-coder, ciertos Hermes) cuando
# ignoran el tool calling nativo de Ollama y responden por texto.
_FUNCTION_XML = re.compile(
    r"<function=([a-zA-Z0-9_]+)>\s*(.*?)\s*</function>",
    re.DOTALL | re.IGNORECASE,
)
_PARAMETER_XML = re.compile(
    r"<parameter=([a-zA-Z0-9_]+)>\s*(.*?)\s*</parameter>",
    re.DOTALL | re.IGNORECASE,
)


def parse_tool_calls(text, known_tools=None):
    if not text:
        return []
    # 1. Dialecto <function=NAME>...</function>.
    calls = parse_function_xml(text, known_tools=known_tools)
    if calls:
        return calls
    # 2. Dialectos JSON-in-markup (tool_call, tool_use, fenced).
    candidates = []
    for pattern in (_TOOL_CALL_XML, _TOOL_USE_XML, _TOOL_CALL_MD):
        candidates.extend(pattern.findall(text))
    if not candidates:
        return []
    for raw in candidates:
        parsed = _parse_json_call(raw, known_tools)
        if parsed is not None:
            calls.append(parsed)
    return calls


def parse_function_xml(text, known_tools=None):
    """Parsea el dialecto ``<function=NAME>`` con ``<parameter=K>V</parameter>``.

    Es el formato que emiten modelos como qwen3-coder cuando ignoran
    el tool calling nativo de Ollama y responden con XML en el texto.
    Cada ``<parameter>`` se convierte en una entrada del dict de
    argumentos. El valor se intenta convertir a int/bool/float; si no,
    se deja como str.
    """
    if not text or "<function=" not in text:
        return []
    calls = []
    for match in _FUNCTION_XML.finditer(text):
        name = match.group(1).strip()
        if not name:
            continue
        if known_tools is not None and name not in known_tools:
            continue
        body = match.group(2)
        args: dict = {}
        for param in _PARAMETER_XML.finditer(body):
            key = param.group(1).strip()
            raw_value = param.group(2).strip()
            args[key] = _coerce_value(raw_value)
        calls.append((name, args))
    return calls


def _coerce_value(raw: str):
    """Convierte un valor textual a int/bool/float si es posible."""
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null" or lowered == "none":
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def strip_tool_call_blocks(text):
    if not text:
        return text
    for pattern in (_TOOL_CALL_XML, _TOOL_USE_XML, _TOOL_CALL_MD):
        text = pattern.sub("", text)
    # También el dialecto <function=NAME>...</function> con sus
    # parámetros anidados. Se limpia el bloque completo, no solo la
    # etiqueta exterior, para no dejar los <parameter> sueltos.
    text = _FUNCTION_XML.sub("", text)
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
        "Las rutas son RELATIVAS al workspace. Usa \".\" para la raiz, "
        "nunca \"/\" ni rutas absolutas.",
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
