python3 <<'SCRIPT'
from pathlib import Path

files = {}

files["core/model_capabilities.py"] = '''"""Deteccion de capacidades de modelos Ollama via /api/show."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Literal

import httpx


logger = logging.getLogger(__name__)

ToolMode = Literal["native", "xml", "unknown"]


@dataclass(frozen=True)
class ModelCapabilities:
    name: str
    native_tools: bool
    vision: bool = False
    thinking: bool = False
    probed: bool = True

    @property
    def tool_mode(self) -> ToolMode:
        if not self.probed:
            return "unknown"
        return "native" if self.native_tools else "xml"


_CACHE = {}
_CACHE_LOCK = threading.Lock()


def get_capabilities(host, model, *, timeout=5.0, force_refresh=False):
    if not model:
        return ModelCapabilities(name=model, native_tools=False, probed=False)

    key = (host.rstrip("/"), model)
    if not force_refresh:
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
        if cached is not None:
            return cached

    caps = _probe(host, model, timeout=timeout)
    with _CACHE_LOCK:
        _CACHE[key] = caps
    return caps


def _probe(host, model, *, timeout):
    url = f"{host.rstrip('/')}/api/show"
    try:
        response = httpx.post(url, json={"name": model}, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("No se pudieron obtener capacidades de %s: %s", model, exc)
        return ModelCapabilities(name=model, native_tools=False, probed=False)

    raw_caps = data.get("capabilities")
    if not isinstance(raw_caps, list):
        logger.info("Ollama sin capabilities para %s; asumiendo native=True", model)
        return ModelCapabilities(name=model, native_tools=True, probed=False)

    caps_lower = {str(c).lower() for c in raw_caps}
    return ModelCapabilities(
        name=model,
        native_tools="tools" in caps_lower,
        vision="vision" in caps_lower,
        thinking="thinking" in caps_lower,
        probed=True,
    )


def clear_cache():
    with _CACHE_LOCK:
        _CACHE.clear()
'''

files["core/xml_tools.py"] = '''"""Prompt-guided XML tool calling."""
from __future__ import annotations

import json
import logging
import re


logger = logging.getLogger(__name__)

_TOOL_CALL_XML = re.compile(r"<tool_call>\\s*(\\{.*?\\})\\s*</tool_call>", re.DOTALL | re.IGNORECASE)
_TOOL_USE_XML = re.compile(r"<tool_use>\\s*(\\{.*?\\})\\s*</tool_use>", re.DOTALL | re.IGNORECASE)
_TOOL_CALL_MD = re.compile(r"```(?:tool_call|tool|function)\\s*\\n(\\{.*?\\})\\s*\\n```", re.DOTALL | re.IGNORECASE)


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
        desc = fn.get("description", "").strip().replace("\\n", " ")
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
    return "\\n".join(lines)
'''

for path_str, content in files.items():
    p = Path(path_str)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    print(f"OK: {path_str} creado ({len(content)} bytes)")

from core.model_capabilities import get_capabilities
from core.xml_tools import parse_tool_calls
print("OK imports")
SCRIPT