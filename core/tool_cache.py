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

# Cota dura de entradas. Una sesión normal no necesita más; evita
# crecimiento no acotado en sesiones largas con argumentos muy
# variados (por ejemplo, búsquedas con queries distintas).
_MAX_ENTRIES = 256


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

    # Umbral a partir del cual vale la pena purgar preventivamente.
    # No queremos purgar en cada put (O(n) sobre el diccionario),
    # pero tampoco esperar a llegar al tope. Con 80% del límite, la
    # purga es puntual y el diccionario nunca se llena.
    _PURGE_THRESHOLD = int(_MAX_ENTRIES * 0.8)

    def _purge_expired(self, now: float) -> int:
        """Elimina entradas expiradas. Devuelve cuántas eliminó.

        Se llama desde put() cuando el diccionario se acerca al tope.
        Coste O(n) sobre el diccionario, aceptable porque solo ocurre
        cuando ya hay muchas entradas acumuladas.
        """
        before = len(self._entries)
        self._entries = {
            k: v for k, v in self._entries.items() if v[0] > now
        }
        return before - len(self._entries)

    def put(self, tool_name: str, arguments: dict[str, Any], result: str) -> None:
        ttl = self._ttls.get(tool_name, self._default_ttl)
        if ttl <= 0:
            return
        now = time.monotonic()
        expires_at = now + ttl
        key = _key(tool_name, arguments)
        with self._lock:
            # Purga proactiva: si el diccionario se acerca al tope,
            # eliminar entradas expiradas antes de insertar. Esto
            # evita acumular basura hasta que el diccionario esté
            # lleno del todo.
            if len(self._entries) >= self._PURGE_THRESHOLD:
                self._purge_expired(now)
            # Si tras purgar sigue lleno (muchas entradas vivas),
            # descartar la más antigua por orden de inserción.
            if len(self._entries) >= _MAX_ENTRIES:
                oldest_key = next(iter(self._entries))
                del self._entries[oldest_key]
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
