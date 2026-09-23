"""Cálculo del presupuesto de contexto de una petición.

Motivación: la heurística anterior estimaba tokens solo sobre el
historial visible (``ChatController.messages``) con un ratio fijo de
4 chars/token. El benchmark mostró que en conversaciones con código
esa estimación subestima el consumo real un ~43%, lo que hacía que la
compactación se disparase tarde y el modelo pudiera recibir más tokens
de los que la app creía.

Este módulo centraliza el cálculo:
  · incluye system prompt y definiciones de herramientas,
  · usa un ratio ponderado por densidad de código,
  · reserva tokens para la respuesta del modelo.

No conoce Qt, ni Ollama, ni ChatController: recibe datos, devuelve
historial podado. El llamante decide qué hacer con el resultado.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final


# Ratio de chars por token según densidad de código.
# Justificación empírica: scripts/benchmark_context.py mide ~4.2 chars/
# token en prosa y ~2.8 en código (identificadores largos, indentación
# y símbolos tokenizan peor en BPE).
_PROSE_CHARS_PER_TOKEN: Final[float] = 4.2
_CODE_CHARS_PER_TOKEN: Final[float] = 2.8

# Umbral de "esto parece código". No es un clasificador real, solo
# señales baratas: indentación de 4 espacios, llaves y puntos y coma.
_CODE_HINT_THRESHOLD: Final[int] = 5

# Reserva por defecto para la respuesta del modelo. Si el límite es
# pequeño, la reserva efectiva se reduce proporcionalmente para no
# comerse todo el presupuesto.
_DEFAULT_OUTPUT_RESERVE: Final[int] = 1024

# Mínimo de turnos (pares user+assistant) que se conservan aunque el
# presupuesto esté agotado. Coincide con ChatController.MIN_TURNS_TO_KEEP.
_DEFAULT_MIN_TURNS: Final[int] = 8

# Margen de seguridad aplicado al prompt_budget. La estimacion de
# tokens puede desviarse un 10-20% sobre la realidad (el propio
# benchmark del proyecto da 42.9% de diferencia entre heuristicas
# en codigo). Reservar un 15% evita que un prompt ligeramente
# sobreestimado se pase del limite del modelo y Ollama lo trunque
# en silencio. El coste es conservar un poco menos de historial.
_PROMPT_BUDGET_MARGIN: Final[float] = 0.85


class RequestTokenCache:
    """Cache efímera de costes de tokens durante un `chat()`.

    Vive solo mientras dura una generación. Evita que `ContextWindow.fit()`
    vuelva a estimar los mismos mensajes en cada ronda del bucle de
    tool calling: los mensajes que no han cambiado entre rondas
    devuelven su coste cacheado.

    No se usa entre turnos porque los mensajes mutan y `id()` podría
    reciclarse. Al morir con cada `chat()`, no hay riesgo de servir un
    coste obsoleto.
    """

    def __init__(self) -> None:
        # key = id(message), value = (content_ref, tool_calls_ref, tokens)
        self._entries: dict[int, tuple[object, object, int, int]] = {}

    def get_or_compute(
        self,
        window: "ContextWindow",
        message: dict,
    ) -> int:
        key = id(message)
        content = message.get("content")
        tool_calls = message.get("tool_calls")
        # Comprobar tambien la longitud de tool_calls. Si alguien
        # hace `message["tool_calls"].append(...)`, la identidad del
        # objeto no cambia pero el coste real si. Sin este check, la
        # cache devolveria un coste obsoleto.
        tc_len = len(tool_calls) if tool_calls else 0
        entry = self._entries.get(key)
        if entry is not None:
            cached_content, cached_calls, cached_tc_len, tokens = entry
            if (
                cached_content is content
                and cached_calls is tool_calls
                and cached_tc_len == tc_len
            ):
                return tokens
        tokens = window.estimate_message_tokens(message)
        self._entries[key] = (content, tool_calls, tc_len, tokens)
        return tokens


@dataclass(frozen=True)
class ContextBudget:
    """Resultado del cálculo del presupuesto de una petición."""
    limit_tokens: int          # límite efectivo del modelo
    output_reserve: int        # reserva efectiva para la respuesta
    prompt_budget: int         # limit - reserve (lo que queda para el prompt)
    estimated_prompt: int      # estimación del prompt real
    dropped_messages: int      # mensajes eliminados por poda
    # True si fixed (system + tools) por sí solo ya excede el
    # presupuesto. En ese caso se devuelve el último user truncado
    # como excepción consciente: mejor un prompt con menos contexto
    # que un chat mudo. El llamante puede decidir avisar, reducir
    # tools, o ignorarlo.
    overflow: bool = False


class ContextWindow:
    """Calcula el presupuesto de contexto y poda el historial.

    Args:
        limit_tokens: límite efectivo del modelo (min entre el del
            modelo y el num_ctx del agente). 0 o negativo se trata
            como "desconocido" y el llamante debe usar un fallback.
        output_reserve: tokens a reservar para la respuesta. Si el
            límite es pequeño, la reserva efectiva se reduce a la
            mitad del límite para no dejar el prompt sin espacio.
        min_turns: mínimo de turnos user+assistant que se conservan
            siempre, aunque no quepan en el presupuesto.
    """

    def __init__(
        self,
        *,
        limit_tokens: int,
        output_reserve: int = _DEFAULT_OUTPUT_RESERVE,
        min_turns: int = _DEFAULT_MIN_TURNS,
        model: str = "",
    ) -> None:
        self._limit = max(0, int(limit_tokens))
        self._requested_reserve = max(0, int(output_reserve))
        self._min_turns = max(1, int(min_turns))
        self._model = model or ""

    # -- propiedades ---------------------------------------------------------

    @property
    def limit_tokens(self) -> int:
        return self._limit

    @property
    def output_reserve(self) -> int:
        """Reserva efectiva: nunca más de la mitad del límite."""
        if self._limit <= 0:
            return 0
        return min(self._requested_reserve, self._limit // 2)

    @property
    def prompt_budget(self) -> int:
        raw = max(0, self._limit - self.output_reserve)
        # Margen de seguridad contra la desviacion de la estimacion.
        # Ver _PROMPT_BUDGET_MARGIN para la justificacion.
        return int(raw * _PROMPT_BUDGET_MARGIN)

    # -- estimación ----------------------------------------------------------

    def estimate_tokens(self, text: str) -> int:
        """Estima tokens de un bloque de texto con ratio ponderado.

        Nunca devuelve 0 para texto no vacío: la división entera
        convertía "hola" (4 chars / 4.2) en 0 tokens, lo cual rompe
        cualquier suma acumulada de presupuesto.
        """
        if not text:
            return 0

        # Si el modelo tiene calibración suficiente, usarla.
        if self._model:
            from .token_calibration import has_calibration, chars_per_token
            if has_calibration(self._model):
                ratio = chars_per_token(self._model)
                return max(1, int(len(text) / ratio))

        hints = text.count("\n    ") + text.count("{") + text.count(";")
        ratio = (
            _CODE_CHARS_PER_TOKEN
            if hints > _CODE_HINT_THRESHOLD
            else _PROSE_CHARS_PER_TOKEN
        )
        return max(1, int(len(text) / ratio))

    def estimate_message_tokens(self, message: dict) -> int:
        """Estima tokens de un mensaje completo, incluidos tool_calls.

        Los argumentos de las tool calls ocupan contexto real: Ollama
        los serializa en el payload que envía al modelo. Ignorarlos
        subestima el consumo cuando el modelo encadena varias llamadas
        con argumentos grandes (por ejemplo, el `content` de un
        `escribir_archivo`).
        """
        total = 0
        content = message.get("content")
        if isinstance(content, str):
            total += self.estimate_tokens(content)
        tool_calls = message.get("tool_calls")
        if tool_calls:
            total += self.estimate_tokens(
                json.dumps(
                    tool_calls,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        return total

    def _truncate_by_lines(self, text: str, max_tokens: int) -> str:
        """Trunca un texto por lineas hasta que quepa en max_tokens.

        O(n) amortizado: se construyen prefix sums de caracteres por
        linea y se bisecciona sobre ellos, en lugar de reconstruir
        `"".join(...)` en cada iteracion.
        """
        if max_tokens <= 0:
            return ""
        lines = text.splitlines(keepends=True)
        if not lines:
            return text

        # prefix_chars[i] = longitud de las primeras i lineas.
        prefix_chars = [0]
        for line in lines:
            prefix_chars.append(prefix_chars[-1] + len(line))

        note = "\n\n[... truncado por limite de contexto]"
        # Descontar los tokens de la nota ANTES del binary search.
        # Sin esto, `candidate <= max_tokens` es cierto pero
        # `candidate + note` excede el limite. El contrato de fit()
        # promete que el resultado nunca excede el presupuesto.
        note_tokens = self.estimate_tokens(note)
        content_budget = max(0, max_tokens - note_tokens)
        if content_budget == 0:
            # Solo cabe la nota. Devolverla sola es mejor que devolver
            # el texto original sin recortar.
            return note.strip()

        # Biseccion: mayor n tal que el prefijo de n lineas quepa en
        # el presupuesto del contenido (ya sin la nota).
        # estimate_tokens es monotona no decreciente con la longitud,
        # asi que la biseccion es valida.
        lo, hi = 0, len(lines)
        best = 0
        while lo < hi:
            mid = (lo + hi + 1) // 2
            candidate = text[:prefix_chars[mid]]
            if self.estimate_tokens(candidate) <= content_budget:
                best = mid
                lo = mid
            else:
                hi = mid - 1

        if best > 0:
            return text[:prefix_chars[best]] + note

        # Ni una sola linea cabe: cortar por caracteres con ratio
        # conservador de codigo, tambien sobre content_budget.
        estimated_chars = max(1, int(content_budget * 2.8))
        return text[:estimated_chars] + note

    def estimate_request(
        self,
        *,
        system_prompt: str,
        tool_definitions: list[dict],
        messages: list[dict],
    ) -> int:
        """Estima tokens del prompt completo (system + tools + historial)."""
        total = self.estimate_tokens(system_prompt)
        for definition in tool_definitions:
            # Misma serializacion que fit() (separators compactos).
            # Antes se usaba la default con espacios, lo que daba
            # estimaciones ligeramente distintas.
            total += self.estimate_tokens(
                json.dumps(
                    definition,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        total += self._estimate_messages(messages)
        return total

    def _estimate_messages(self, messages: list[dict]) -> int:
        return sum(self.estimate_message_tokens(m) for m in messages)

    # -- poda ----------------------------------------------------------------

    def fit(
        self,
        *,
        system_prompt: str,
        tool_definitions: list[dict],
        messages: list[dict],
        cache: "RequestTokenCache | None" = None,
    ) -> tuple[list[dict], ContextBudget]:
        """Poda el historial hasta que quepa en el presupuesto.

        Devuelve ``(historial_podado, budget)``. La poda respeta dos
        reglas, en este orden:
          1. El sufijo resultante debe caber en el presupuesto. NUNCA
             se devuelve un historial que exceda el límite por respetar
             min_turns: si no cabe, se poda hasta que quepa.
          2. Si es posible sin violar (1), se conservan al menos
             min_turns turnos completos. Y el primer mensaje del
             historial es siempre un ``user``.

        El cálculo usa prefix sums: O(n) en lugar del O(n²) anterior.
        """
        fixed = self.estimate_tokens(system_prompt)
        for d in tool_definitions:
            fixed += self.estimate_tokens(
                json.dumps(d, ensure_ascii=False, separators=(",", ":"))
            )

        budget = self.prompt_budget
        if fixed >= budget:
            # El system prompt + tools ya consumen todo el
            # presupuesto. Antes devolviamos [] y el modelo se
            # quedaba sin la pregunta del usuario. Preferimos un
            # ultimo user truncado a un chat mudo: la alternativa
            # ([] silencioso) es peor para el usuario.
            user_positions = [
                i for i, m in enumerate(messages)
                if m.get("role") == "user"
            ]
            if user_positions:
                last_user = user_positions[-1]
                raw = messages[last_user].get("content", "")
                if isinstance(raw, str) and raw:
                    # Presupuesto minimo: 64 tokens. Suficiente para
                    # una pregunta corta. No mas, para no comerse el
                    # margen del output.
                    minimal = 64
                    truncated = self._truncate_by_lines(raw, minimal)
                    if truncated:
                        new_msg = dict(messages[last_user])
                        new_msg["content"] = truncated
                        return [new_msg], ContextBudget(
                            limit_tokens=self._limit,
                            output_reserve=self.output_reserve,
                            prompt_budget=budget,
                            estimated_prompt=fixed
                            + self.estimate_message_tokens(new_msg),
                            dropped_messages=len(messages) - 1,
                            overflow=True,
                        )
            return [], ContextBudget(
                limit_tokens=self._limit,
                output_reserve=self.output_reserve,
                prompt_budget=budget,
                estimated_prompt=fixed,
                dropped_messages=len(messages),
                overflow=True,
            )

        # Tokens disponibles para el historial visible.
        available = budget - fixed

        # Coste de cada mensaje. Si hay cache, se reutiliza el coste
        # de rondas anteriores (mismo objeto, mismos campos).
        if cache is not None:
            costs: list[int] = [
                cache.get_or_compute(self, m) for m in messages
            ]
        else:
            costs = [self.estimate_message_tokens(m) for m in messages]

        # Prefix sums: prefix[i] = coste acumulado de messages[:i].
        prefix: list[int] = [0]
        for c in costs:
            prefix.append(prefix[-1] + c)
        total = prefix[-1]

        # Si todo cabe, no tocamos nada.
        if total <= available:
            return list(messages), ContextBudget(
                limit_tokens=self._limit,
                output_reserve=self.output_reserve,
                prompt_budget=budget,
                estimated_prompt=fixed + total,
                dropped_messages=0,
            )

        # Hay que podar. Buscamos el menor índice cut_at tal que
        # (total - prefix[cut_at]) <= available.
        cut_at = 0
        while cut_at < len(messages) and (total - prefix[cut_at]) > available:
            cut_at += 1

        # Ajustar hacia adelante hasta el siguiente "user".
        while cut_at < len(messages) and messages[cut_at].get("role") != "user":
            cut_at += 1

        # Si es posible, retroceder hacia atrás para respetar min_turns
        # sin exceder el presupuesto.
        user_positions = [
            i for i, m in enumerate(messages) if m.get("role") == "user"
        ]
        if len(user_positions) >= self._min_turns:
            min_start = user_positions[-self._min_turns]
            if min_start < cut_at and (total - prefix[min_start]) <= available:
                cut_at = min_start

        pruned = list(messages[cut_at:])

        # Suelo: si todo el historial se ha podado y había mensajes,
        # preservar al menos el último user. Sin esto, un límite muy
        # pequeño dejaría el chat sin prompt y el modelo respondería
        # a nada. Es una excepción consciente al presupuesto: la
        # alternativa (chat mudo) es peor.
        if not pruned and messages and user_positions:
            last_user = user_positions[-1]
            raw_content = messages[last_user].get("content", "")
            if isinstance(raw_content, str):
                truncated = self._truncate_by_lines(
                    raw_content, max(0, available)
                )
                if truncated:
                    new_msg = dict(messages[last_user])
                    new_msg["content"] = truncated
                    pruned = [new_msg]
        # `estimated` se calcula desde `pruned` para reflejar el
        # contenido real (posiblemente truncado). Usar `prefix[cut_at]`
        # daba el coste del mensaje original sin truncar.
        if cache is not None:
            estimated = fixed + sum(
                cache.get_or_compute(self, m) for m in pruned
            )
        else:
            estimated = fixed + sum(
                self.estimate_message_tokens(m) for m in pruned
            )
        return pruned, ContextBudget(
            limit_tokens=self._limit,
            output_reserve=self.output_reserve,
            prompt_budget=budget,
            estimated_prompt=estimated,
            dropped_messages=len(messages) - len(pruned),
        )
