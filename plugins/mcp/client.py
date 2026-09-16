from __future__ import annotations

import asyncio
import os
import threading
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any


class MCPError(RuntimeError):
    """Error controlado del adaptador MCP."""


@dataclass(frozen=True)
class MCPServerConfig:
    """Configuración mínima de un servidor MCP por stdio."""

    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None

    def __post_init__(self) -> None:
        if not self.command.strip():
            raise ValueError("El comando del servidor MCP no puede estar vacío.")


class MCPClient:
    """Adaptador síncrono con una sesión MCP persistente por servidor.

    La API pública sigue siendo síncrona para que el núcleo no tenga que conocer
    asyncio. Internamente, cada cliente mantiene un event loop dedicado en un
    hilo de fondo y conserva abierta la sesión/transport de MCP entre llamadas.
    Así, un servidor stdio (por ejemplo ``npx server-filesystem``) se arranca una
    sola vez al activar el servidor y se cierra al desactivarlo.
    """

    def __init__(self, server: MCPServerConfig):
        self.server = server
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._client: Any = None
        self._client_cm: Any = None
        self._initial_tools: list[dict[str, Any]] | None = None
        self._closed = False
        self._lifecycle_lock = threading.Lock()

    def list_tools(self) -> list[dict[str, Any]]:
        self._ensure_connected()
        if self._initial_tools is not None:
            tools = self._initial_tools
            self._initial_tools = None
            return list(tools)
        return self._submit(self._async_list_tools())

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        if not name.strip():
            raise MCPError("El nombre de la herramienta MCP no puede estar vacío.")
        self._ensure_connected()
        return self._submit(self._async_call_tool(name, arguments or {}))

    def close(self) -> None:
        """Cierra la sesión MCP y su proceso hijo, de forma idempotente."""
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            loop = self._loop
            thread = self._loop_thread

        if loop is None:
            return

        try:
            future = asyncio.run_coroutine_threadsafe(self._async_close(), loop)
            future.result(timeout=5)
        except Exception:
            # La limpieza debe ser best-effort: el cierre de la aplicación no
            # debe quedar bloqueado por un servidor MCP que no responde.
            pass
        finally:
            loop.call_soon_threadsafe(loop.stop)
            if thread is not None and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=5)
            self._loop = None
            self._loop_thread = None

    def __del__(self) -> None:
        # No dependemos del destructor para la limpieza normal. Solo intentamos
        # cerrar si el objeto se recoge con su hilo todavía vivo.
        try:
            self.close()
        except Exception:
            pass

    @staticmethod
    def to_ollama_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convierte definiciones MCP a la forma de herramientas de Ollama."""
        result: list[dict[str, Any]] = []
        for tool in tools:
            name = str(tool.get("name", "")).strip()
            if not name:
                continue
            description = str(tool.get("description", "")).strip()
            schema = tool.get("inputSchema") or tool.get("input_schema") or {}
            if not isinstance(schema, dict):
                schema = {}
            parameters = {
                "type": schema.get("type", "object"),
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
            }
            result.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": description,
                        "parameters": parameters,
                    },
                }
            )
        return result

    async def _async_list_tools(self) -> list[dict[str, Any]]:
        try:
            response = await self._client.list_tools()
            return [self._tool_to_dict(tool) for tool in response.tools]
        except Exception as exc:
            raise MCPError(f"No se pudo consultar el servidor MCP: {exc}") from exc

    async def _async_call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        try:
            result = await self._client.call_tool(name, arguments)
            return self._result_to_text(result)
        except Exception as exc:
            raise MCPError(f"No se pudo ejecutar la herramienta MCP «{name}»: {exc}") from exc

    async def _async_connect(self) -> list[dict[str, Any]]:
        Client, server_params = self._load_sdk()
        params = server_params(
            command=self.server.command,
            args=list(self.server.args),
            env=self._environment(),
            cwd=self.server.cwd,
        )
        try:
            # MCP SDK v2 mantiene el transporte y el proceso abiertos mientras
            # el Client permanezca dentro de este contexto asíncrono.
            self._client_cm = Client(params)
            self._client = await self._client_cm.__aenter__()
            self._initial_tools = await self._async_list_tools()
            return list(self._initial_tools)
        except Exception:
            await self._async_close()
            raise

    async def _async_close(self) -> None:
        client_cm = self._client_cm
        self._client = None
        self._client_cm = None
        if client_cm is not None:
            try:
                await client_cm.__aexit__(None, None, None)
            except Exception:
                pass

    def _ensure_connected(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                raise MCPError("El cliente MCP ya está cerrado.")
            if self._loop_thread is not None:
                # Ya hay un loop y, si la conexión inicial falló, propagamos el
                # mismo error en lugar de intentar crear procesos duplicados.
                pass
            else:
                self._ready.clear()
                self._startup_error = None
                self._loop = asyncio.new_event_loop()
                self._loop_thread = threading.Thread(
                    target=self._run_loop,
                    name=f"MCP-{self.server.command}",
                    daemon=True,
                )
                self._loop_thread.start()

        self._ready.wait(timeout=30)
        if not self._ready.is_set():
            raise MCPError("Tiempo agotado al iniciar el servidor MCP.")
        if self._startup_error is not None:
            error = self._startup_error
            raise error if isinstance(error, MCPError) else MCPError(str(error))

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        future = asyncio.ensure_future(self._async_connect(), loop=self._loop)

        def startup_done(done: Future[Any]) -> None:
            try:
                done.result()
            except BaseException as exc:
                self._startup_error = exc
            finally:
                self._ready.set()

        future.add_done_callback(startup_done)
        try:
            self._loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()

    def _submit(self, coroutine: Any) -> Any:
        loop = self._loop
        if loop is None or self._closed:
            raise MCPError("El cliente MCP no está conectado.")
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
            return future.result()
        except MCPError:
            raise
        except Exception as exc:
            raise MCPError(str(exc)) from exc

    def _environment(self) -> dict[str, str]:
        """Construye un entorno mínimo para el proceso MCP."""
        allowed = {
            "PATH",
            "HOME",
            "USERPROFILE",
            "TMP",
            "TEMP",
            "SystemRoot",
            "WINDIR",
        }
        env = {key: value for key, value in os.environ.items() if key in allowed}
        env.update(self.server.env)
        return env

    @staticmethod
    def _tool_to_dict(tool: Any) -> dict[str, Any]:
        if hasattr(tool, "model_dump"):
            try:
                return tool.model_dump(by_alias=True, exclude_none=True)
            except TypeError:
                return tool.model_dump()
        if hasattr(tool, "dict"):
            try:
                return tool.dict(by_alias=True, exclude_none=True)
            except TypeError:
                return tool.dict()
        if isinstance(tool, dict):
            return tool
        return {
            "name": getattr(tool, "name", ""),
            "description": getattr(tool, "description", ""),
            "inputSchema": getattr(tool, "inputSchema", getattr(tool, "input_schema", {})),
            "annotations": getattr(tool, "annotations", None),
        }

    @staticmethod
    def _result_to_text(result: Any) -> str:
        is_error = getattr(result, "is_error", getattr(result, "isError", False))
        prefix = "ERROR MCP: " if is_error else ""
        content = getattr(result, "content", result)
        if not isinstance(content, list):
            return prefix + str(content)

        parts: list[str] = []
        for item in content:
            if hasattr(item, "text"):
                parts.append(str(item.text))
            elif isinstance(item, dict) and "text" in item:
                parts.append(str(item["text"]))
            else:
                parts.append(str(item))
        return prefix + "\n".join(parts)

    @staticmethod
    def _load_sdk() -> tuple[Any, Any]:
        try:
            from mcp import Client, StdioServerParameters
        except ImportError as exc:
            raise MCPError(
                "El plugin MCP está instalado en el proyecto, pero falta la dependencia «mcp». "
                "Instálala con: pip install -r plugins/mcp/requirements.txt"
            ) from exc
        return Client, StdioServerParameters
