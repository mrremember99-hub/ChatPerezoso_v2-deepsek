"""Diálogos como funciones puras: reciben datos, devuelven datos.

El controlador los llama cuando toca; no sabe cómo se ven por dentro.
"""
from __future__ import annotations

import html
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    # Solo para Pylance: las anotaciones string "Agent" en la firma de
    # edit_agent necesitan ver el símbolo a nivel de módulo. En runtime
    # se importa dentro de la función (import diferido intencional).
    from core.agents import Agent


def confirm_tool(parent: QWidget | None, name: str, arguments: dict) -> bool:
    """Decide si el usuario aprueba una operación."""
    if name == "ejecutar_comando":
        return _confirm_shell(parent, arguments)
    return _confirm_generic(parent, name, arguments)


# -- shell -------------------------------------------------------------------

def _confirm_shell(parent: QWidget | None, arguments: dict) -> bool:
    from plugins.shell import analyze_risk

    command = str(arguments.get("command", ""))
    cwd = str(arguments.get("cwd", "."))
    timeout = arguments.get("timeout_seconds", 30)
    risks = analyze_risk(command)

    dialog = QDialog(parent)
    dialog.setWindowTitle("Confirmar comando")
    dialog.setMinimumSize(600, 300)
    dialog.setMaximumSize(700, 560)
    dialog.resize(640, 400)
    layout = QVBoxLayout(dialog)
    layout.setSpacing(12)

    intro = QLabel("El modelo quiere ejecutar el siguiente comando:")
    intro.setWordWrap(True)
    layout.addWidget(intro)

    # El comando lo propone el modelo. Fijamos PlainText y no pasamos por
    # el auto-detect de RichText de QLabel, que interpretaría <b>...</b>
    # como formato y podría usarse para spoofear el diálogo.
    command_label = QLabel(command or "(vacío)")
    command_label.setTextFormat(Qt.TextFormat.PlainText)
    command_label.setWordWrap(True)
    command_label.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse
    )
    font = QFont("Menlo")
    font.setStyleHint(QFont.StyleHint.Monospace)
    command_label.setFont(font)
    command_label.setStyleSheet(
        "background-color: #0F1514;"
        "border: 1px solid #27332F;"
        "border-radius: 6px;"
        "padding: 10px 12px;"
        "color: #D5DED9;"
    )
    layout.addWidget(command_label)

    # cwd viene controlado por el modelo. Si no se escapa, podría inyectar
    # HTML y falsear el diálogo (por ejemplo, mostrar una ruta distinta a
    # la real). Se escapa por separado, manteniendo el <b> del propio
    # diálogo, que sí es nuestro.
    meta = QLabel(
        f"<b>Directorio:</b> {html.escape(str(cwd))} &nbsp;&nbsp; "
        f"<b>Timeout:</b> {int(timeout)} s"
    )
    meta.setTextFormat(Qt.TextFormat.RichText)
    layout.addWidget(meta)

    if risks:
        warning = QLabel(
            "⚠ <b>Atención:</b>\n" + "\n".join(f"• {r}" for r in risks)
        )
        warning.setTextFormat(Qt.TextFormat.RichText)
        warning.setWordWrap(True)
        warning.setStyleSheet(
            "background-color: #2A1A1A;"
            "border: 1px solid #6B3333;"
            "border-radius: 6px;"
            "padding: 10px 12px;"
            "color: #E0A0A0;"
        )
        layout.addWidget(warning)

    layout.addStretch(1)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
    )
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Ejecutar")
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    return dialog.exec() == QDialog.DialogCode.Accepted


# -- resto de operaciones ----------------------------------------------------

def _confirm_generic(parent: QWidget | None, name: str, arguments: dict) -> bool:
    # Casos con contenido largo: usamos diálogo con scroll.
    if _is_write_tool(name):
        return _confirm_file_write(parent, name, arguments)

    if name == "borrar_archivo":
        path = str(arguments.get("path", ""))
        text = (
            f"¿Quieres borrar el archivo «{path}»?\n\n"
            "Esta operación no se puede deshacer."
        )
        title = "Confirmar borrado"
    elif name == "crear_carpeta":
        path = str(arguments.get("path", ""))
        text = f"¿Quieres crear la carpeta «{path}»?"
        title = "Confirmar creación de carpeta"
    else:
        return _confirm_generic_with_scroll(parent, name, arguments)

    reply = QMessageBox.question(
        parent,
        title,
        text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return reply == QMessageBox.StandardButton.Yes


def _confirm_generic_with_scroll(
    parent: QWidget | None, name: str, arguments: dict
) -> bool:
    """Fallback para herramientas sin confirmación específica.

    Muestra los argumentos en un visor con scroll y tamaño fijo. Sin esto,
    una herramienta MCP con argumentos largos (por ejemplo un bloque de
    código) hacía crecer el diálogo más allá de la pantalla.
    """
    import json as _json

    dialog = QDialog(parent)
    dialog.setWindowTitle(f"Confirmar: {name}")
    dialog.setMinimumSize(640, 440)
    dialog.setMaximumSize(640, 440)
    dialog.resize(640, 440)
    layout = QVBoxLayout(dialog)
    layout.setSpacing(10)

    question = QLabel(
        f"La herramienta <b>{html.escape(name)}</b> puede modificar o "
        "ejecutar acciones. ¿Quieres permitirla?"
    )
    question.setTextFormat(Qt.TextFormat.RichText)
    question.setWordWrap(True)
    layout.addWidget(question)

    layout.addWidget(QLabel("<b>Argumentos</b>:"))

    args_view = QPlainTextEdit()
    try:
        rendered = _json.dumps(arguments, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        rendered = str(arguments)
    args_view.setPlainText(rendered)
    args_view.setReadOnly(True)
    args_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
    args_view.setMaximumHeight(240)
    args_view.setMinimumHeight(160)
    font = QFont("Menlo")
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSize(11)
    args_view.setFont(font)
    layout.addWidget(args_view)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
    )
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Permitir")
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    return dialog.exec() == QDialog.DialogCode.Accepted


# Nombres de herramientas (nativas o MCP) que escriben contenido en archivos.
# Se usa para decidir si un diálogo debe mostrar el contenido con scroll
# en lugar de volcar los argumentos como texto plano.
_WRITE_TOOL_HINTS = (
    "crear_archivo",
    "escribir_archivo",
    "write_file",
    "edit_file",
    "create_file",
)


def _is_write_tool(name: str) -> bool:
    """Cierto si la herramienta escribe contenido en un archivo."""
    return any(hint in name for hint in _WRITE_TOOL_HINTS)


def _confirm_file_write(parent: QWidget | None, name: str, arguments: dict) -> bool:
    """Diálogo de confirmación para crear/escribir archivos.

    Usa un QPlainTextEdit con altura fija y scroll en lugar de QMessageBox.
    Sin esto, un archivo grande hacía que el diálogo creciera más allá de
    la pantalla y los botones quedaban inaccesibles.
    """
    path = str(arguments.get("path", ""))
    content = str(arguments.get("content", ""))
    is_mcp = name.startswith("mcp__")
    if "write_file" in name or "create_file" in name or name == "crear_archivo":
        action = "escribir"
    else:
        action = "modificar"

    # Para herramientas MCP, el argumento puede llamarse "content" o "text".
    if not content:
        for key in ("text", "body", "data"):
            if key in arguments and isinstance(arguments[key], str):
                content = arguments[key]
                break

    # Edits de mcp__fs__edit_file: mostrar como lista JSON legible.
    if not content and "edits" in arguments:
        import json as _json
        try:
            content = _json.dumps(arguments["edits"], indent=2, ensure_ascii=False)
        except (TypeError, ValueError):
            content = str(arguments.get("edits", ""))

    size_bytes = len(content.encode("utf-8"))

    dialog = QDialog(parent)
    dialog.setWindowTitle(f"Confirmar: {name}" if name.startswith("mcp__") else "Confirmar escritura")
    # Doble constraint: en macOS, setFixedSize a veces no respeta el
    # tamaño si el contenido interno pide más. Forzamos min=max=640x440.
    dialog.setMinimumSize(640, 440)
    dialog.setMaximumSize(640, 440)
    dialog.resize(640, 440)
    layout = QVBoxLayout(dialog)
    layout.setSpacing(10)

    # Pregunta
    question = QLabel(
        f"¿Quieres <b>{action}</b> el archivo "
        f"<code>{html.escape(path)}</code>?"
    )
    question.setTextFormat(Qt.TextFormat.RichText)
    question.setWordWrap(True)
    layout.addWidget(question)

    # Etiqueta del contenido + tamaño
    meta = QLabel(f"<b>Contenido</b> ({size_bytes} bytes):")
    meta.setTextFormat(Qt.TextFormat.RichText)
    layout.addWidget(meta)

    # Visor de contenido: altura fija, scroll vertical y horizontal.
    content_view = QPlainTextEdit()
    content_view.setPlainText(content if content else "(vacío)")
    content_view.setReadOnly(True)
    content_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
    # Límite de altura: el widget no debe pedir más de 220px, aunque
    # el documento sea enorme. Esto evita que el layout del diálogo
    # se expanda pese al setMaximumSize.
    content_view.setMaximumHeight(220)
    content_view.setMinimumHeight(160)
    font = QFont("Menlo")
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSize(11)
    content_view.setFont(font)
    layout.addWidget(content_view)

    # Botones
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
    )
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText(action.capitalize())
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    return dialog.exec() == QDialog.DialogCode.Accepted


def warn(parent: QWidget | None, title: str, message: str) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(message)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.exec()


# -- agente ------------------------------------------------------------------

def edit_agent(
    parent: QWidget | None,
    *,
    agent: "Agent",
    available_tools: list[str],
    available_models: list[str] | None = None,
) -> "Agent | None":
    """Devuelve un Agent editado o None si se cancela.

    ``available_tools`` es la lista completa de nombres de herramientas
    disponibles en la app para que el usuario elija cuáles permite. Si el
    agente tenía ``allowed_tools=None``, el checkbox "todas" arranca
    marcado; si no, se marcan solo las que estaban permitidas.

    ``available_models`` es la lista de modelos que Ollama ha reportado.
    Si está vacía, el combo de modelo se muestra deshabilitado con la
    opción "(usar el global)".
    """
    from core.agents import Agent
    from PySide6.QtWidgets import (
        QCheckBox,
        QComboBox,
        QDoubleSpinBox,
        QListWidget,
        QListWidgetItem,
        QPlainTextEdit,
        QSpinBox,
    )

    dialog = QDialog(parent)
    dialog.setWindowTitle("Editar agente")
    dialog.setMinimumWidth(520)
    layout = QVBoxLayout(dialog)
    layout.setSpacing(10)

    # -- nombre y categoría
    name_row = QFormLayout()
    name_edit = QLineEdit(agent.name)
    name_row.addRow("Nombre:", name_edit)
    category_edit = QLineEdit(agent.category)
    category_edit.setPlaceholderText(
        "(opcional · agrupa el agente en la lista)"
    )
    name_row.addRow("Categoría:", category_edit)
    layout.addLayout(name_row)

    # -- system prompt
    layout.addWidget(QLabel("Instrucciones persistentes (system prompt):"))
    prompt_edit = QPlainTextEdit()
    prompt_edit.setPlaceholderText(
        "Se añaden al inicio de cada conversación. Ej: «Responde en español, "
        "sin rodeos, cita rutas concretas cuando hables de archivos»."
    )
    prompt_edit.setPlainText(agent.system_prompt)
    prompt_edit.setFixedHeight(120)
    layout.addWidget(prompt_edit)

    # -- parámetros
    params_row = QFormLayout()
    temp_spin = QDoubleSpinBox()
    temp_spin.setRange(0.0, 2.0)
    temp_spin.setSingleStep(0.1)
    temp_spin.setDecimals(1)
    temp_spin.setValue(agent.temperature)
    params_row.addRow("Temperature:", temp_spin)

    ctx_spin = QSpinBox()
    ctx_spin.setRange(0, 512_000)
    ctx_spin.setSingleStep(1024)
    ctx_spin.setSpecialValueText("(por defecto del modelo)")
    ctx_spin.setValue(agent.num_ctx)
    params_row.addRow("num_ctx:", ctx_spin)

    # Combo de modelo. "(usar el global)" = sin modelo específico.
    model_combo = QComboBox()
    model_combo.addItem("(usar el global)", "")
    models = list(available_models or [])
    for m in models:
        model_combo.addItem(m, m)
    # Si el agente apunta a un modelo que ya no está disponible, lo
    # añadimos al final con un sufijo para no perder la configuración.
    if agent.model and agent.model not in models:
        model_combo.addItem(f"{agent.model} (no disponible)", agent.model)
    # Seleccionar el modelo actual del agente.
    idx = model_combo.findData(agent.model)
    if idx >= 0:
        model_combo.setCurrentIndex(idx)
    if not models:
        # Sin modelos detectados, no tiene sentido dejar elegir uno
        # específico. Se queda en "(usar el global)".
        pass
    params_row.addRow("Modelo:", model_combo)
    layout.addLayout(params_row)

    # -- herramientas permitidas
    all_tools_check = QCheckBox("Permitir todas las herramientas disponibles")
    all_tools_check.setChecked(agent.allowed_tools is None)
    layout.addWidget(all_tools_check)

    tools_label = QLabel("Herramientas permitidas:")
    layout.addWidget(tools_label)

    tools_list = QListWidget()
    tools_list.setFixedHeight(140)
    allowed_set = set(agent.allowed_tools or [])
    for tool_name in available_tools:
        item = QListWidgetItem(tool_name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        if agent.allowed_tools is None:
            item.setCheckState(Qt.CheckState.Checked)
        else:
            item.setCheckState(
                Qt.CheckState.Checked
                if tool_name in allowed_set
                else Qt.CheckState.Unchecked
            )
        tools_list.addItem(item)
    layout.addWidget(tools_list)

    def _on_toggle_all(checked: bool) -> None:
        tools_list.setEnabled(not checked)
        tools_label.setEnabled(not checked)

    all_tools_check.toggled.connect(_on_toggle_all)
    _on_toggle_all(all_tools_check.isChecked())

    # -- botones
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None

    new_name = name_edit.text().strip()
    if not new_name:
        QMessageBox.warning(parent, "Agente", "El nombre no puede estar vacío.")
        return None

    if all_tools_check.isChecked():
        allowed: list[str] | None = None
    else:
        allowed = []
        for i in range(tools_list.count()):
            item = tools_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                allowed.append(item.text())

    selected_model = model_combo.currentData()
    if not isinstance(selected_model, str):
        selected_model = ""

    return Agent(
        name=new_name,
        system_prompt=prompt_edit.toPlainText(),
        temperature=temp_spin.value(),
        num_ctx=ctx_spin.value(),
        allowed_tools=allowed,
        model=selected_model,
        category=category_edit.text().strip(),
        # La UI de edición todavía no expone estos tres campos. Se
        # preservan tal cual del agente original para no perderlos al
        # guardar. Cuando se añadan al formulario, sustituir por los
        # valores de los widgets correspondientes.
        top_p=agent.top_p,
        top_k=agent.top_k,
        repeat_penalty=agent.repeat_penalty,
    )
