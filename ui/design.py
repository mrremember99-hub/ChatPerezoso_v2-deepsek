"""Valores de diseño de la interfaz: colores, tipografías y espaciados que
no viven en theme.py (DARK_STYLE) porque se aplican a mano — en HTML
inyectado en el chat o en construcción de widgets — en vez de por QSS.
Cambiar el aspecto de la burbuja, los márgenes de respuesta o los colores
de los eventos de herramienta se hace aquí, en un solo sitio.
"""
from __future__ import annotations

# -- paleta base -------------------------------------------------------------

BG_APP = "#0D1211"
BG_SURFACE = "#131A19"
BG_CHAT = "#0F1514"
BG_INPUT = "#161E1D"

ACCENT = "#7EE0A8"
ACCENT_HOVER = "#99EDBD"
ACCENT_PRESSED = "#5FC48B"

TEXT_PRIMARY = "#E8EFEC"
TEXT_SECONDARY = "#94A8A0"
TEXT_MUTED = "#5E6F69"

# -- mensaje de usuario (tabla HTML alineada a la derecha) -------------------

USER_MESSAGE_BG_COLOR = "#1E2D28"
USER_MESSAGE_TEXT_COLOR = "#E8EFEC"
USER_MESSAGE_FONT_SIZE_PX = 14
USER_MESSAGE_PADDING_PX = 12

# -- respuesta en streaming (párrafo a párrafo) ------------------------------

RESPONSE_RIGHT_MARGIN_RATIO = 0.20
RESPONSE_FIRST_PARAGRAPH_TOP_MARGIN = 16.0
RESPONSE_PARAGRAPH_TOP_MARGIN = 3.0
RESPONSE_PARAGRAPH_BOTTOM_MARGIN = 3.0
RESPONSE_TEXT_COLOR = "#D5DED9"

# -- eventos de herramienta --------------------------------------------------

TOOL_EVENT_COLOR = "#7C8F87"
TOOL_EVENT_ERROR_COLOR = "#E08888"
TOOL_EVENT_CANCELLED_COLOR = "#E0BC7A"
TOOL_EVENT_FONT_SIZE_PT = 8

TOOL_RESULT_BG_COLOR = "#131B1A"
TOOL_RESULT_TEXT_COLOR = "#A8BAB3"
TOOL_RESULT_FONT_SIZE_PT = 9

# -- layout de la ventana principal ------------------------------------------

SIDEBAR_WIDTH_PX = 240
CHAT_INPUT_HEIGHT_PX = 72

# -- indicadores -------------------------------------------------------------

THINKING_TIMER_INTERVAL_MS = 420
ELAPSED_TIMER_INTERVAL_MS = 100
