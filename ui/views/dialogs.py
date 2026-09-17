"""Diálogos como funciones puras: reciben datos, devuelven datos.

El controlador los llama cuando toca; no sabe cómo se ven por dentro.
"""
from __future__ import annotations

import html

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


def confirm_tool(parent: QWidget, name: str, arguments: dict) -> bool:
    """Decide si el usuario aprueba una operación."""
    if name == "ejecutar_comando":
        return _confirm_shell(parent, arguments)
    return _confirm_generic(parent, name, arguments)


# -- shell -------------------------------------------------------------------

def _confirm_shell(parent: QWidget, arguments: dict) -> bool:
    from plugins.shell import analyze_risk

    command = str(arguments.get("command", ""))
    cwd = str(arguments.get("cwd", "."))
    timeout = arguments.get("timeout_seconds", 30)
    risks = analyze_risk(command)

    dialog = QDialog(parent)
    dialog.setWindowTitle("Confirmar comando")
    dialog.setMinimumWidth(520)
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

def _confirm_generic(parent: QWidget, name: str, arguments: dict) -> bool:
    # Casos con contenido largo: usamos diálogo con scroll.
    if name in {"crear_archivo", "escribir_archivo"}:
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
        text = (
            f"La herramienta «{name}» puede modificar o ejecutar acciones.\n\n"
            f"Argumentos: {arguments}\n\n¿Quieres permitir esta operación?"
        )
        title = "Confirmar operación"

    reply = QMessageBox.question(
        parent,
        title,
        text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return reply == QMessageBox.StandardButton.Yes


def _confirm_file_write(parent: QWidget, name: str, arguments: dict) -> bool:
    """Diálogo de confirmación para crear/escribir archivos.

    Usa un QPlainTextEdit con altura fija y scroll en lugar de QMessageBox.
    Sin esto, un archivo grande hacía que el diálogo creciera más allá de
    la pantalla y los botones quedaban inaccesibles.
    """
    path = str(arguments.get("path", ""))
    content = str(arguments.get("content", ""))
    action = "crear" if name == "crear_archivo" else "escribir o reemplazar"
    size_bytes = len(content.encode("utf-8"))

    dialog = QDialog(parent)
    dialog.setWindowTitle("Confirmar escritura")
    dialog.setMinimumWidth(560)
    dialog.setMaximumWidth(720)
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
    content_view.setFixedHeight(220)
    content_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
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


def warn(parent: QWidget, title: str, message: str) -> None:
    QMessageBox.warning(parent, title, message)


# -- agente ------------------------------------------------------------------

def edit_agent(
    parent: QWidget,
    *,
    agent: "Agent",
    available_tools: list[str],
) -> "Agent | None":
    """Devuelve un Agent editado o None si se cancela.

    ``available_tools`` es la lista completa de nombres de herramientas
    disponibles en la app para que el usuario elija cuáles permite. Si el
    agente tenía ``allowed_tools=None``, el checkbox "todas" arranca
    marcado; si no, se marcan solo las que estaban permitidas.
    """
    from core.agents import Agent
    from PySide6.QtWidgets import (
        QCheckBox,
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

    # -- nombre
    name_row = QFormLayout()
    name_edit = QLineEdit(agent.name)
    name_row.addRow("Nombre:", name_edit)
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

    return Agent(
        name=new_name,
        system_prompt=prompt_edit.toPlainText(),
        temperature=temp_spin.value(),
        num_ctx=ctx_spin.value(),
        allowed_tools=allowed,
    )
