"""Paleta y estilo para la máscara v2 de PEREZOSO.

Colores extraidos del SVG de diseño (viewBox 1080x1920).
Fuente: VT323 (Google Fonts, licencia SIL OFL).
"""
from __future__ import annotations

from pathlib import Path

# -- Paleta (extraida del SVG) ------------------------------------------

BG_APP = "#0E0003"          # rgb(14,0,3)   fondo app
ACCENT_BORDER = "#CF8B0B"   # rgb(207,139,11) bordes cards + titulos
ACCENT = "#FFAE0D"          # rgb(255,174,13) texto, iconos, botones
ACCENT_DIM = "#9F6809"      # rgb(159,104,9) logo "perezoso"
BOX_FILL = "#3E2305"        # rgb(62,35,5)   cajas marrones de contenido

# -- Tokens de layout (proporciones del SVG) ----------------------------

CARD_BORDER_WIDTH = 2       # 2-3 px en el SVG; 2 va bien para pantalla
CARD_RADIUS = 8             # radio de esquina aprox
SIDEBAR_WIDTH = 320
RIGHT_PANEL_WIDTH = 310
HEADER_HEIGHT = 64

# -- Fuente -------------------------------------------------------------

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
FONT_DIR = ASSETS_DIR / "fonts"
SLOTH_SVG = ASSETS_DIR / "sloth.svg"

FONT_FAMILY = "VT323"
FONT_SIZE = 15
FONT_SIZE_LARGE = 22
FONT_SIZE_SMALL = 12


# -- QSS global ---------------------------------------------------------

# Nota: el nombre de la fuente se registra en runtime con
# QFontDatabase.addApplicationFont() antes de aplicar el stylesheet.
DARK_STYLE = f"""
* {{
    font-family: '{FONT_FAMILY}', 'Menlo', monospace;
    color: {ACCENT};
}}

QMainWindow, QDialog, QWidget {{
    background-color: {BG_APP};
    color: {ACCENT};
    font-size: {FONT_SIZE}px;
}}

QWidget#Header {{
    background-color: {BG_APP};
    border-bottom: 1px solid {ACCENT_BORDER};
}}

QLabel#Logo {{
    color: {ACCENT_DIM};
    font-size: 26px;
    padding: 8px 16px;
    letter-spacing: 2px;
}}

QLabel#HeaderStatus {{
    color: {ACCENT_BORDER};
    font-size: {FONT_SIZE}px;
    padding-right: 16px;
}}

QFrame#Card {{
    background-color: {BG_APP};
    border: {CARD_BORDER_WIDTH}px solid {ACCENT_BORDER};
    border-radius: {CARD_RADIUS}px;
}}

QLabel#CardTitle {{
    color: {ACCENT_BORDER};
    font-size: {FONT_SIZE_LARGE}px;
    padding: 6px 10px 2px 10px;
    letter-spacing: 1px;
}}

QLabel#BoxContent {{
    background-color: {BOX_FILL};
    color: {ACCENT};
    padding: 10px;
    border-radius: 4px;
}}

QPushButton {{
    background-color: {BG_APP};
    color: {ACCENT};
    border: {CARD_BORDER_WIDTH}px solid {ACCENT_BORDER};
    border-radius: 14px;
    padding: 4px 14px;
    font-size: {FONT_SIZE}px;
}}
QPushButton:hover {{
    background-color: {BOX_FILL};
}}
QPushButton:pressed {{
    background-color: {ACCENT_BORDER};
    color: {BG_APP};
}}
QPushButton:disabled {{
    color: {ACCENT_DIM};
    border-color: {ACCENT_DIM};
}}

QLineEdit, QPlainTextEdit, QTextEdit {{
    background-color: {BG_APP};
    border: {CARD_BORDER_WIDTH}px solid {ACCENT_BORDER};
    border-radius: 14px;
    color: {ACCENT};
    padding: 6px 10px;
    selection-background-color: {ACCENT_BORDER};
    selection-color: {BG_APP};
}}

QRadioButton {{
    color: {ACCENT};
    spacing: 6px;
    padding: 3px 0;
}}
QRadioButton::indicator {{
    width: 14px;
    height: 14px;
    border: {CARD_BORDER_WIDTH}px solid {ACCENT};
    border-radius: 7px;
    background: transparent;
}}
QRadioButton::indicator:checked {{
    background: {ACCENT};
}}

QComboBox {{
    background-color: {BG_APP};
    color: {ACCENT};
    border: {CARD_BORDER_WIDTH}px solid {ACCENT_BORDER};
    border-radius: 14px;
    padding: 4px 10px;
    font-size: {FONT_SIZE}px;
}}

QSplitter::handle {{
    background-color: transparent;
    width: 6px;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 4px 2px;
}}
QScrollBar::handle:vertical {{
    background: {ACCENT_BORDER};
    min-height: 24px;
    border-radius: 4px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    height: 0;
    background: transparent;
}}

QStatusBar {{
    background-color: {BG_APP};
    color: {ACCENT_BORDER};
    border-top: 1px solid {ACCENT_BORDER};
}}
"""


def load_font() -> bool:
    """Registra VT323 en Qt. Devuelve True si cargo bien.

    Idempotente: si la fuente ya esta registrada, Qt lo detecta y
    devuelve un id valido sin duplicar.
    """
    from PySide6.QtGui import QFontDatabase
    font_path = FONT_DIR / "VT323-Regular.ttf"
    if not font_path.exists():
        return False
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    return font_id >= 0


# -- CSS extra: cards del sidebar (mascara v2) --------------------------

CARD_EXTRA = f"""
QFrame#Card {{
    background-color: {BG_APP};
    border: {CARD_BORDER_WIDTH}px solid {ACCENT_BORDER};
    border-radius: {CARD_RADIUS}px;
}}

QLabel#CardTitle {{
    color: {ACCENT_BORDER};
    font-size: {FONT_SIZE_LARGE}px;
    padding: 4px 8px;
    letter-spacing: 1px;
    background: transparent;
}}

QCheckBox {{
    color: {ACCENT};
    spacing: 8px;
    padding: 3px 0;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: {CARD_BORDER_WIDTH}px solid {ACCENT};
    border-radius: 8px;
    background: transparent;
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
}}
QCheckBox::indicator:disabled {{
    border-color: {ACCENT_DIM};
}}
"""

DARK_STYLE = DARK_STYLE + CARD_EXTRA


# -- Fix labels dentro de cajas (mascara v2) ----------------------------

_LABEL_FIX = f"""
QLabel#CapabilitiesBadge,
QLabel#RecommendationLabel,
QLabel#FolderLabel,
QLabel#ContextUsageBadge {{
    background: transparent;
    color: {ACCENT};
    padding: 0;
}}
QLabel#BoxContent {{
    background-color: {BOX_FILL};
    color: {ACCENT};
    border-radius: 6px;
    padding: 8px;
}}
"""

# Botones mas redondeados (diseno pill-shaped).
_PILL_FIX = """
QPushButton {
    border-radius: 18px;
    padding: 5px 18px;
}
QComboBox {
    border-radius: 18px;
    padding: 4px 12px;
}
QLineEdit {
    border-radius: 18px;
    padding: 6px 14px;
}
"""

DARK_STYLE = DARK_STYLE + _LABEL_FIX + _PILL_FIX


# -- Document stylesheet v2 (Markdown dentro del chat) ------------------

DOCUMENT_STYLESHEET_V2 = f"""
body {{
    background-color: transparent;
    color: {ACCENT};
}}
h1, h2, h3, h4, h5, h6 {{
    color: {ACCENT};
    margin: 12px 0 6px 0;
}}
h1 {{ font-size: 1.4em; }}
h2 {{ font-size: 1.2em; }}
a {{ color: {ACCENT_BORDER}; text-decoration: underline; }}
strong {{ font-weight: bold; }}
code {{
    font-family: 'Menlo', 'Menlo Regular', monospace;
    font-size: 9pt;
    color: {ACCENT_BORDER};
}}
pre {{
    font-family: 'Menlo', 'Menlo Regular', monospace;
    font-size: 9pt;
    color: {ACCENT_BORDER};
    background-color: #1A0800;
    padding: 8px 10px;
    border-radius: 6px;
}}
blockquote {{
    border-left: 3px solid {ACCENT_BORDER};
    padding-left: 10px;
    color: {ACCENT_DIM};
    margin: 6px 0;
}}
ul, ol {{ margin: 4px 0; padding-left: 20px; }}
li {{ margin: 2px 0; }}
hr {{ border: none; border-top: 1px solid {ACCENT_BORDER}; margin: 10px 0; }}
table {{ border-collapse: collapse; margin: 8px 0; }}
th {{
    border: 1px solid {ACCENT_BORDER};
    padding: 4px 8px;
    background-color: {BOX_FILL};
    color: {ACCENT};
    font-weight: bold;
}}
td {{ border: 1px solid {BOX_FILL}; padding: 4px 8px; }}
"""


# -- Reglas especificas del chat (override del QSS global) --------------

_CHAT_FIX = f"""
QTextEdit#ChatView, QTextBrowser#ChatView {{
    background-color: {BG_APP};
    color: {ACCENT};
    border: {CARD_BORDER_WIDTH}px solid {ACCENT_BORDER};
    border-radius: 12px;
    padding: 12px;
}}
QTextEdit#ChatInput, QPlainTextEdit#ChatInput {{
    background-color: {BG_APP};
    color: {ACCENT};
    border: {CARD_BORDER_WIDTH}px solid {ACCENT_BORDER};
    border-radius: 20px;
    padding: 8px 14px;
}}
QPushButton#PrimaryButton {{
    background-color: {BG_APP};
    color: {ACCENT};
    border: {CARD_BORDER_WIDTH}px solid {ACCENT_BORDER};
    border-radius: 18px;
    padding: 6px 22px;
    font-size: {FONT_SIZE_LARGE}px;
    letter-spacing: 1px;
    min-width: 200px;
}}
QPushButton#PrimaryButton:hover {{
    background-color: {BOX_FILL};
}}
"""

DARK_STYLE = DARK_STYLE + _CHAT_FIX
