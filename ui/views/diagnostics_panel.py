"""Bloque compacto de diagnóstico para la sidebar.

Muestra tres líneas con información de la sesión actual:
  · Modelo y parámetros activos
  · Número de respuestas y tiempo medio
  · Tokens estimados del contexto
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class DiagnosticsPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.model_label = QLabel("—")
        self.model_label.setObjectName("DiagnosticLine")
        self.model_label.setWordWrap(True)
        layout.addWidget(self.model_label)

        self.response_label = QLabel("Respuestas: 0")
        self.response_label.setObjectName("DiagnosticLine")
        layout.addWidget(self.response_label)

        self.context_label = QLabel("Contexto: ~0 tokens")
        self.context_label.setObjectName("DiagnosticLine")
        layout.addWidget(self.context_label)

        # Linea que solo aparece cuando hay intentos de tool calling
        # textual (indica que el modelo no soporta tools nativos).
        self.textual_label = QLabel("")
        self.textual_label.setObjectName("DiagnosticLine")
        self.textual_label.setStyleSheet("color: #E0BC7A;")
        self.textual_label.setVisible(False)
        layout.addWidget(self.textual_label)

        # Métricas reales de la última generación (tokens de prompt,
        # tokens generados, tok/s). Se oculta si Ollama no las envía.
        self.metrics_label = QLabel("")
        self.metrics_label.setObjectName("DiagnosticLine")
        self.metrics_label.setVisible(False)
        layout.addWidget(self.metrics_label)

    # -- API pública ---------------------------------------------------------

    def set_model(self, name: str, temperature: float, num_ctx: int) -> None:
        if not name:
            self.model_label.setText("—")
            return
        params: list[str] = [f"T={temperature:.1f}"]
        if num_ctx > 0:
            params.append(f"ctx={_format_ctx(num_ctx)}")
        self.model_label.setText(f"{name} · {', '.join(params)}")

    def set_responses(self, count: int, average_seconds: float) -> None:
        if count == 0:
            self.response_label.setText("Respuestas: 0")
            return
        self.response_label.setText(
            f"Respuestas: {count} · {average_seconds:.1f} s de media"
        )

    def set_textual_tool(self, attempts: int, responses: int) -> None:
        if attempts == 0:
            self.textual_label.setVisible(False)
            return
        if responses > 0:
            pct = (attempts / responses) * 100
            text = f"Tool-call textual: {attempts}/{responses} ({pct:.0f}%)"
        else:
            text = f"Tool-call textual: {attempts}"
        self.textual_label.setText(text)
        self.textual_label.setVisible(True)

    def set_context_tokens(self, tokens: int) -> None:
        self.context_label.setText(f"Contexto: ~{_format_tokens(tokens)} tokens")

    def set_last_metrics(self, text: str) -> None:
        """Muestra u oculta la línea de métricas reales de la última
        generación. Un string vacío la oculta."""
        self.metrics_label.setText(text)
        self.metrics_label.setVisible(bool(text))


def _format_ctx(num_ctx: int) -> str:
    if num_ctx >= 1000 and num_ctx % 1000 == 0:
        return f"{num_ctx // 1000}k"
    return str(num_ctx)


def _format_tokens(tokens: int) -> str:
    if tokens >= 1000:
        return f"{tokens / 1000:.1f}k"
    return str(tokens)
