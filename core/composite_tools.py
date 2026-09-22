"""Une varios ``ToolProvider`` en uno solo y permite filtrar por nombre.

El primer provider que declara una herramienta es su dueño; los siguientes
no pueden sobrescribirla. Las definiciones y las reglas de intención se
recalculan en cada llamada, así que un provider que cambia su catálogo en
caliente (por ejemplo el ``MCPToolBridge`` al activar/desactivar servidores)
se refleja sin reiniciar.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from .intent import IntentRule, ToolIntentGate
from .tool_provider import ToolProvider

if TYPE_CHECKING:
    # Solo para Pylance: la anotación string "ToolCache" en la firma de
    # CachedToolProvider.__init__ necesita ver el símbolo a nivel de
    # módulo. En runtime se importa dentro del __init__ para evitar
    # dependencia circular con tool_cache.py.
    from .tool_cache import ToolCache


class CompositeToolProvider:
    def __init__(self, providers: list[ToolProvider]):
        self.providers = list(providers)
        self._owners: dict[str, ToolProvider] = {}
        # Caché de definitions() e intent_rules(). Se invalida
        # explícitamente desde AppController cuando un provider cambia
        # su catálogo en caliente (por ejemplo, MCP al activar o
        # desactivar un servidor). Sin esto, cada llamada a
        # definitions() recorre los N providers completos.
        self._cached_definitions: list[dict[str, Any]] | None = None
        self._cached_intent_rules: dict[str, IntentRule] | None = None
        self._cache_lock = threading.Lock()

    def invalidate(self) -> None:
        """Invalida las cachés de catálogo.

        Llamar cuando un provider de los envueltos cambia su catálogo
        en caliente. No hay invalidación automática: el llamante sabe
        cuándo ha cambiado algo.
        """
        with self._cache_lock:
            self._cached_definitions = None
            self._cached_intent_rules = None

    # -- catálogo ------------------------------------------------------------

    def definitions(self) -> list[dict[str, Any]]:
        with self._cache_lock:
            cached = self._cached_definitions
            if cached is not None:
                return cached

            owners: dict[str, ToolProvider] = {}
            result: list[dict[str, Any]] = []
            seen: set[str] = set()
            for provider in self.providers:
                for definition in provider.definitions():
                    name = str(
                        definition.get("function", {}).get("name", "")
                    ).strip()
                    if not name or name in seen:
                        continue
                    seen.add(name)
                    owners[name] = provider
                    result.append(definition)

            self._owners = owners
            self._cached_definitions = result
            return result

    def intent_rules(self) -> dict[str, IntentRule]:
        with self._cache_lock:
            cached = self._cached_intent_rules
            if cached is not None:
                return cached

            merged: dict[str, IntentRule] = {}
            for provider in self.providers:
                rules_method = getattr(provider, "intent_rules", None)
                if not callable(rules_method):
                    continue
                rules = rules_method()
                if not isinstance(rules, dict):
                    continue
                for name, rule in rules.items():
                    merged.setdefault(name, rule)

            self._cached_intent_rules = merged
            return merged

    # -- ejecución -----------------------------------------------------------

    def call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        allow_destructive: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> str:
        owner = self._owner_for(name)
        if owner is None:
            return f"ERROR: herramienta desconocida: {name}"
        return owner.call(
            name,
            arguments,
            allow_destructive=allow_destructive,
            cancel_event=cancel_event,
        )

    def requires_confirmation(self, name: str) -> bool:
        owner = self._owner_for(name)
        if owner is None:
            return False
        return owner.requires_confirmation(name)

    # -- integración con ToolIntentGate -------------------------------------

    def build_intent_gate(self) -> ToolIntentGate:
        rules = self.intent_rules()
        ToolIntentGate.register_rules(rules)
        return ToolIntentGate(rules)

    # -- interno -------------------------------------------------------------

    def _owner_for(self, name: str) -> ToolProvider | None:
        if not self._owners:
            self.definitions()
        return self._owners.get(name)


class FilteredToolProvider:
    """Envuelve otro provider y solo expone un subconjunto de herramientas.

    Se usa para implementar la lista de herramientas permitidas de cada
    agente. Un nombre no permitido en ``call`` devuelve un error textual
    en lugar de delegar al provider subyacente: así el modelo no puede
    colar una llamada por error.

    El conjunto ``allowed_names`` admite el comodín ``"mcp__*"``: si está
    presente, se permiten todas las herramientas MCP que el provider
    subyacente exponga, sin necesidad de enumerarlas una por una (lo cual
    sería imposible, porque los servidores MCP se activan y desactivan en
    caliente).
    """

    MCP_WILDCARD = "mcp__*"

    def __init__(self, source: ToolProvider, allowed_names: set[str]):
        self.source = source
        self.allowed_names = set(allowed_names)
        self._allow_all_mcp = self.MCP_WILDCARD in self.allowed_names

    # -- catálogo ------------------------------------------------------------

    def definitions(self) -> list[dict[str, Any]]:
        if not self.allowed_names:
            return []
        result: list[dict[str, Any]] = []
        for definition in self.source.definitions():
            name = _tool_name(definition)
            if not name:
                continue
            if name in self.allowed_names:
                result.append(definition)
            elif self._allow_all_mcp and name.startswith("mcp__"):
                result.append(definition)
        return result

    def intent_rules(self) -> dict[str, IntentRule]:
        rules_method = getattr(self.source, "intent_rules", None)
        if not callable(rules_method):
            return {}
        rules = rules_method()
        if not isinstance(rules, dict):
            return {}
        result: dict[str, IntentRule] = {}
        for name, rule in rules.items():
            if name in self.allowed_names:
                result[name] = rule
            elif self._allow_all_mcp and name.startswith("mcp__"):
                result[name] = rule
        return result

    # -- ejecución -----------------------------------------------------------

    def call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        allow_destructive: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> str:
        if not self._is_allowed(name):
            return f"ERROR: herramienta no permitida para este agente: {name}"
        return self.source.call(
            name,
            arguments,
            allow_destructive=allow_destructive,
            cancel_event=cancel_event,
        )

    def requires_confirmation(self, name: str) -> bool:
        if not self._is_allowed(name):
            return False
        return self.source.requires_confirmation(name)

    # -- interno -------------------------------------------------------------

    def _is_allowed(self, name: str) -> bool:
        if name in self.allowed_names:
            return True
        return self._allow_all_mcp and name.startswith("mcp__")


def _tool_name(definition: dict[str, Any]) -> str:
    return str(definition.get("function", {}).get("name", "")).strip()


class CachedToolProvider:
    """Envuelve un ToolProvider y cachea las herramientas de solo lectura.

    Se coloca entre el CompositeToolProvider y el ChatController. El
    Composite no sabe que está cacheado; el ChatController tampoco.

    Args:
        source: provider subyacente.
        cacheable: nombres de herramientas cuyos resultados se cachean.
        invalidating: nombres de herramientas que, al ejecutarse, vacían
            el caché completo (escrituras, borrados, shell).
        cache: instancia compartida de ToolCache. Si no se pasa, se crea
            una nueva. Útil para compartir entre providers.
    """

    def __init__(
        self,
        source: ToolProvider,
        *,
        cacheable: set[str] | None = None,
        invalidating: set[str] | None = None,
        cache: "ToolCache | None" = None,
    ):
        from .tool_cache import ToolCache
        self.source = source
        self._cacheable = set(cacheable or ())
        self._invalidating = set(invalidating or ())
        self.cache = cache or ToolCache()

    # -- catálogo (delegación directa) ---------------------------------------

    def definitions(self):
        return self.source.definitions()

    def intent_rules(self):
        return self.source.intent_rules()

    def requires_confirmation(self, name: str) -> bool:
        return self.source.requires_confirmation(name)

    # -- ejecución -----------------------------------------------------------

    def call(
        self,
        name: str,
        arguments: dict,
        *,
        allow_destructive: bool = False,
        cancel_event=None,
    ) -> str:
        # Herramientas que invalidan el caché completo: se ejecutan y
        # limpian. El workspace pudo cambiar.
        if name in self._invalidating:
            result = self.source.call(
                name, arguments,
                allow_destructive=allow_destructive,
                cancel_event=cancel_event,
            )
            self.cache.invalidate_all()
            return result

        # Herramientas no cacheables: bypass directo.
        if name not in self._cacheable:
            return self.source.call(
                name, arguments,
                allow_destructive=allow_destructive,
                cancel_event=cancel_event,
            )

        # Cacheables: consultar caché primero.
        cached = self.cache.get(name, arguments)
        if cached is not None:
            return cached

        result = self.source.call(
            name, arguments,
            allow_destructive=allow_destructive,
            cancel_event=cancel_event,
        )
        # No cachear errores: un fallo puede ser transitorio.
        if not result.startswith("ERROR"):
            self.cache.put(name, arguments, result)
        return result
