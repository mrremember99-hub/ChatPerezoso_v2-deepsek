"""Resumen rolling de la sesion (Hueco 2).

Objetivo: cuando la conversacion crece, mantener un bloque
estructurado que resuma lo esencial (progreso, archivos,
pendiente, preferencias) para inyectar al system prompt del
siguiente turno.

Modulo puro: no importa Ollama ni Qt. El llamante decide
cuando llamar al modelo y con que prompt.
"""
from __future__ import annotations

from dataclasses import dataclass


SUMMARY_HEADER = "[RESUMEN DE LA SESIÓN]"
SUMMARY_MAX_CHARS = 2000
SUMMARY_SECTIONS: tuple[str, ...] = (
    "Progreso",
    "Archivos",
    "Pendiente",
    "Contexto",
)


@dataclass
class SessionSummary:
    """Estado del resumen rolling de la sesion."""

    text: str = ""
    last_message_count: int = 0
    cycles: int = 0

    def reset(self) -> None:
        self.text = ""
        self.last_message_count = 0
        self.cycles = 0

    def should_update(
        self,
        history_len: int,
        *,
        min_new_messages: int = 20,
        max_cycles: int = 2,
    ) -> bool:
        if self.cycles >= max_cycles:
            return False
        new_since = history_len - self.last_message_count
        return new_since >= min_new_messages

    def apply(self, new_text: str, history_len: int) -> None:
        self.text = new_text
        self.last_message_count = history_len
        self.cycles += 1


def build_summary_prompt(
    messages: list[dict],
    *,
    keep_recent: int = 6,
) -> str:
    """Construye el prompt para el modelo que hara el resumen."""
    if not messages:
        return ""
    slice_end = max(0, len(messages) - max(0, keep_recent))
    older = messages[:slice_end]
    transcript_parts: list[str] = []
    for m in older:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        label = "Usuario" if role == "user" else "Asistente"
        transcript_parts.append(f"{label}: {content.strip()}")
    if not transcript_parts:
        return ""
    transcript = "\n\n".join(transcript_parts)
    sections = "\n".join(f"· {name}: ..." for name in SUMMARY_SECTIONS)
    reglas = (
        "Reglas:\n"
        "- Cada seccion en UNA linea (o vacia si no aplica).\n"
        "- 'Archivos' lista rutas concretas si se han tocado.\n"
        "- 'Pendiente' describe que queda por hacer.\n"
        "- 'Contexto' son preferencias del usuario relevantes.\n"
        "- Se conciso: maximo ~150 palabras en total.\n"
    )
    intro = (
        "Vas a resumir una conversacion tecnica entre un usuario "
        "y un asistente de codigo. Devuelve SOLO el resumen en el "
        "siguiente formato exacto, sin texto antes ni despues:\n\n"
    )
    return (
        intro
        + SUMMARY_HEADER + "\n"
        + sections + "\n\n"
        + reglas
        + "\n--- CONVERSACION A RESUMIR ---\n"
        + transcript
        + "\n--- FIN DE LA CONVERSACION ---"
    )


def format_summary_block(raw: str | None) -> str:
    """Normaliza la respuesta del modelo pequeno."""
    if not raw:
        return ""
    text = raw.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl >= 0:
            text = text[first_nl + 1:]
        if text.endswith("```"):
            text = text[:-3].rstrip()
    if not text.startswith(SUMMARY_HEADER):
        text = SUMMARY_HEADER + "\n" + text
    if len(text) > SUMMARY_MAX_CHARS:
        lines = text.splitlines()
        kept: list[str] = []
        total = 0
        for line in lines:
            if total + len(line) + 1 > SUMMARY_MAX_CHARS - 30:
                break
            kept.append(line)
            total += len(line) + 1
        kept.append("... (resumen truncado)")
        text = "\n".join(kept)
    return text
