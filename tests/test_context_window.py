"""Tests del cálculo del presupuesto de contexto."""
from __future__ import annotations

from core.context_window import ContextBudget, ContextWindow


# -- estimación ---------------------------------------------------------------

def test_estimate_prose_uses_wide_ratio():
    w = ContextWindow(limit_tokens=8192)
    # 420 chars de prosa a 4.2 chars/tok ≈ 100 tokens.
    assert 95 <= w.estimate_tokens("a" * 420) <= 105


def test_estimate_code_uses_narrow_ratio():
    w = ContextWindow(limit_tokens=8192)
    code = "def foo():\n    return 1;\n" * 30
    est_code = w.estimate_tokens(code)
    est_prose_same_len = int(len(code) / 4.2)
    # Con señales de código, el ratio es menor → más tokens estimados.
    assert est_code > est_prose_same_len


def test_estimate_empty_text_is_zero():
    assert ContextWindow(limit_tokens=8192).estimate_tokens("") == 0


# -- presupuesto --------------------------------------------------------------

def test_prompt_budget_respects_output_reserve():
    from core.context_window import _PROMPT_BUDGET_MARGIN
    w = ContextWindow(limit_tokens=8192, output_reserve=1024)
    raw = 8192 - 1024
    expected = int(raw * _PROMPT_BUDGET_MARGIN)
    assert w.prompt_budget == expected
    assert w.output_reserve == 1024


def test_prompt_budget_proportional_for_small_limits():
    """Con límites pequeños, la reserva no debe comerse todo el prompt."""
    from core.context_window import _PROMPT_BUDGET_MARGIN
    w = ContextWindow(limit_tokens=1000, output_reserve=1024)
    # Reserva efectiva = min(1024, 1000//2) = 500.
    assert w.output_reserve == 500
    # Margen de seguridad aplicado al raw = 500.
    assert w.prompt_budget == int(500 * _PROMPT_BUDGET_MARGIN)


def test_prompt_budget_never_negative():
    w = ContextWindow(limit_tokens=100, output_reserve=100_000)
    assert w.prompt_budget >= 0


# -- fit ----------------------------------------------------------------------

def test_fit_does_nothing_when_under_budget():
    w = ContextWindow(limit_tokens=100_000)
    messages = [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "qué tal"},
    ]
    pruned, budget = w.fit(
        system_prompt="eres un asistente",
        tool_definitions=[],
        messages=messages,
    )
    assert budget.dropped_messages == 0
    assert pruned == messages


def test_fit_drops_old_turns_when_over_budget():
    w = ContextWindow(limit_tokens=2000, output_reserve=500, min_turns=2)
    messages = [
        {"role": "user", "content": "pregunta " * 100},
        {"role": "assistant", "content": "respuesta " * 100},
    ] * 10
    pruned, budget = w.fit(
        system_prompt="",
        tool_definitions=[],
        messages=messages,
    )
    assert budget.dropped_messages > 0
    assert len(pruned) < len(messages)
    user_count = sum(1 for m in pruned if m["role"] == "user")
    assert user_count >= 2


def test_fit_includes_system_prompt_in_estimation():
    """El system prompt consume presupuesto aunque no esté en messages."""
    w = ContextWindow(limit_tokens=2000, output_reserve=500, min_turns=1)
    system = "instrucciones " * 200
    _, budget = w.fit(
        system_prompt=system,
        tool_definitions=[],
        messages=[{"role": "user", "content": "hola"}],
    )
    # El system prompt por sí solo ya ocupa tokens, y la estimación
    # final debe reflejarlo.
    assert budget.estimated_prompt > 500


def test_fit_includes_tool_definitions_in_estimation():
    w = ContextWindow(limit_tokens=2000, output_reserve=500, min_turns=1)
    tools = [
        {
            "type": "function",
            "function": {
                "name": f"tool_{i}",
                "description": "descripción larga " * 30,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }
        for i in range(20)
    ]
    _, budget = w.fit(
        system_prompt="",
        tool_definitions=tools,
        messages=[{"role": "user", "content": "hola"}],
    )
    # 20 definiciones con descripciones largas pesan varios cientos de tokens.
    assert budget.estimated_prompt > 500


def test_fit_preserves_last_user_when_fixed_part_exceeds_budget():
    """Con fixed >= budget, se preserva el ultimo user truncado.

    Antes se devolvia [] y el modelo recibia un prompt sin la pregunta
    del usuario. El nuevo contrato prefiere un user truncado a un
    chat mudo.
    """
    w = ContextWindow(limit_tokens=500, output_reserve=400, min_turns=1)
    pruned, budget = w.fit(
        system_prompt="x" * 10_000,
        tool_definitions=[],
        messages=[{"role": "user", "content": "hola"}],
    )
    # El user se preserva (posiblemente truncado).
    assert pruned
    assert pruned[0]["role"] == "user"
    # Y el historial descartado son todos los mensajes menos el user.
    assert budget.dropped_messages == 0


def test_fit_does_not_split_user_assistant_pair():
    """El corte debe dejar un mensaje user al principio del historial."""
    w = ContextWindow(limit_tokens=1000, output_reserve=200, min_turns=2)
    messages = []
    for i in range(20):
        messages.append({"role": "user", "content": f"pregunta {i} " * 50})
        messages.append({"role": "assistant", "content": f"respuesta {i} " * 50})
    pruned, _ = w.fit(
        system_prompt="",
        tool_definitions=[],
        messages=messages,
    )
    if pruned:
        assert pruned[0]["role"] == "user"


def test_fit_respects_budget_over_min_turns():
    """El budget manda. Si min_turns no cabe, se poda igualmente.

    El fix del parche X cambió el contrato: antes se devolvía el
    historial completo aunque excediera el presupuesto si había menos
    de min_turns. Ahora el presupuesto es duro. La única excepción
    es preservar el último user si TODO queda fuera (ver test
    siguiente).
    """
    w = ContextWindow(limit_tokens=200, output_reserve=100, min_turns=4)
    messages = []
    for i in range(10):
        messages.append({"role": "user", "content": "x" * 100})
        messages.append({"role": "assistant", "content": "y" * 100})
    pruned, budget = w.fit(
        system_prompt="",
        tool_definitions=[],
        messages=messages,
    )
    # El resultado debe caber en el presupuesto, sin importar min_turns.
    assert budget.estimated_prompt <= budget.prompt_budget
    # Y no puede bajar de 1 mensaje: siempre preservamos algo.
    assert len(pruned) >= 1


def test_fit_preserves_last_user_when_everything_is_too_big():
    """Si ningún mensaje cabe, se preserva al menos el último user.

    Excepción consciente al presupuesto: sin mensaje user, el chat
    queda mudo y el modelo no tiene nada a lo que responder.
    """
    w = ContextWindow(limit_tokens=200, output_reserve=100, min_turns=4)
    # Budget = 100 tokens. Cada mensaje: 500 chars / 4.2 ≈ 119 tokens.
    # Ninguno cabe individualmente, y menos los dos juntos.
    messages = [
        {"role": "user", "content": "x" * 500},
        {"role": "assistant", "content": "y" * 500},
    ]
    pruned, _ = w.fit(
        system_prompt="",
        tool_definitions=[],
        messages=messages,
    )
    assert len(pruned) == 1
    assert pruned[0]["role"] == "user"

# -- fixes del parche X -----------------------------------------------------

def test_fit_does_not_double_penalize_fixed_cost():
    """El coste del system prompt no debe restarse dos veces.

    Antes: ``estimated + fixed < budget - fixed`` -> reserva el doble
    de ``fixed``. Ahora: ``estimated <= budget - fixed``.
    """
    w = ContextWindow(limit_tokens=10_000, output_reserve=1000)
    # Presupuesto real: 9000. fixed = ~100 tokens (400 chars / 4.2).
    # Los 9000-100 disponibles son 8900 para el historial.
    system = "x" * 400
    messages = [
        {"role": "user", "content": "a" * 8000},
        {"role": "assistant", "content": "b" * 8000},
    ]
    # 16000 chars / 4.2 ≈ 3810 tokens. Debe caber.
    pruned, budget = w.fit(
        system_prompt=system,
        tool_definitions=[],
        messages=messages,
    )
    assert len(pruned) == 2, (
        f"El fix del doble coste debe dejar caber el historial. "
        f"budget={budget}"
    )


def test_fit_prunes_when_min_turns_exceeds_budget():
    """Si min_turns no cabe, hay que podar igualmente.

    Antes: con pocos turnos y presupuesto pequeño, se devolvía el
    historial completo aunque excediera el límite. Ahora se poda.
    """
    w = ContextWindow(limit_tokens=2000, output_reserve=200, min_turns=8)
    # Budget = 1800 tokens. Cada mensaje: 3000 chars / 4.2 ≈ 714 tokens.
    # 4 mensajes = 2857 tokens. Cabe 1 mensaje holgado, 2 justos,
    # 3+ no caben.
    messages = [
        {"role": "user", "content": "a" * 3000},
        {"role": "assistant", "content": "b" * 3000},
        {"role": "user", "content": "c" * 3000},
        {"role": "assistant", "content": "d" * 3000},
    ]
    pruned, budget = w.fit(
        system_prompt="",
        tool_definitions=[],
        messages=messages,
    )
    # El resultado debe caber en el presupuesto, aunque min_turns no
    # se pueda respetar.
    assert budget.estimated_prompt <= budget.prompt_budget, (
        f"El historial podado excede el presupuesto: "
        f"estimated={budget.estimated_prompt}, "
        f"budget={budget.prompt_budget}"
    )
    # Y debe empezar por un user.
    assert pruned[0]["role"] == "user"


def test_fit_starts_pruned_history_with_user():
    """El primer mensaje del historial podado debe ser user."""
    w = ContextWindow(limit_tokens=3000, output_reserve=500, min_turns=2)
    messages = []
    for i in range(30):
        messages.append({"role": "user", "content": f"pregunta {i} " * 40})
        messages.append({"role": "assistant", "content": f"respuesta {i} " * 40})
    pruned, _ = w.fit(
        system_prompt="",
        tool_definitions=[],
        messages=messages,
    )
    if pruned:
        assert pruned[0]["role"] == "user", (
            f"El historial podado empieza por {pruned[0]['role']}"
        )

# -- parche AG: correcciones de la auditoría --------------------------------

def test_estimate_tokens_never_zero_for_nonempty():
    """Texto no vacío siempre consume al menos 1 token."""
    w = ContextWindow(limit_tokens=8192)
    assert w.estimate_tokens("hola") >= 1
    assert w.estimate_tokens("a") >= 1
    assert w.estimate_tokens(".") >= 1
    # Vacío sigue siendo 0.
    assert w.estimate_tokens("") == 0


def test_fit_truncates_last_user_when_it_does_not_fit():
    """El último user se trunca si no cabe, en lugar de devolverse completo."""
    w = ContextWindow(limit_tokens=200, output_reserve=100, min_turns=4)
    # Budget = 100 tokens. El mensaje tiene ~1200 tokens (5000 chars / 4.2).
    # Debe truncarse.
    huge = "linea uno\n" * 500  # 5000 chars
    messages = [{"role": "user", "content": huge}]
    pruned, budget = w.fit(
        system_prompt="",
        tool_definitions=[],
        messages=messages,
    )
    assert len(pruned) == 1
    # El mensaje truncado debe caber aproximadamente en el presupuesto.
    # El margen tolera la nota de truncado (~40 chars ≈ 10 tokens) y
    # el redondeo del estimador por ratio.
    assert budget.estimated_prompt <= budget.prompt_budget + 20
    # Y contener la marca visible.
    assert "truncado" in pruned[0]["content"].lower()


def test_fit_truncated_message_preserves_beginning():
    """El truncado conserva el inicio del mensaje (la instrucción)."""
    w = ContextWindow(limit_tokens=200, output_reserve=100, min_turns=4)
    mensaje = "PRIMERA LINEA IMPORTANTE\n" + ("relleno\n" * 500)
    pruned, _ = w.fit(
        system_prompt="",
        tool_definitions=[],
        messages=[{"role": "user", "content": mensaje}],
    )
    assert len(pruned) == 1
    assert pruned[0]["content"].startswith("PRIMERA LINEA IMPORTANTE")


def test_fit_truncates_single_line_message():
    """Un mensaje de una sola línea gigante también se trunca."""
    w = ContextWindow(limit_tokens=200, output_reserve=100, min_turns=4)
    # 5000 chars en una única línea.
    one_line = "x" * 5000
    pruned, budget = w.fit(
        system_prompt="",
        tool_definitions=[],
        messages=[{"role": "user", "content": one_line}],
    )
    assert len(pruned) == 1
    # El mensaje resultante debe ser más corto que el original.
    assert len(pruned[0]["content"]) < len(one_line)
    assert "truncado" in pruned[0]["content"].lower()

