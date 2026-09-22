"""Estrategias de tool calling.

Encapsula las dos formas de invocar herramientas, con una interfaz
común. `OllamaClient.chat()` elige la estrategia según las capacidades
del modelo (native vs xml) y delega en ella la lógica específica.

Motivación del refactor: antes, `chat()` tenía dos ramas `if/else`
que duplicaban el bloque de autorización `gate.tool_is_requested(...)`.
La duplicación es una bomba de relojería: si mañana alguien cambia
una de las dos copias, la otra queda desincronizada y puede saltarse
el bloqueo de seguridad. Con estrategias, hay UN solo punto donde se
toma la decisión de autorizar.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .intent import ToolIntentGate


logger = logging.getLogger(__name__)


# ── Resultado de una ronda ────────────────────────────────────────────

@dataclass
class RoundResult:
    """Qué ha pasado en una ronda de generación.

    El `chat()` que delega en la estrategia mira estos campos para
    decidir si ejecutar tools, pedir reintento o cerrar el bucle.
    """
    is_final: bool = False
    final_text: str = ""
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    visible_text: str = ""
    assistant_content: str = ""
    retry_requested: bool = False
    retry_message: str = ""


# ── Autorización compartida ───────────────────────────────────────────

# Mensaje único de bloqueo. Antes estaba duplicado literalmente en las
# dos ramas del chat(). Ahora hay una sola copia.
#
# Es importante que el mensaje indique al modelo QUE NO REINTENTE la
# misma herramienta: sin esa instrucción explícita, modelos pequeños
# entran en bucle intentando la misma llamada una y otra vez, gastando
# las rondas disponibles sin producir nada útil.
_BLOCKED_MESSAGE = (
    "OPERACIÓN NO AUTORIZADA: la última petición del usuario no "
    "autoriza esta herramienta sobre el workspace. NO vuelvas a "
    "intentar la misma herramienta. En su lugar, responde al usuario "
    "explicando qué necesitas para proceder (por ejemplo, pídele que "
    "confirme el nombre del archivo o reformule la petición)."
)


def authorize_and_execute(
    name: str,
    arguments: dict[str, Any],
    gate: ToolIntentGate,
    authorization_text: str,
    on_tool,
) -> str:
    """Ejecuta una tool SI el gate la autoriza. Único punto de decisión.

    Esta función es la salvaguarda compartida entre estrategias. No se
    duplica: si cambia la política de autorización, cambia aquí y las
    dos estrategias la heredan.
    """
    if not gate.tool_is_requested(name, authorization_text):
        logger.warning(
            "Tool call bloqueado por el gate: %s (última petición: %r)",
            name, authorization_text[:80],
        )
        return _BLOCKED_MESSAGE
    return on_tool(name, arguments)


# ── Interfaz ──────────────────────────────────────────────────────────

@runtime_checkable
class ToolCallingStrategy(Protocol):
    """Contrato que deben cumplir NativeToolStrategy y XmlToolStrategy."""

    def prepare_system_prompt(self, active_tools: list[dict[str, Any]]) -> str:
        """Texto a inyectar en el system prompt para describir las tools."""
        ...

    def should_send_tools_param(self) -> bool:
        """True si el payload debe incluir `tools` en la petición HTTP."""
        ...

    def needs_full_buffer(self, has_active_tools: bool) -> bool:
        """True si el streaming debe bufferearse entero antes de mostrar.

        En modo XML no sabemos si el modelo va a escribir texto o un
        bloque <tool_call> hasta que termina. Bufferear evita mostrar
        el XML crudo al usuario.
        """
        ...

    def process_round(
        self,
        message: dict[str, Any],
        tool_names: set[str],
    ) -> RoundResult:
        """Interpreta la respuesta del modelo y devuelve qué hacer."""
        ...

    def format_tool_result(
        self,
        name: str,
        result_text: str,
    ) -> dict[str, Any]:
        """Cómo añadir un resultado de tool al historial."""
        ...


# ── Estrategia nativa ─────────────────────────────────────────────────

class NativeToolStrategy:
    """Tool calling nativo: usa `tools` del payload y `message.tool_calls`."""

    def prepare_system_prompt(self, active_tools: list[dict[str, Any]]) -> str:
        from .ollama import OllamaClient
        return OllamaClient._tool_system_prompt(active_tools)

    def should_send_tools_param(self) -> bool:
        return True

    def needs_full_buffer(self, has_active_tools: bool) -> bool:
        # En modo nativo, el streaming es en vivo siempre.
        return False

    def process_round(
        self,
        message: dict[str, Any],
        tool_names: set[str],
    ) -> RoundResult:
        content = str(message.get("content") or "")
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            # ¿El modelo escribió el JSON como texto en vez de usar
            # tool_calls nativos? `_stream` marca `_textual_tool_name`
            # cuando lo detecta. Se pide un reintento al modelo.
            #
            # El dialecto XML <function=NAME> ya viene convertido a
            # tool_calls por `_stream_async`, así que no se maneja aquí.
            textual_name = message.get("_textual_tool_name")
            if textual_name:
                return RoundResult(
                    retry_requested=True,
                    retry_message=(
                        f"Has escrito el JSON de la herramienta "
                        f"«{textual_name}» como texto normal. No ejecutes "
                        "herramientas así. Repite la operación usando "
                        "EXCLUSIVAMENTE la llamada nativa de tool calling "
                        "que Ollama expone en el parámetro `tools`. No "
                        "escribas JSON en el mensaje."
                    ),
                    assistant_content=content,
                )
            # Respuesta final. En modo nativo, _stream ya llamó a
            # on_text durante el streaming, así que NO marcamos
            # visible_text: chat() no debe volver a mostrarlo.
            return RoundResult(is_final=True, final_text=content)

        # Hay tool calls nativos. Parsear argumentos.
        parsed: list[tuple[str, dict[str, Any]]] = []
        for call in tool_calls:
            function = call.get("function", {})
            name = str(function.get("name", ""))
            args = function.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            if not isinstance(args, dict):
                args = {}
            if name:
                parsed.append((name, args))

        return RoundResult(
            tool_calls=parsed,
            assistant_content=content,
        )

    def format_tool_result(self, name: str, result_text: str) -> dict[str, Any]:
        # En nativo, Ollama espera role="tool" con tool_name.
        return {
            "role": "tool",
            "content": result_text,
            "tool_name": name,
        }


# ── Estrategia XML ────────────────────────────────────────────────────

class XmlToolStrategy:
    """Prompt-guided XML: describe las tools en el system prompt y parsea
    bloques <tool_call> del texto."""

    def __init__(self, active_tools: list[dict[str, Any]] | None = None):
        # Guardamos las tools activas para saber si esta estrategia debe
        # bufferear el stream (solo si hay tools para parsear).
        self._has_tools = bool(active_tools)

    def prepare_system_prompt(self, active_tools: list[dict[str, Any]]) -> str:
        from .xml_tools import build_tools_prompt
        return build_tools_prompt(active_tools)

    def should_send_tools_param(self) -> bool:
        return False

    def needs_full_buffer(self, has_active_tools: bool) -> bool:
        return has_active_tools

    def process_round(
        self,
        message: dict[str, Any],
        tool_names: set[str],
    ) -> RoundResult:
        from .xml_tools import parse_tool_calls, strip_tool_call_blocks

        content = str(message.get("content") or "")
        xml_calls = parse_tool_calls(content, known_tools=tool_names)
        visible = strip_tool_call_blocks(content)

        if xml_calls:
            return RoundResult(
                tool_calls=xml_calls,
                visible_text=visible,
                assistant_content=content,
            )

        # Sin bloques XML: respuesta final.
        return RoundResult(
            is_final=True,
            final_text=visible,
            visible_text=visible,
        )

    def format_tool_result(self, name: str, result_text: str) -> dict[str, Any]:
        # En XML, Ollama no entiende role="tool" (no enviamos tools param).
        # Usamos role="user" con prefijo inequívoco para que
        # _last_user_text ignore estos mensajes (confused deputy).
        return {
            "role": "user",
            "content": f"[TOOL_RESULT:{name}]\n{result_text}",
        }
