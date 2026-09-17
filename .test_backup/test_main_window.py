"""Pruebas de la capa de vista.

``MainWindow`` es un contenedor sin lógica. Los tests se centran en
``ChatPanel`` y en ``PlainTextRenderer``, que es donde vive el renderizado.
"""
import pytest

pytest.importorskip("PySide6")

from ui.views.chat_panel import ChatPanel


def test_user_message_renders_as_bubble(qapp):
    panel = ChatPanel()
    try:
        panel.renderer.insert_user_message("Pregunta de prueba")
        assert "Pregunta de prueba" in panel.chat.toPlainText()
    finally:
        panel.close()
        qapp.processEvents()


def test_assistant_response_uses_configured_right_margin(qapp):
    from ui import design

    panel = ChatPanel()
    try:
        panel.resize(1000, 700)
        panel.chat.resize(760, 500)
        panel.renderer.on_text("Respuesta de prueba")

        cursor = panel.chat.textCursor()
        cursor.setPosition(panel.renderer.response_start or 0)
        block_format = cursor.blockFormat()

        assert block_format.rightMargin() > 0
        expected = panel.chat.viewport().width() * design.RESPONSE_RIGHT_MARGIN_RATIO
        assert abs(block_format.rightMargin() - expected) < 1.0
    finally:
        panel.close()
        qapp.processEvents()


def test_final_text_inserts_fallback_when_no_streaming(qapp):
    panel = ChatPanel()
    try:
        text = panel.renderer.final_text("respuesta sin streaming")
        assert "respuesta sin streaming" in text
        assert "respuesta sin streaming" in panel.chat.toPlainText()
    finally:
        panel.close()
        qapp.processEvents()


def test_final_text_strips_perezoso_header(qapp):
    panel = ChatPanel()
    try:
        panel.renderer.on_text("**PEREZOSO**\n\nContenido real")
        text = panel.renderer.final_text("")
        assert "PEREZOSO" not in text
        assert "Contenido real" in text
    finally:
        panel.close()
        qapp.processEvents()


def test_error_insertion_does_not_stick_to_next_text(qapp):
    panel = ChatPanel()
    try:
        panel.renderer.insert_error("algo falló")
        panel.renderer.insert_user_message("siguiente mensaje")

        doc = panel.chat.document()
        block = doc.begin()
        while block.isValid():
            text = block.text()
            if "siguiente mensaje" in text:
                fragment = block.begin()
                while not fragment.atEnd():
                    frag = fragment.fragment()
                    if frag.isValid() and "siguiente" in frag.text():
                        color = frag.charFormat().foreground().color()
                        assert color.name() != "#e08888", (
                            f"formato rojo heredado en: {frag.text()!r}"
                        )
                        return
                    fragment += 1
            block = block.next()
    finally:
        panel.close()
        qapp.processEvents()

def test_restore_conversation_renders_messages(qapp):
    panel = ChatPanel()
    try:
        panel.restore_conversation([
            {"role": "user", "content": "pregunta guardada"},
            {"role": "assistant", "content": "respuesta guardada"},
        ])
        text = panel.chat.toPlainText()
        assert "pregunta guardada" in text
        assert "respuesta guardada" in text
    finally:
        panel.close()
        qapp.processEvents()


def test_restore_conversation_ignores_invalid_entries(qapp):
    panel = ChatPanel()
    try:
        panel.restore_conversation([
            {"role": "tool", "content": "no renderizar"},
            {"role": "user", "content": ""},
            {"role": "user"},  # sin content
        ])
        text = panel.chat.toPlainText()
        assert "no renderizar" not in text
    finally:
        panel.close()
        qapp.processEvents()
