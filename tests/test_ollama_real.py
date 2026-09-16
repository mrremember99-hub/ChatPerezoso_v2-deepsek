"""Pruebas opcionales contra una instancia real de Ollama.

No se ejecutan por defecto porque requieren Ollama y un modelo instalado.
Ejecutar con PEREZOSO_REAL_OLLAMA=1 pytest -q.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.ollama import OllamaClient, OllamaError
from core.tools import ToolRegistry
from core.workspace import Workspace


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("PEREZOSO_REAL_OLLAMA") != "1",
        reason="Prueba de integración desactivada; usar PEREZOSO_REAL_OLLAMA=1",
    ),
]


def _client_and_model() -> tuple[OllamaClient, str]:
    host = os.environ.get("PEREZOSO_OLLAMA_HOST", "http://localhost:11434")
    client = OllamaClient(host)
    models = client.list_models()
    if not models:
        raise OllamaError("Ollama está disponible pero no tiene modelos instalados.")
    model = os.environ.get("PEREZOSO_OLLAMA_MODEL", models[0])
    if model not in models:
        raise OllamaError(f"El modelo solicitado no está instalado: {model}")
    return client, model


def test_real_ollama_lists_models() -> None:
    client, model = _client_and_model()
    assert model


def test_real_ollama_conversation() -> None:
    client, model = _client_and_model()
    chunks: list[str] = []
    result = client.chat(
        model=model,
        messages=[{"role": "user", "content": "Responde únicamente con: OK"}],
        tools=None,
        on_text=chunks.append,
        on_tool=lambda name, args: f"ERROR inesperado: {name} {args}",
    )
    assert result.strip()


def test_real_ollama_informative_request_does_not_call_tools(tmp_path: Path) -> None:
    client, model = _client_and_model()
    tools = ToolRegistry(Workspace(tmp_path))
    called: list[tuple[str, dict]] = []

    result = client.chat(
        model=model,
        messages=[
            {
                "role": "user",
                "content": "¿Qué es el diseño editorial? Responde brevemente en dos párrafos.",
            }
        ],
        tools=tools.definitions(),
        on_text=lambda _text: None,
        on_tool=lambda name, arguments: called.append((name, arguments)) or "no debe ejecutarse",
    )

    assert result.strip()
    assert called == []


def test_real_ollama_tool_roundtrip(tmp_path: Path) -> None:
    client, model = _client_and_model()
    workspace = Workspace(tmp_path)
    tools = ToolRegistry(workspace)
    called: list[tuple[str, dict]] = []

    def on_tool(name: str, arguments: dict) -> str:
        called.append((name, arguments))
        return tools.call(name, arguments)

    result = client.chat(
        model=model,
        messages=[
            {
                "role": "user",
                "content": (
                    "Lista los archivos de la carpeta actual usando la herramienta "
                    "listar_carpeta. No inventes el resultado."
                ),
            }
        ],
        tools=tools.definitions(),
        on_text=lambda _text: None,
        on_tool=on_tool,
    )

    assert called, "El modelo no llegó a ejecutar ninguna herramienta."
    assert called[0][0] == "listar_carpeta"
    assert result.strip()
