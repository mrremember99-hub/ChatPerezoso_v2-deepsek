"""Renderer por defecto: texto plano con párrafos, burbujas HTML para el
usuario, cajas para resultados de herramienta y errores en rojo.
"""
from __future__ import annotations

import html

from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QTextEdit

from .. import design


class PlainTextRenderer:
    _HEADER_PREFIXES = ("**PEREZOSO**", "PEREZOSO:", "PEREZOSO")

    def __init__(self, chat: QTextEdit):
        self.chat = chat
        self.response_text = ""
        self.response_start: int | None = None
        self.response_segment = ""
        # Posición del documento donde empieza el último mensaje de
        # usuario. Se usa para regenerar.
        self.last_user_start: int | None = None

    # -- ciclo de vida -------------------------------------------------------

    def reset(self) -> None:
        self.response_text = ""
        self.response_start = None
        self.response_segment = ""

    def reset_response_segment(self) -> None:
        self.response_start = None
        self.response_segment = ""

    # -- mensajes de usuario -------------------------------------------------

    def insert_user_message(self, text: str) -> None:
        cursor = self.chat.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if cursor.position() > 0 and not cursor.atBlockStart():
            cursor.insertBlock()
        self.last_user_start = cursor.position()
        safe_text = html.escape(text).replace("\n", "<br>")
        cursor.insertHtml(
            f'<table align="right" '
            f'cellpadding="{design.USER_MESSAGE_PADDING_PX}" '
            f'cellspacing="0" '
            f'bgcolor="{design.USER_MESSAGE_BG_COLOR}">'
            f'<tr><td style="color:{design.USER_MESSAGE_TEXT_COLOR}; '
            f'font-size:{design.USER_MESSAGE_FONT_SIZE_PX}px;">'
            f'{safe_text}'
            f'</td></tr></table>'
        )
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertBlock()
        self.chat.setTextCursor(cursor)
        self.chat.ensureCursorVisible()

    # -- respuesta en streaming ----------------------------------------------

    @staticmethod
    def display_response_text(text: str) -> str:
        return (
            text.replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\\r\\n", "\n")
            .replace("\\n", "\n")
        )

    @classmethod
    def clean_response_text(cls, text: str) -> str:
        stripped = text.lstrip()
        for prefix in cls._HEADER_PREFIXES:
            if stripped.startswith(prefix):
                return stripped[len(prefix):].lstrip(" \n:")
        return stripped

    def on_text(self, text: str) -> None:
        text = self.display_response_text(text)
        if not text:
            return
        self.response_text += text
        self.response_segment += text
        self.response_segment = self.clean_response_text(self.response_segment)

        cursor = self.chat.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if self.response_start is None:
            if cursor.position() > 0 and not cursor.atBlockStart():
                cursor.insertBlock()
            self.response_start = cursor.position()

        replace_cursor = QTextCursor(self.chat.document())
        replace_cursor.setPosition(self.response_start)
        replace_cursor.movePosition(
            QTextCursor.MoveOperation.End,
            QTextCursor.MoveMode.KeepAnchor,
        )
        replace_cursor.removeSelectedText()
        replace_cursor.setPosition(self.response_start)

        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Weight.Normal)
        fmt.setForeground(QColor(design.RESPONSE_TEXT_COLOR))
        replace_cursor.setCharFormat(fmt)

        paragraphs = self.response_segment.split("\n")
        right_margin = max(
            0.0,
            self.chat.viewport().width() * design.RESPONSE_RIGHT_MARGIN_RATIO,
        )
        for index, paragraph in enumerate(paragraphs):
            if index:
                replace_cursor.insertBlock()
            replace_cursor.insertText(paragraph)
            block = replace_cursor.blockFormat()
            block.setTopMargin(
                design.RESPONSE_FIRST_PARAGRAPH_TOP_MARGIN
                if index == 0
                else design.RESPONSE_PARAGRAPH_TOP_MARGIN
            )
            block.setBottomMargin(design.RESPONSE_PARAGRAPH_BOTTOM_MARGIN)
            block.setRightMargin(right_margin)
            replace_cursor.setBlockFormat(block)

        self.chat.setTextCursor(replace_cursor)
        self.chat.ensureCursorVisible()

    # -- eventos de herramienta ---------------------------------------------

    def insert_tool_event(self, text: str, color: str) -> None:
        self.reset_response_segment()
        cursor = self.chat.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if cursor.position() > 0 and not cursor.atBlockStart():
            cursor.insertBlock()
        cursor.insertHtml(
            f'<div style="margin:12px 0 4px 0; color:{color}; '
            f'font-size:{design.TOOL_EVENT_FONT_SIZE_PT}pt; '
            f'letter-spacing:0.4px;">'
            f'{html.escape(text)}</div>'
        )
        cursor.insertBlock()
        self.chat.setTextCursor(cursor)
        self.chat.ensureCursorVisible()

    def insert_tool_result(self, text: str) -> None:
        cursor = self.chat.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        safe_text = html.escape(text).replace("\n", "<br>")
        cursor.insertHtml(
            f'<div style="margin:2px 0 14px 18px; padding:10px 12px; '
            f'background-color:{design.TOOL_RESULT_BG_COLOR}; '
            f'color:{design.TOOL_RESULT_TEXT_COLOR}; '
            f'font-family:monospace; '
            f'font-size:{design.TOOL_RESULT_FONT_SIZE_PT}pt;">'
            f'{safe_text}'
            f'</div>'
        )
        cursor.insertBlock()
        self.chat.setTextCursor(cursor)
        self.chat.ensureCursorVisible()

    # -- errores -------------------------------------------------------------

    def insert_error(self, message: str) -> None:
        cursor = self.chat.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(design.TOOL_EVENT_ERROR_COLOR))
        cursor.setCharFormat(fmt)
        cursor.insertText(f"\n\nError: {message}\n")
        cursor.setCharFormat(QTextCharFormat())
        self.chat.setTextCursor(cursor)

    # -- cierre de respuesta -------------------------------------------------

    def final_text(self, fallback: str) -> str:
        """Devuelve el texto final de la respuesta, ya normalizado.

        Si no hubo streaming (``response_text`` vacío), inserta ``fallback``
        en el chat para que el usuario lo vea. La normalización se aplica
        siempre al texto devuelto, porque ``response_text`` se acumuló con
        ``display_response_text`` pero sin el ``clean_response_text`` final
        (que solo se aplica al segmento visible).
        """
        if not self.response_text and fallback:
            self.on_text(fallback)
        raw = self.response_text or fallback
        return self.clean_response_text(self.display_response_text(raw))

    # -- restauración -------------------------------------------------------

    def restore_assistant_message(self, text: str) -> None:
        """Inserta una respuesta completa sin simular streaming.

        Se usa al cargar una conversación guardada. El flujo es:
        ``on_text(full_text)`` acumula el texto y lo pinta, y después un
        ``reset()`` limpia el estado para que la próxima respuesta empiece
        limpia. El texto queda en el chat como si hubiera terminado ahí.
        """
        if not text:
            return
        self.on_text(text)
        self.reset()

    # -- regenerar ----------------------------------------------------------

    def remove_from_last_user(self) -> None:
        """Borra desde el último mensaje del usuario hasta el final.

        Si no hay ``last_user_start`` registrado, no hace nada. Después
        de la eliminación se limpia el estado del segmento para que la
        próxima respuesta empiece en blanco.
        """
        if self.last_user_start is None:
            return
        cursor = QTextCursor(self.chat.document())
        cursor.setPosition(self.last_user_start)
        cursor.movePosition(
            QTextCursor.MoveOperation.End,
            QTextCursor.MoveMode.KeepAnchor,
        )
        cursor.removeSelectedText()
        self.last_user_start = None
        self.response_start = None
        self.response_segment = ""
        self.chat.setTextCursor(cursor)
