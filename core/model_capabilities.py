"""Deteccion de capacidades de modelos Ollama via /api/show."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Literal

import httpx

from .models_config import get_override


logger = logging.getLogger(__name__)

ToolMode = Literal["native", "xml", "unknown"]


@dataclass(frozen=True)
class ModelCapabilities:
    name: str
    native_tools: bool
    vision: bool = False
    thinking: bool = False
    probed: bool = True
    # De donde sale el modo: "override" si viene de models.json,
    # "probe" si viene de /api/show, "fallback" si no pudimos consultar.
    source: str = "probe"
    # Longitud del contexto del modelo en tokens, segun el GGUF.
    # 0 = desconocido (no pudimos consultar /api/show o el modelo no
    # expone model_info). En ese caso el llamante debe asumir un valor
    # conservador (4096, el default de Ollama).
    context_length: int = 0
    # Texto corto que resume para qué sirve el modelo. Se muestra en
    # la sidebar. Viene del override manual o de una heurística según
    # las capabilities detectadas.
    recommendation: str = ""

    @property
    def tool_mode(self) -> ToolMode:
        if not self.probed:
            return "unknown"
        return "native" if self.native_tools else "xml"


_CACHE = {}
_CACHE_LOCK = threading.Lock()


def _auto_recommendation(caps: ModelCapabilities) -> str:
    """Heurística simple para sugerir un uso al usuario.

    No pretende ser precisa: solo evitar que el usuario use un modelo
    con thinking para tool calling rápido, o al revés.
    """
    if not caps.probed:
        return "Capacidades sin detectar"
    if caps.thinking and caps.native_tools:
        return "Thinking · razona lento, para análisis"
    if caps.thinking and not caps.native_tools:
        return "Thinking · sin tool calling nativo"
    if caps.native_tools:
        return "Rápido · tool calling fiable"
    if caps.tool_mode == "xml":
        return "Modo XML · tool calling limitado"
    return ""


def _with_recommendation(
    caps: ModelCapabilities,
    override,
) -> ModelCapabilities:
    """Aplica la recomendación del override, o una automática si no hay."""
    rec = override.recommendation or _auto_recommendation(caps)
    if not rec:
        return caps
    from dataclasses import replace
    return replace(caps, recommendation=rec)


def get_capabilities(host, model, *, timeout=5.0, force_refresh=False):
    if not model:
        return ModelCapabilities(name=model, native_tools=False, probed=False, source="fallback")

    # 1. Override manual tiene prioridad absoluta.
    override = get_override(model)
    if override.is_forced():
        native = override.mode == "native"
        caps = ModelCapabilities(
            name=model,
            native_tools=native,
            probed=True,
            source="override",
        )
        return _with_recommendation(caps, override)

    # 2. Sin override: consultamos /api/show (con caché).
    key = (host.rstrip("/"), model)
    if not force_refresh:
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
        if cached is not None:
            return _with_recommendation(cached, override)

    caps = _probe(host, model, timeout=timeout)
    with _CACHE_LOCK:
        _CACHE[key] = caps
    return _with_recommendation(caps, override)


def _probe(host, model, *, timeout):
    url = f"{host.rstrip('/')}/api/show"
    try:
        response = httpx.post(url, json={"name": model}, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # No pudimos consultar /api/show. Asumimos native (comportamiento
        # histórico): si el modelo sí soporta tools, todo funciona como
        # antes. Si no las soporta, el regex textual sigue siendo un
        # fallback razonable. `probed=False` permite distinguir este
        # caso de una detección confirmada.
        logger.warning("No se pudieron obtener capacidades de %s: %s", model, exc)
        return ModelCapabilities(name=model, native_tools=True, probed=False, source="fallback")

    raw_caps = data.get("capabilities")
    if not isinstance(raw_caps, list):
        logger.info("Ollama sin capabilities para %s; asumiendo native=True", model)
        return ModelCapabilities(name=model, native_tools=True, probed=False, source="fallback")

    caps_lower = {str(c).lower() for c in raw_caps}
    context_length = _extract_context_length(data.get("model_info"))
    return ModelCapabilities(
        name=model,
        native_tools="tools" in caps_lower,
        vision="vision" in caps_lower,
        thinking="thinking" in caps_lower,
        probed=True,
        context_length=context_length,
    )


def _extract_context_length(model_info) -> int:
    """Extrae la longitud del contexto del bloque model_info de /api/show.

    La clave varia por arquitectura: "llama.context_length",
    "gemma4.context_length", "qwen2.context_length", etc. Buscamos
    cualquier clave que termine en ".context_length" y sea un entero.

    Tambien aceptamos "context_length" sin prefijo por si alguna
    version de Ollama lo expone plano.

    Devuelve 0 si no se encuentra (desconocido).
    """
    if not isinstance(model_info, dict):
        return 0
    for key, value in model_info.items():
        if not isinstance(key, str):
            continue
        if key == "context_length" or key.endswith(".context_length"):
            # Excluir bool explicitamente: isinstance(True, int) es True
            # en Python, pero un booleano no es un context_length valido.
            if isinstance(value, bool):
                continue
            if isinstance(value, int) and value > 0:
                return value
            if isinstance(value, float) and value > 0:
                return int(value)
    return 0


def clear_cache():
    with _CACHE_LOCK:
        _CACHE.clear()
