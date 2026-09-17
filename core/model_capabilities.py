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

    @property
    def tool_mode(self) -> ToolMode:
        if not self.probed:
            return "unknown"
        return "native" if self.native_tools else "xml"


_CACHE = {}
_CACHE_LOCK = threading.Lock()


def get_capabilities(host, model, *, timeout=5.0, force_refresh=False):
    if not model:
        return ModelCapabilities(name=model, native_tools=False, probed=False, source="fallback")

    # 1. Override manual tiene prioridad absoluta.
    override = get_override(model)
    if override.is_forced():
        native = override.mode == "native"
        return ModelCapabilities(
            name=model,
            native_tools=native,
            probed=True,
            source="override",
        )

    # 2. Sin override: consultamos /api/show (con caché).
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
