"""Paletas de color para PlainTextRenderer (v1 y v2).

El renderer emite HTML inline (tablas de mensajes de usuario,
tarjetas de herramientas, narracion). Ese HTML gana a cualquier
stylesheet de documento; por eso la mascara v2 no puede repintar
el chat solo con QSS. Esta paleta inyecta los colores.

`V1_PALETTE` reproduce exactamente los colores originales del
renderer v1: los que salian de `ui.design` + los literales que
estaban hardcodeados en `plain_text.py`. Cualquier instanciacion
sin `palette=` explicito usa esta.
"""
from __future__ import annotations

from dataclasses import dataclass

from .. import design


@dataclass(frozen=True)
class RendererPalette:
    response_text: str
    user_message_bg: str
    user_message_text: str
    user_label: str
    narration: str
    narration_active: str
    tool_card_text: str
    tool_card_border: str
    tool_card_error: str
    tool_card_cancelled: str
    tool_card_detail: str


V1_PALETTE = RendererPalette(
    response_text=design.RESPONSE_TEXT_COLOR,
    user_message_bg=design.USER_MESSAGE_BG_COLOR,
    user_message_text=design.USER_MESSAGE_TEXT_COLOR,
    user_label="#7EE0A8",
    narration=design.NARRATION_COLOR,
    narration_active=design.NARRATION_ACTIVE_COLOR,
    tool_card_text=design.TOOL_CARD_TEXT,
    tool_card_border=design.TOOL_CARD_BORDER,
    tool_card_error="#E0A0A0",
    tool_card_cancelled="#E0BC7A",
    tool_card_detail="#8FA79A",
)


# Paleta v2: literales equivalentes a ui.theme_v2.
# No importamos theme_v2 para no acoplar la capa de rendering a un
# theme concreto. El test test_v2_palette_uses_theme_v2 verifica
# que estos valores siguen coincidiendo con theme_v2.*.
V2_PALETTE = RendererPalette(
    response_text="#FFAE0D",      # == theme_v2.ACCENT
    user_message_bg="#3E2305",    # == theme_v2.BOX_FILL
    user_message_text="#FFAE0D",  # == theme_v2.ACCENT
    user_label="#CF8B0B",         # == theme_v2.ACCENT_BORDER
    narration="#9F6809",          # == theme_v2.ACCENT_DIM
    narration_active="#CF8B0B",   # == theme_v2.ACCENT_BORDER
    tool_card_text="#FFAE0D",     # == theme_v2.ACCENT
    tool_card_border="#CF8B0B",   # == theme_v2.ACCENT_BORDER
    tool_card_error="#E0A0A0",    # semantico, igual que v1
    tool_card_cancelled="#E0BC7A",# semantico, igual que v1
    tool_card_detail="#9F6809",   # == theme_v2.ACCENT_DIM
)
