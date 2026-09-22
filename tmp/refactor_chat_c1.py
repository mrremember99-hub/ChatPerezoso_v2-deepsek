#!/usr/bin/env python3
"""C-Fase 1: extraer _prepare_context + _ChatContext de chat().

Refactor mecánico. Sin cambios de comportamiento.
Si algo no cuadra, no escribe y reporta.
"""
from pathlib import Path

p = Path("core/ollama.py")
s = p.read_text()

# ── 1. Import de dataclasses ─────────────────────────────────────────
OLD_IMP = "from typing import Any, AsyncIterator, Callable"
NEW_IMP = "from dataclasses import dataclass\n" + OLD_IMP
if "from dataclasses import dataclass" not in s:
    assert OLD_IMP in s, "no encontré el import de typing"
    s = s.replace(OLD_IMP, NEW_IMP, 1)
    print("[+] import dataclasses añadido")
else:
    print("[=] import dataclasses ya estaba")

# ── 2. _ChatContext tras el logger ───────────────────────────────────
LOGGER = "logger = logging.getLogger(__name__)"
CTX_CLASS = LOGGER + '''

@dataclass
class _ChatContext:
    """Estado constante durante el bucle de rondas de chat().

    Los campos "Recibidos" los pasa chat() y no cambian. Los campos
    "Preparados" los produce `_prepare_context`. Solo `history` se
    reasigna en cada ronda (fit de contexto).
    """
    # -- Recibidos del llamante ------------------------------------
    model: str
    options: dict[str, Any] | None
    on_text: Callable[[str], None]
    on_tool: Callable[[str, dict[str, Any]], str]
    on_metrics: Callable[[dict[str, int]], None] | None
    cancel_event: threading.Event | None
    context_window: ContextWindow | None

    # -- Preparados por _prepare_context ---------------------------
    strategy: Any
    history: list[dict[str, Any]]
    authorization_text: str
    gate: ToolIntentGate
    tool_names: set[str]
    send_tools: list[dict[str, Any]] | None
    buffer_only: bool
'''
if "class _ChatContext" not in s:
    assert LOGGER in s, "no encontré el logger"
    s = s.replace(LOGGER, CTX_CLASS, 1)
    print("[+] _ChatContext añadido")
else:
    print("[=] _ChatContext ya estaba")

# ── 3. _prepare_context antes de _fit_round_history ──────────────────
MARKER = "    @staticmethod\n    def _fit_round_history("
METHOD = '''    def _prepare_context(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: Any,
        *,
        on_text: Callable[[str], None],
        on_tool: Callable[[str, dict[str, Any]], str],
        on_metrics: Callable[[dict[str, int]], None] | None,
        cancel_event: threading.Event | None,
        options: dict[str, Any] | None,
        system_prompt: str,
        context_window: ContextWindow | None,
    ) -> _ChatContext:
        """Prepara el contexto de un chat().

        Ejecuta la fase "una sola vez por chat": capabilities,
        eleccion de estrategia, filtrado de tools, inyeccion del
        system prompt. El bucle de rondas reutiliza el contexto.
        """
        if not model:
            raise OllamaError("No hay un modelo seleccionado.")

        caps = get_capabilities(self.host, model)
        logger.debug(
            "Modelo %s: tool_mode=%s (probed=%s)",
            model, caps.tool_mode, caps.probed,
        )

        strategy = self._choose_strategy(caps, tools)

        history = list(messages)
        definitions = self._extract_definitions(tools)
        authorization_text = self._last_user_text(history)
        gate = self._build_intent_gate(tools)
        active_tools = gate.tools_for_request(
            definitions, authorization_text
        )
        tool_names = {
            str(item.get("function", {}).get("name", ""))
            for item in (active_tools or [])
            if item.get("function", {}).get("name")
        }

        tool_prompt = (
            strategy.prepare_system_prompt(active_tools)
            if active_tools
            else ""
        )
        self._inject_system_prompts(history, system_prompt, tool_prompt)

        send_tools = (
            active_tools if strategy.should_send_tools_param() else None
        )
        buffer_only = strategy.needs_full_buffer(bool(active_tools))

        return _ChatContext(
            model=model,
            options=options,
            on_text=on_text,
            on_tool=on_tool,
            on_metrics=on_metrics,
            cancel_event=cancel_event,
            context_window=context_window,
            strategy=strategy,
            history=history,
            authorization_text=authorization_text,
            gate=gate,
            tool_names=tool_names,
            send_tools=send_tools,
            buffer_only=buffer_only,
        )

'''
if "def _prepare_context" not in s:
    assert MARKER in s, "no encontré _fit_round_history"
    s = s.replace(MARKER, METHOD + MARKER, 1)
    print("[+] _prepare_context añadido")
else:
    print("[=] _prepare_context ya estaba")

# ── 4. Reemplazar el bloque de preparación dentro de chat() ──────────
START = '        if not model:\n            raise OllamaError("No hay un modelo seleccionado.")\n'
END = '        buffer_only = strategy.needs_full_buffer(bool(active_tools))\n'

i = s.find(START)
j = s.find(END)
assert i != -1, "no encontré el inicio del bloque de preparación"
assert j != -1, "no encontré el fin del bloque de preparación"
j += len(END)
old_block = s[i:j]
assert "get_capabilities" in old_block, "el bloque no empieza donde esperaba"
assert "should_send_tools_param" in old_block, "el bloque no acaba donde esperaba"

NEW_BLOCK = '''        # Fase "una vez por chat": capabilities, strategy, gate,
        # system prompt, filtro de tools. Ver `_prepare_context`.
        ctx = self._prepare_context(
            model,
            messages,
            tools,
            on_text=on_text,
            on_tool=on_tool,
            on_metrics=on_metrics,
            cancel_event=cancel_event,
            options=options,
            system_prompt=system_prompt,
            context_window=context_window,
        )

        # Aliases locales. El cuerpo del bucle los usa tal cual.
        history = ctx.history
        strategy = ctx.strategy
        gate = ctx.gate
        authorization_text = ctx.authorization_text
        send_tools = ctx.send_tools
        buffer_only = ctx.buffer_only
        tool_names = ctx.tool_names
'''
s = s[:i] + NEW_BLOCK + s[j:]
print("[+] bloque de preparación reemplazado")

# ── 5. Sincronizar ctx.history tras el fit ───────────────────────────
FIT_OLD = '''            if context_window is not None:
                history = self._fit_round_history(
                    context_window,
                    history,
                    send_tools or [],
                    cache=token_cache,
                )
'''
FIT_NEW = FIT_OLD + '''                ctx.history = history
'''
if "ctx.history = history" not in s:
    assert FIT_OLD in s, "no encontré el bloque del fit"
    s = s.replace(FIT_OLD, FIT_NEW, 1)
    print("[+] ctx.history sync añadido")
else:
    print("[=] ctx.history ya sincronizado")

# ── Escribir solo si todo fue bien ───────────────────────────────────
p.write_text(s)
print("\nOK: core/ollama.py actualizado")
