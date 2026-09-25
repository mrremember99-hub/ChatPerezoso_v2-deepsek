
"""Detección y troceo de prompts multi-fase.

Un prompt tipo OVERPAPER contiene varias secciones marcadas como
``FASE 1``, ``FASE 2``, etc. Enviado entero, el prefill del modelo
crece linealmente con el número de fases hasta ahogarse.

Este módulo detecta y trocea. Cada fase se convierte en un prompt
independiente con:
- El mismo *preamble* (reglas globales que van antes de FASE 1).
- El cuerpo de su propia fase.
- Un snapshot del workspace en el momento de envío.

Filosofía: no ejecuta nada. Solo produce la lista de prompts. El
llamante (ChatController) decide cómo enviarlos.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# Coincide con múltiples formatos:
#   "FASE 3"
#   "## Fase 3 — Título"
#   "**FASE 3:**"
#   "━━━ FASE 3 ━━━"
#   "════ FASE 3 ════"
# El prefijo es cualquier cosa no-alfabética de hasta 8 caracteres
# (marcas markdown, caracteres de caja, símbolos de separador). Lo
# importante es que la palabra FASE esté seguida de un número.
_PHASE_HEADER_RE = re.compile(
    r"^[^\w\n]{0,8}"
    r"(?:FASE|Fase|fase)\s+(\d+)"
    r"[^\n]*$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class DetectedPhases:
    """Resultado del troceo."""

    preamble: str
    """Texto antes de la primera fase (reglas globales)."""

    phases: list[str]
    """Cuerpo de cada fase, con su header incluido."""

    @property
    def count(self) -> int:
        return len(self.phases)


def detect_phases(text: str, *, min_phases: int = 2) -> DetectedPhases | None:
    """Trocea ``text`` en fases si encuentra al menos ``min_phases``.

    Devuelve ``None`` si no hay suficientes fases o si los números no
    son consecutivos desde 1 (evita falsos positivos con textos que
    contienen la palabra "fase" en prosa).
    """
    if not isinstance(text, str) or not text:
        return None

    matches = list(_PHASE_HEADER_RE.finditer(text))
    if len(matches) < min_phases:
        return None

    # Validar que los números son 1, 2, 3, ... en orden.
    numbers = [int(m.group(1)) for m in matches]
    expected = list(range(1, len(numbers) + 1))
    if numbers != expected:
        return None

    preamble = text[: matches[0].start()].rstrip()
    phases: list[str] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].rstrip()
        if body:
            phases.append(body)

    if len(phases) < min_phases:
        return None
    return DetectedPhases(preamble=preamble, phases=phases)


def build_phase_prompt(
    preamble: str,
    phase_body: str,
    workspace_snapshot: str = "",
) -> str:
    """Ensambla el prompt de una fase.

    Orden: preamble → snapshot del workspace → cuerpo de la fase.
    El snapshot va antes para que el modelo lo tenga presente al leer
    la fase.
    """
    parts: list[str] = []
    if preamble.strip():
        parts.append(preamble.strip())
    if workspace_snapshot.strip():
        parts.append(workspace_snapshot.strip())
    parts.append(phase_body.strip())
    return "\n\n---\n\n".join(parts)