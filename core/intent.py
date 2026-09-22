"""Heurística de intención para el uso de herramientas.

Decide, a partir del último mensaje del usuario, si procede ofrecer
herramientas al modelo y si una llamada de herramienta concreta se
corresponde con lo que el usuario pidió.

Importante: esto NO es un mecanismo de seguridad. Es un filtro
anti-alucinación para reducir llamadas de herramientas no solicitadas.
El control de seguridad real frente a operaciones destructivas es la
confirmación explícita en la interfaz.

Las reglas concretas de cada herramienta las declara su ``ToolProvider``
a través del método ``intent_rules()``. Aquí vive solo la maquinaria.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Any


_CONJUGATION_SUFFIXES: tuple[str, ...] = (
    "o", "as", "a", "amos", "áis", "an",
    "o", "es", "e", "emos", "éis", "en",
    "o", "es", "e", "imos", "ís", "en",
    "é", "aste", "ó", "amos", "asteis", "aron",
    "í", "iste", "ió", "imos", "isteis", "ieron",
    "e", "es", "e", "emos", "éis", "en",
    "a", "as", "a", "amos", "áis", "an",
)


MCP_ACTION_VERBS: tuple[str, ...] = (
    "usa", "usar", "utiliza", "utilizar", "llama", "llamar",
    "invoca", "invocar", "ejecuta", "ejecutar", "realiza", "realizar",
)


# Detecta un nombre de archivo con extensión (por ejemplo "notas.txt").
_FILENAME_PATTERN = re.compile(
    r"\b[\w\-]+\.(?:txt|md|markdown|csv|tsv|json|py|js|ts|jsx|tsx|html|htm|css"
    r"|xml|yaml|yml|log|pdf|docx?|xlsx?|ini|cfg|conf|sh|png|jpe?g|gif|svg|webp|toml)\b",
    re.IGNORECASE,
)


def _strip_accents(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFD", text.lower())
        if unicodedata.category(char) != "Mn"
    )


@dataclass(frozen=True)
class IntentRule:
    """Reglas de intención para una herramienta concreta.

    Una regla vacía (``IntentRule()``) nunca autoriza la herramienta.
    """

    # Verbos que activan la herramienta ("crea", "lee", "diff").
    verbs: tuple[str, ...] = ()

    # Palabras-objetivo del dominio de la herramienta ("archivo", "repo").
    target_words: tuple[str, ...] = ()

    # Si es True (por defecto), además del verbo debe aparecer una
    # palabra-objetivo o un nombre de archivo (si ``accepts_filename``).
    requires_target: bool = True

    # Si es True, un nombre de archivo con extensión cuenta como target válido.
    accepts_filename: bool = False

    # Si es True, esta herramienta puede autorizarse como paso previo cuando
    # el usuario pide escribir/editar/actualizar algo.
    is_read_prerequisite: bool = False

    # Regla especial para herramientas MCP genéricas.
    mcp_explicit_name_required: bool = False


class ToolIntentGate:
    """Evalúa si una petición autoriza exponer/ejecutar una herramienta.

    Tanto el texto del usuario como las palabras declaradas en las reglas
    se normalizan (minúsculas + sin acentos) antes de compararse. Así una
    regla puede declarar ``"dónde"`` y matchear ``"¿dónde está X?"`` aunque
    el usuario escriba con tilde o sin ella.
    """

    _RULES_REGISTRY: dict[str, IntentRule] = {}

    def __init__(self, rules: dict[str, IntentRule] | None = None):
        self.rules: dict[str, IntentRule] = dict(rules or {})

    # -- API pública ---------------------------------------------------------

    def tools_for_request(
        self, tools: list[dict[str, Any]] | None, text: str
    ) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        if not (
            self._mentions_workspace_operation(text)
            or self._mentions_mcp_tool(text, tools)
        ):
            return None
        return tools

    def tool_is_requested(self, name: str, text: str) -> bool:
        """Decide si el texto autoriza la herramienta ``name``."""
        rule = self.rules.get(name)
        if rule is None:
            if name.startswith("mcp__"):
                rule = IntentRule(mcp_explicit_name_required=True)
            else:
                return False

        if self._is_negated(rule.verbs, text):
            return False

        if rule.mcp_explicit_name_required:
            return self._mcp_explicit_request(name, text)

        normalised = self._normalise(text)

        if rule.requires_target and not self._mentions_target(
            rule.target_words, text, normalised, rule.accepts_filename
        ):
            return False

        if self._mentions_any_word(normalised, rule.verbs):
            return True

        if rule.is_read_prerequisite and self._mentions_any_word(
            normalised, self._read_prerequisite_verbs()
        ):
            return True
        return False

    # -- negación ------------------------------------------------------------

    @classmethod
    def _is_negated(cls, verbs: tuple[str, ...], text: str) -> bool:
        """Detecta si la petición principal niega explícitamente la operación.

        Regla: cuenta como negación solo si NINGÚN verbo del conjunto
        aparece en forma afirmativa. Si hay al menos una aparición
        afirmativa, las negaciones de otros verbos del mismo conjunto
        se interpretan como restricciones secundarias, no como bloqueo.

        Ejemplos:
            "Crea un archivo. No añadas funciones."
              → "crea" es afirmativo. NO se bloquea.
            "No crees el archivo todavía."
              → "crees" solo aparece negado. Se bloquea.
            "no vas a borrar archivo.txt"
              → "borrar" solo aparece negado (con "vas a" en medio).
                Se bloquea.
        """
        if not verbs:
            return False
        normalised = cls._normalise(text)

        # Paso 1: ¿hay alguna forma afirmativa de algún verbo?
        has_any_affirmative = False
        for verb in verbs:
            v = cls._normalise(verb)
            if not v:
                continue
            for form in _cached_verb_forms(v):
                for match in re.finditer(
                    rf"\b{re.escape(form)}\b", normalised, re.IGNORECASE
                ):
                    start = match.start()
                    # Miramos hacia atrás: si el "no" (posiblemente con
                    # hasta 3 palabras en medio) llega justo hasta aquí,
                    # este match es negado, no afirmativo.
                    prefix = normalised[max(0, start - 40):start]
                    if not re.search(
                        r"\bno\b(?:\s+\w+){0,3}\s+$", prefix
                    ):
                        has_any_affirmative = True
                        break
                if has_any_affirmative:
                    break
            if has_any_affirmative:
                break

        if has_any_affirmative:
            return False

        # Paso 2: sin formas afirmativas, comprobar si hay negación.
        for verb in verbs:
            v = cls._normalise(verb)
            if not v:
                continue
            for form in _cached_verb_forms(v):
                pattern = (
                    rf"\bno\b(?:\s+\w+){{0,3}}\s+{re.escape(form)}\b"
                )
                if re.search(pattern, normalised, re.IGNORECASE):
                    return True
        return False

    @classmethod
    def _mcp_explicit_request(cls, name: str, text: str) -> bool:
        normalised = cls._normalise(text)
        direct_name = cls._normalise(name)
        if not re.search(rf"\b{re.escape(direct_name)}\b", normalised):
            return False
        if cls._is_negated(MCP_ACTION_VERBS, text):
            return False
        return cls._mentions_any_word(normalised, MCP_ACTION_VERBS)

    # -- detección de palabras ----------------------------------------------

    @classmethod
    def _mentions_any_word(cls, normalised: str, words: tuple[str, ...]) -> bool:
        """Cierto si alguna de ``words`` aparece con fronteras de palabra.

        Las palabras se normalizan (minúsculas + sin acentos) antes de
        compararlas. ``\\b`` respeta la transición entre un carácter no-letra
        ("¿", ",", "?") y una letra, así que "¿dónde" y "dónde?" matchean
        la palabra normalizada "donde".
        """
        for word in words:
            w = cls._normalise(word)
            if not w:
                continue
            if re.search(rf"\b{re.escape(w)}\b", normalised):
                return True
        return False

    @classmethod
    def _mentions_target(
        cls,
        words: tuple[str, ...],
        text: str,
        normalised: str,
        accepts_filename: bool,
    ) -> bool:
        if words and cls._mentions_any_word(normalised, words):
            return True
        if accepts_filename and _FILENAME_PATTERN.search(text):
            return True
        return False

    # -- operaciones de workspace -------------------------------------------

    @classmethod
    def _mentions_workspace_operation(cls, text: str) -> bool:
        normalised = cls._normalise(text)
        for rule in list(cls._RULES_REGISTRY.values()):
            if rule.mcp_explicit_name_required:
                continue
            if rule.requires_target and not cls._mentions_target(
                rule.target_words, text, normalised, rule.accepts_filename
            ):
                continue
            if cls._mentions_any_word(normalised, rule.verbs):
                return True
        return False

    @classmethod
    def _mentions_mcp_tool(cls, text: str, tools: list[dict[str, Any]]) -> bool:
        normalised = cls._normalise(text)
        if not cls._mentions_any_word(normalised, MCP_ACTION_VERBS):
            return False
        for item in tools:
            name = str(item.get("function", {}).get("name", ""))
            if not name:
                continue
            direct_name = cls._normalise(name)
            if re.search(rf"\b{re.escape(direct_name)}\b", normalised):
                return True
        return False

    # -- registro global -----------------------------------------------------

    @classmethod
    def register_rules(cls, rules: dict[str, IntentRule]) -> None:
        cls._RULES_REGISTRY.update(rules)

    @classmethod
    def unregister_rules(cls, names: list[str]) -> None:
        for name in names:
            cls._RULES_REGISTRY.pop(name, None)

    @classmethod
    def _read_prerequisite_verbs(cls) -> tuple[str, ...]:
        return (
            "escribe", "escribir", "edita", "editar", "actualiza", "actualizar",
            "reemplaza", "reemplazar", "cambia", "cambiar", "modifica", "modificar",
        )

    # -- normalización -------------------------------------------------------

    @staticmethod
    def _normalise(text: str) -> str:
        return _strip_accents(text)


@lru_cache(maxsize=None)
def _cached_verb_forms(verb: str) -> tuple[str, ...]:
    """Formas conjugadas a partir de un verbo ya normalizado (sin acentos)."""
    forms: set[str] = {verb}
    stem = ""
    if verb.endswith(("ar", "er", "ir")) and len(verb) > 3:
        stem = verb[:-2]
    elif verb and verb[-1] in "aeo" and len(verb) > 3:
        stem = verb[:-1]
    if len(stem) >= 2:
        forms.update(f"{stem}{suffix}" for suffix in _CONJUGATION_SUFFIXES)
    return tuple(forms)
