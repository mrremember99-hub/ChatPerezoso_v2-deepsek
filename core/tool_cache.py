"""Caché de resultados de herramientas con TTL corto.

Evita re-ejecutar `git_status`, `buscar_en_workspace` o `listar_carpeta`
si el modelo pide exactamente lo mismo en pocos segundos. Es habitual
en conversaciones con tool calling: el modelo consulta varias veces el
mismo archivo mientras razona.

Reglas:
  · Solo se cachean herramientas marcadas como `cacheable`.
  · Las herramientas `invalidating` (escritura, borrado, shell) borran
    todo el caché al ejecutarse: el workspace pudo cambiar.
  · Los resultados que empiezan por ERROR no se cachean.
  · La clave es (nombre, argumentos serializados de forma estable).
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable


# TTL por defecto: 5 segundos. Suficiente para absorber ráfagas del
# modelo sin servir datos obsoletos al usuario.
DEFAULT_TTL_SECONDS = 5.0


def _key(name: str, arguments: dict[str, Any]) -> tuple:
    """Clave estable: ordena las claves para que {a:1,b:2} y {b:2,a:1} coincidan."""
    try:
        items = tuple(sorted((k, _freeze(v)) for k, v in arguments.items()))
    except TypeError:
        # Argumentos no serializables: se cachea bajo la representación
        # de su repr. Es raro, pero evita un crash.
        items = (("__repr__", repr(arguments)),)
    return (name, items)


def _freeze(value: Any) -> Any:
    """Convierte valores anidados en algo hashable."""
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, set):
        return tuple(sorted(_freeze(v) for v in value))
    return value


class ToolCache:
    def __init__(self, default_ttl: float = DEFAULT_TTL_SECONDS):
        self._default_ttl = default_ttl
        self._ttls: dict[str, float] = {}
        self._entries: dict[tuple, tuple[float, str]] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    # -- configuración -------------------------------------------------------

    def set_ttl(self, tool_name: str, ttl: float) -> None:
        self._ttls[tool_name] = ttl

    # -- operaciones ---------------------------------------------------------

    def get(self, tool_name: str, arguments: dict[str, Any]) -> str | None:
        key = _key(tool_name, arguments)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            expires_at, value = entry
            if time.monotonic() >= expires_at:
                del self._entries[key]
                self._misses += 1
                return None
            self._hits += 1
            return value

    def put(self, tool_name: str, arguments: dict[str, Any], result: str) -> None:
        ttl = self._ttls.get(tool_name, self._default_ttl)
        if ttl <= 0:
            return
        expires_at = time.monotonic() + ttl
        key = _key(tool_name, arguments)
        with self._lock:
            self._entries[key] = (expires_at, result)

    def invalidate_all(self) -> None:
        with self._lock:
            self._entries.clear()

    def invalidate(self, tool_name: str) -> None:
        with self._lock:
            for key in list(self._entries.keys()):
                if key[0] == tool_name:
                    del self._entries[key]

    # -- métricas (para tests y diagnóstico) ---------------------------------

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._entries)
