"""Valores de diseño de la interfaz."""
from __future__ import annotations

# -- paleta base ------------------------------------------------------------
BG_APP = "#0A0F0E"
BG_SURFACE = "#0F1614"
BG_CHAT = "#0C1211"
BG_INPUT = "#111A18"
BG_CARD = "#101917"
BG_CARD_HEADER = "#141E1C"

BORDER_SUBTLE = "#1A2523"
BORDER_STRONG = "#26332F"

ACCENT = "#7EE0A8"
ACCENT_HOVER = "#99EDBD"
ACCENT_PRESSED = "#5FC48B"
ACCENT_DIM = "#3A6B52"

TEXT_PRIMARY = "#EAF1EE"
TEXT_SECONDARY = "#9BAFA7"
TEXT_MUTED = "#5D6E68"

# -- mensaje de usuario -----------------------------------------------------
USER_MESSAGE_BG_COLOR = "#1E3A30"
USER_MESSAGE_TEXT_COLOR = "#EAF1EE"
USER_MESSAGE_FONT_SIZE_PX = 14
USER_MESSAGE_PADDING_PX = 16
USER_MESSAGE_BORDER_COLOR = "#3A6B52"

# -- respuesta en streaming -------------------------------------------------
RESPONSE_FIRST_PARAGRAPH_TOP_MARGIN = 0.0
RESPONSE_PARAGRAPH_TOP_MARGIN = 10.0
RESPONSE_PARAGRAPH_BOTTOM_MARGIN = 0.0
RESPONSE_TEXT_COLOR = "#DDE5E1"
ASSISTANT_MARKER_COLOR = "#7EE0A8"
ASSISTANT_INDENT_PX = 18

# -- narración del proceso --------------------------------------------------
NARRATION_COLOR = "#6E8079"
NARRATION_ACTIVE_COLOR = "#7EE0A8"
NARRATION_FONT_SIZE_PT = 9

# -- tarjetas de herramienta ------------------------------------------------
TOOL_CARD_BG = "#101917"
TOOL_CARD_HEADER_BG = "#141E1C"
TOOL_CARD_BORDER = "#1F2B28"
TOOL_CARD_TEXT = "#A8BAB3"
TOOL_CARD_MONO = "Menlo"
TOOL_CARD_FONT_SIZE_PT = 9

# -- layout -----------------------------------------------------------------
SIDEBAR_WIDTH_PX = 260
CHAT_INPUT_HEIGHT_PX = 76

# -- indicadores ------------------------------------------------------------
THINKING_TIMER_INTERVAL_MS = 420
ELAPSED_TIMER_INTERVAL_MS = 100

# -- compatibilidad con código/tests anteriores ------------------------------
# Estas constantes existían antes del rediseño. Se mantienen para no romper
# tests antiguos. En código nuevo, usar los TOOL_CARD_* y ToolResult.status.
TOOL_EVENT_COLOR = "#7C8F87"
TOOL_EVENT_ERROR_COLOR = "#E0A0A0"
TOOL_EVENT_CANCELLED_COLOR = "#E0BC7A"
TOOL_EVENT_FONT_SIZE_PT = 8
TOOL_RESULT_BG_COLOR = "#131B1A"
TOOL_RESULT_TEXT_COLOR = "#A8BAB3"
TOOL_RESULT_FONT_SIZE_PT = 9
