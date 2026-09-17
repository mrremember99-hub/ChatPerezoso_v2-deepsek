"""Descubrimiento dinámico de plugins vía entry points."""
from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import Any, Callable

logger = logging.getLogger(__name__)

PLUGIN_GROUP = "chatperezoso.plugins"


def discover_plugin_factories() -> list[tuple[str, Callable[[Any], Any]]]:
    factories: list[tuple[str, Callable[[Any], Any]]] = []
    for ep in entry_points(group=PLUGIN_GROUP):
        try:
            factory = ep.load()
        except Exception as exc:
            logger.warning("Plugin %s no se pudo cargar: %s", ep.name, exc)
            continue
        if not callable(factory):
            logger.warning("Plugin %s no es invocable, se ignora.", ep.name)
            continue
        factories.append((ep.name, factory))
    return factories


def instantiate_plugins(
    factories: list[tuple[str, Callable[[Any], Any]]],
    workspace: Any,
) -> list[Any]:
    instances: list[Any] = []
    for name, factory in factories:
        try:
            instance = factory(workspace)
        except Exception as exc:
            logger.warning("Plugin %s falló al instanciarse: %s", name, exc)
            continue
        instances.append(instance)
    return instances
