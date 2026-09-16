"""Diálogos como funciones puras."""
from __future__ import annotations

import shlex

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QMessageBox, QWidget,
)


def ask_mcp_server(parent: QWidget, default_workspace: str) -> tuple[str, str] | None:
    dialog = QDialog(parent)
    dialog.setWindowTitle("Añadir servidor MCP")
    layout = QFormLayout(dialog)

    server_id = QLineEdit()
    server_id.setPlaceholderText("ej. fs, github, demo")
    server_id.setText("fs")
    layout.addRow("Nombre:", server_id)

    default_cmd = (
        "npx -y @modelcontextprotocol/server-filesystem "
        f"{shlex.quote(default_workspace)}"
    )
    command = QLineEdit(default_cmd)
    layout.addRow("Comando stdio:", command)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addRow(buttons)

    while True:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        name = server_id.text().strip()
        cmd = command.text().strip()
        if not name:
            QMessageBox.warning(
                parent, "MCP", "El nombre del servidor no puede estar vacío."
            )
            continue
        if not cmd:
            QMessageBox.warning(parent, "MCP", "El comando MCP no puede estar vacío.")
            continue
        return name, cmd


def confirm_tool(parent: QWidget, name: str, arguments: dict) -> bool:
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
    elif name in {"crear_archivo", "escribir_archivo"}:
        path = str(arguments.get("path", ""))
        content = str(arguments.get("content", ""))
        preview = content if len(content) <= 800 else content[:800] + "\n…(truncado)"
        action = "crear" if name == "crear_archivo" else "escribir o reemplazar"
        text = (
            f"¿Quieres {action} el archivo «{path}» con este contenido?\n\n"
            f"{preview if preview else '(vacío)'}\n\n"
            "Esta operación solo se realizará si la confirmas."
        )
        title = "Confirmar escritura"
    else:
        text = (
            f"La herramienta MCP «{name}» puede modificar o ejecutar acciones.\n\n"
            f"Argumentos: {arguments}\n\n¿Quieres permitir esta operación?"
        )
        title = "Confirmar operación MCP"

    reply = QMessageBox.question(
        parent, title, text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return reply == QMessageBox.StandardButton.Yes


def warn(parent: QWidget, title: str, message: str) -> None:
    QMessageBox.warning(parent, title, message)
