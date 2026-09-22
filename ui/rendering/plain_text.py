"""Renderer de chat: texto, tarjetas y narración."""
from __future__ import annotations

import html

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QTextEdit

from core.tool_result import ToolResult

from .. import design
from .markdown_renderer import to_html


# Frecuencia con la que volcamos el texto acumulado al QTextDocument.
# 32 ms ~= 30 fps. Suficiente para percibir streaming fluido sin
# saturar el layout de QTextDocument con cientos de operaciones por
# segundo. Ajustable si se ve lento o a tirones.
_RENDER_INTERVAL_MS = 32


class PlainTextRenderer:
    _HEADER_PREFIXES = ("**PEREZOSO**", "PEREZOSO:", "PEREZOSO")

    def __init__(self, chat: QTextEdit):
        self.chat = chat
        # Los textos se acumulan como listas de fragmentos. La
        # concatenación con += sobre str es O(n²) para respuestas
        # largas; append sobre list y "".join() en el punto de consumo
        # es O(n) en total.
        self._response_parts: list[str] = []
        self._segment_parts: list[str] = []
        self._response_start: int | None = None
        self._segment_end: int | None = None
        self.last_user_start: int | None = None
        # Caché del número de caracteres del segmento actual. Permite
        # aplicar la limpieza de prefijo solo al principio (<= 100
        # chars), como el código anterior hacía con len(response_segment).
        self._segment_chars: int = 0
        # Estado del fence ``` durante streaming. Permite aplicar
        # formato monoespaciado al código mientras llega, sin
        # esperar al cierre del bloque para Pygments.
        self._in_code_fence: bool = False
        self._code_fence_lang: str = ""
        # Estado del bloque "cola de prompts". Se rellena al encolar
        # y se actualiza in-place cada vez que un prompt avanza.

    # -- compatibilidad con el protocolo ChatRenderer ---------------------
    # El protocolo declara response_text y response_start como
    # propiedades. Se exponen como @property que hacen join perezoso
    # de las listas subyacentes.

    @property
    def response_text(self) -> str:
        return "".join(self._response_parts)

    @property
    def response_segment(self) -> str:
        return "".join(self._segment_parts)

    @property
    def response_start(self) -> int | None:
        return self._response_start

    # -- ciclo de vida ------------------------------------
    def reset(self) -> None:
        self._response_parts.clear()
        self._segment_parts.clear()
        self._response_start = None
        self._segment_end = None
        self._segment_chars = 0
        self._in_code_fence = False
        self._code_fence_lang = ""
        # No limpiamos el todo list aquí: queremos que sobreviva a
        # resets entre turnos de la cola. Solo se limpia al iniciar

    def reset_response_segment(self) -> None:
        if (
            self._response_start is not None
            and self._segment_parts
            and self._segment_end is not None
        ):
            self._render_markdown_block()
        self._response_start = None
        self._segment_parts.clear()
        self._segment_end = None
        self._segment_chars = 0

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

    # Umbral mínimo de caracteres acumulados para trocear durante el
    # streaming. Por debajo de este umbral, esperamos al siguiente
    # boundary (párrafo o cierre de fence) para no fragmentar en
    # exceso respuestas cortas.
    _MIN_CHUNK_CHARS = 300

    def on_text(self, text: str) -> None:
        """Acumula el delta y programa un volcado al documento.

        Escribe directamente (el controller ya coalesce).
        El usuario ve streaming fluido, pero el QTextDocument solo
        recibe ~30 actualizaciones/segundo en lugar de una por chunk.

        Si el delta cruza una frontera de párrafo o cierra un bloque
        de código Y el segmento ya tiene suficiente contenido, se
        renderiza a Markdown/Html ahora, en vez de acumular todo el
        Markdown y convertirlo de una sola pasada en final_text.
        """
        text = self.display_response_text(text)
        if not text:
            return

        self._response_parts.append(text)
        self._segment_parts.append(text)
        self._segment_chars += len(text)

        # Limpiar el prefijo del asistente (encabezados tipo
        # "**PEREZOSO**") solo mientras el segmento es corto. Una vez
        # supera ~100 chars, ya no puede empezar con esos prefijos, así
        # que dejamos de comprobar.
        if self._segment_chars <= 100:
            joined = "".join(self._segment_parts)
            cleaned = self.clean_response_text(joined)
            if cleaned != joined:
                self._segment_parts = [cleaned]
                self._segment_chars = len(cleaned)

        # Recordar el estado del fence ANTES del append para saber
        # si este delta lo acaba de cerrar.
        was_in_fence = self._in_code_fence
        self._append_plain_text(text)

        # Decidir si toca trocear ahora.
        if self._in_code_fence:
            return  # dentro de un bloque de código: no trocear
        if self._segment_chars < self._MIN_CHUNK_CHARS:
            return  # segmento aún pequeño: esperar al siguiente boundary

        crossed_paragraph = "\n\n" in text
        just_closed_fence = was_in_fence and not self._in_code_fence
        if crossed_paragraph or just_closed_fence:
            self.reset_response_segment()


    def _append_plain_text(self, text: str) -> None:
        """Aplica el texto acumulado al documento. Antiguo on_text."""
        cursor = self.chat.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        if self.response_start is None:
            if cursor.position() > 0 and not cursor.atBlockStart():
                cursor.insertBlock(self._fresh_block_format())
            block = self._fresh_block_format()
            block.setLeftMargin(design.ASSISTANT_INDENT_PX)
            cursor.setBlockFormat(block)
            cursor.setCharFormat(self._fresh_char_format())
            self._response_start = cursor.position()

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
                # Detectar transiciones de fence ```.
                is_fence = line.lstrip().startswith("```")
                if is_fence:
                    self._in_code_fence = not self._in_code_fence
                    if self._in_code_fence:
                        self._code_fence_lang = line.lstrip()[3:].strip()
                    else:
                        self._code_fence_lang = ""
                if line:
                    if self._in_code_fence or is_fence:
                        # Aplicar formato monoespaciado para código.
                        code_fmt = QTextCharFormat()
                        code_fmt.setFontFamilies(
                            ["Menlo", "Courier New", "monospace"]
                        )
                        code_fmt.setForeground(QColor(design.TOOL_CARD_TEXT))
                        cursor.insertText(line, code_fmt)
                    else:
                        cursor.insertText(line)
                first_line = False

        self._segment_end = cursor.position()
        self.chat.setTextCursor(cursor)
        if self.chat.isVisible():
            sb = self.chat.verticalScrollBar()
            at_bottom = sb.value() >= sb.maximum() - 8
            if at_bottom:
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

        self._response_start = None
        self._segment_end = None




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
        # Sin buffer interno que forzar: el llamante ya coalesce.

        if not self._response_parts and fallback:
            self.on_text(fallback)

        raw = self.response_text or fallback
        cleaned = self.clean_response_text(self.display_response_text(raw))

        if self._response_start is not None and self._segment_parts:
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
        self._response_start = None
        self._segment_parts.clear()
        self._segment_end = None
        self._segment_chars = 0
        self.chat.setTextCursor(cursor)
