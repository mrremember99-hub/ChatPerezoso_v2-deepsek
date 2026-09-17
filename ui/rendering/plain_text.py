"""Renderer de chat: texto, tarjetas y narración."""
from __future__ import annotations

import html

from PySide6.QtGui import QColor, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QTextEdit

from core.tool_result import ToolResult

from .. import design
from .markdown_renderer import to_html


class PlainTextRenderer:
    _HEADER_PREFIXES = ("**PEREZOSO**", "PEREZOSO:", "PEREZOSO")

    def __init__(self, chat: QTextEdit):
        self.chat = chat
        self.response_text = ""
        self.response_start = None
        self.response_segment = ""
        self.last_user_start = None
        self._segment_end = None

    # -- ciclo de vida ------------------------------------
    def reset(self) -> None:
        self.response_text = ""
        self.response_start = None
        self.response_segment = ""
        self._segment_end = None

    def reset_response_segment(self) -> None:
        if (
            self.response_start is not None
            and self.response_segment
            and self._segment_end is not None
        ):
            self._render_markdown_block()
        self.response_start = None
        self.response_segment = ""
        self._segment_end = None

    # -- helpers de formato -------------------------------
    @staticmethod
    def _fresh_block_format() -> QTextBlockFormat:
        fmt = QTextBlockFormat()
        fmt.setTopMargin(0)
        fmt.setBottomMargin(0)
        fmt.setLeftMargin(0)
        fmt.setRightMargin(0)
        return fmt

    @staticmethod
    def _fresh_char_format() -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(design.RESPONSE_TEXT_COLOR))
        fmt.setBackground(QColor(0, 0, 0, 0))
        return fmt

    def _reset_cursor_format(self, cursor: QTextCursor) -> None:
        cursor.setBlockFormat(self._fresh_block_format())
        cursor.setCharFormat(self._fresh_char_format())

    # -- mensaje de usuario -------------------------------
    def insert_user_message(self, text: str) -> None:
        cursor = self.chat.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if cursor.position() > 0 and not cursor.atBlockStart():
            cursor.insertBlock()
        self.last_user_start = cursor.position()

        viewport_width = self.chat.viewport().width()
        table_width = max(200, int(viewport_width * 0.6))

        safe_text = html.escape(text).replace(chr(10), "<br>")
        # Sin border en la tabla: el borde forzaba a QTextDocument a
        # recalcular el layout de toda la tabla en cada operación, lo
        # que ralentizaba el streaming posterior. El fondo de color ya
        # distingue visualmente la pregunta del resto.
        cursor.insertHtml(
            f'<table align="right" width="{table_width}" '
            f'cellpadding="0" cellspacing="0">'
            f'<tr><td bgcolor="{design.USER_MESSAGE_BG_COLOR}" '
            f'style="padding:{design.USER_MESSAGE_PADDING_PX}px;">'
            f'<div style="color:#7EE0A8; font-size:8pt; '
            f'font-weight:700; letter-spacing:1.2px; '
            f'margin-bottom:6px;">TÚ</div>'
            f'<div style="color:{design.USER_MESSAGE_TEXT_COLOR}; '
            f'font-size:{design.USER_MESSAGE_FONT_SIZE_PX}px;">'
            f'{safe_text}</div>'
            f'</td></tr></table>'
        )
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertBlock()
        self._reset_cursor_format(cursor)
        self.chat.setTextCursor(cursor)
        self.chat.ensureCursorVisible()

    # -- respuesta en streaming ----------------------------
    @staticmethod
    def display_response_text(text: str) -> str:
        return (
            text.replace(chr(13) + chr(10), chr(10))
            .replace(chr(13), chr(10))
            .replace("\\r\\n", chr(10))
            .replace("\\n", chr(10))
        )

    @classmethod
    def clean_response_text(cls, text: str) -> str:
        stripped = text.lstrip()
        for prefix in cls._HEADER_PREFIXES:
            if stripped.startswith(prefix):
                return stripped[len(prefix):].lstrip(" " + chr(10) + ":")
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
                cursor.insertBlock(self._fresh_block_format())
            block = self._fresh_block_format()
            block.setLeftMargin(design.ASSISTANT_INDENT_PX)
            cursor.setBlockFormat(block)
            cursor.setCharFormat(self._fresh_char_format())
            self.response_start = cursor.position()

        chunks = text.split(chr(10) + chr(10))
        for i, chunk in enumerate(chunks):
            if i > 0:
                cursor.insertBlock()
                block = self._fresh_block_format()
                block.setLeftMargin(design.ASSISTANT_INDENT_PX)
                block.setTopMargin(design.RESPONSE_PARAGRAPH_TOP_MARGIN)
                cursor.setBlockFormat(block)
                cursor.setCharFormat(self._fresh_char_format())
            first_line = True
            for line in chunk.split(chr(10)):
                if not first_line:
                    cursor.insertText(chr(10))
                if line:
                    cursor.insertText(line)
                first_line = False

        self._segment_end = cursor.position()
        self.chat.setTextCursor(cursor)
        self.chat.ensureCursorVisible()

    # -- renderizado de Markdown ---------------------------
    def _render_markdown_block(self) -> None:
        if self.response_start is None or self._segment_end is None:
            return
        text = self.response_segment
        if not text.strip():
            return

        cursor = QTextCursor(self.chat.document())
        cursor.setPosition(self.response_start)
        cursor.setPosition(self._segment_end, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()

        rendered = to_html(text)
        if rendered:
            cursor.insertHtml(
                f'<div style="margin-left:{design.ASSISTANT_INDENT_PX}px; '
                f'color:{design.RESPONSE_TEXT_COLOR};">'
                f'{rendered}'
                f'</div>'
            )
        cursor.insertBlock(self._fresh_block_format())
        cursor.setCharFormat(self._fresh_char_format())

        self.response_start = None
        self._segment_end = None

    # -- narración ----------------------------------------
    def insert_narration(self, text: str, active: bool = False) -> None:
        self.reset_response_segment()
        cursor = self.chat.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if cursor.position() > 0 and not cursor.atBlockStart():
            cursor.insertBlock(self._fresh_block_format())

        block = self._fresh_block_format()
        block.setLeftMargin(design.ASSISTANT_INDENT_PX)
        block.setTopMargin(6)
        cursor.setBlockFormat(block)
        cursor.setCharFormat(self._fresh_char_format())

        color = design.NARRATION_ACTIVE_COLOR if active else design.NARRATION_COLOR
        safe = html.escape(text)
        cursor.insertHtml(
            f'<span style="color:{color}; '
            f'font-size:{design.NARRATION_FONT_SIZE_PT}pt; '
            f'font-style:italic;">{safe}</span>'
        )
        cursor.insertBlock(self._fresh_block_format())
        cursor.setCharFormat(self._fresh_char_format())
        self.chat.setTextCursor(cursor)
        self.chat.ensureCursorVisible()

    # -- tarjeta de herramienta ----------------------------
    def insert_tool_card(self, result: ToolResult) -> None:
        self.reset_response_segment()
        cursor = self.chat.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if cursor.position() > 0 and not cursor.atBlockStart():
            cursor.insertBlock(self._fresh_block_format())

        block = self._fresh_block_format()
        block.setLeftMargin(design.ASSISTANT_INDENT_PX)
        cursor.setBlockFormat(block)
        cursor.setCharFormat(self._fresh_char_format())

        color = {
            "ok": design.TOOL_CARD_TEXT,
            "error": "#E0A0A0",
            "cancelled": "#E0BC7A",
        }[result.status]
        icon = {"ok": "●", "error": "▲", "cancelled": "■"}[result.status]
        duration = f" · {result.duration_ms} ms" if result.duration_ms else ""
        summary = html.escape(result.summary or result.status_label)

        cursor.insertHtml(
            f'<div style="border-left:3px solid {color}; '
            f'padding:2px 0 2px 10px; '
            f'color:{color}; '
            f'font-size:{design.TOOL_CARD_FONT_SIZE_PT}pt;">'
            f'<span style="color:{color};">{icon}</span> '
            f'<b>{html.escape(result.tool_name)}</b>{html.escape(duration)}'
            f'<br><span style="color:#8FA79A;">{summary}</span>'
            f'</div>'
        )

        if result.detail:
            cursor.insertBlock(self._fresh_block_format())
            block = self._fresh_block_format()
            block.setLeftMargin(design.ASSISTANT_INDENT_PX)
            cursor.setBlockFormat(block)

            lines = result.detail.split(chr(10))
            detail_lines = "".join(
                f'<div style="margin:0; padding:0; line-height:1.3;">'
                f'{html.escape(line) if line else "&nbsp;"}'
                f'</div>'
                for line in lines
            )
            cursor.insertHtml(
                f'<div style="border-left:3px solid {design.TOOL_CARD_BORDER}; '
                f'padding:6px 0 6px 10px; '
                f'color:{design.TOOL_CARD_TEXT}; '
                f'font-family:{design.TOOL_CARD_MONO}; '
                f'font-size:{design.TOOL_CARD_FONT_SIZE_PT}pt;">'
                f'{detail_lines}'
                f'</div>'
            )

        cursor.insertBlock(self._fresh_block_format())
        cursor.setCharFormat(self._fresh_char_format())
        self.chat.setTextCursor(cursor)
        self.chat.ensureCursorVisible()

    # -- errores ------------------------------------------
    def insert_error(self, message: str) -> None:
        self.insert_narration(f"Error: {message}", active=False)

    # -- cierre de respuesta -----------------------------
    def final_text(self, fallback: str) -> str:
        if not self.response_text and fallback:
            self.on_text(fallback)
        raw = self.response_text or fallback
        cleaned = self.clean_response_text(self.display_response_text(raw))

        if self.response_start is not None and self.response_segment:
            self._render_markdown_block()

        return cleaned

    # -- restauración ------------------------------------
    def restore_assistant_message(self, text: str) -> None:
        if not text:
            return
        cursor = self.chat.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if cursor.position() > 0 and not cursor.atBlockStart():
            cursor.insertBlock(self._fresh_block_format())

        block = self._fresh_block_format()
        block.setLeftMargin(design.ASSISTANT_INDENT_PX)
        cursor.setBlockFormat(block)
        cursor.setCharFormat(self._fresh_char_format())

        rendered = to_html(text)
        if rendered:
            cursor.insertHtml(
                f'<div style="color:{design.RESPONSE_TEXT_COLOR};">'
                f'{rendered}'
                f'</div>'
            )
        cursor.insertBlock(self._fresh_block_format())
        cursor.setCharFormat(self._fresh_char_format())
        self.chat.setTextCursor(cursor)
        self.chat.ensureCursorVisible()
        self.reset()

    # -- regenerar ---------------------------------------
    def remove_from_last_user(self) -> None:
        if self.last_user_start is None:
            return
        doc = self.chat.document()
        if self.last_user_start >= doc.characterCount():
            self.last_user_start = None
            return
        cursor = QTextCursor(doc)
        cursor.setPosition(self.last_user_start)
        cursor.movePosition(
            QTextCursor.MoveOperation.End,
            QTextCursor.MoveMode.KeepAnchor,
        )
        cursor.removeSelectedText()
        self.last_user_start = None
        self.response_start = None
        self.response_segment = ""
        self._segment_end = None
        self.chat.setTextCursor(cursor)
