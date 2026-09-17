"""Adaptador síncrono con una sesión MCP persistente por servidor.

La API pública es síncrona para que el núcleo no tenga que conocer asyncio.
Internamente, cada cliente mantiene un event loop dedicado en un hilo de
fondo y conserva abierta la conexión MCP entre llamadas.

Usa la API de alto nivel de ``mcp`` v2: ``Client(StdioServerParameters)``.
La conexión se mantiene abierta con un ``AsyncExitStack`` para que el
``async with`` no termine hasta el cierre explícito.
"""
from __future__ import annotations

import asyncio
import inspect
import threading
import time
from concurrent.futures import Future
from contextlib import AsyncExitStack
from typing import Any

from ._base import MCPError, MCPServerConfig


_POLL_INTERVAL_SECONDS = 0.2


class MCPClient:
    def __init__(self, server: MCPServerConfig):
        self.server = server
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._client: Any = None
        self._exit_stack: AsyncExitStack | None = None
        self._initial_tools: list[dict[str, Any]] | None = None
        self._closed = False
        self._lifecycle_lock = threading.Lock()

    # -- API pública ---------------------------------------------------------

    def list_tools(self) -> list[dict[str, Any]]:
        self._ensure_connected()
        if self._initial_tools is not None:
            tools = self._initial_tools
            self._initial_tools = None
            return list(tools)
        return self._submit(self._async_list_tools(), timeout=60.0)

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        cancel_event: threading.Event | None = None,
    ) -> str:
        if not name.strip():
            raise MCPError("El nombre de la herramienta MCP no puede estar vacío.")
        self._ensure_connected()
        return self._submit(
            self._async_call_tool(name, arguments or {}),
            timeout=30.0,
            cancel_event=cancel_event,
        )

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
            pass
        finally:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
            if (
                thread is not None
                and thread.is_alive()
                and thread is not threading.current_thread()
            ):
                thread.join(timeout=5)
            self._loop = None
            self._loop_thread = None

    def __del__(self) -> None:
        if self._closed:
            return
        try:
            self._closed = True
            loop = self._loop
            if loop is not None:
                loop.call_soon_threadsafe(loop.stop)
        except Exception:
            pass

    # -- conversión a formato Ollama ----------------------------------------

    @staticmethod
    def to_ollama_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
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

    # -- implementación asíncrona -------------------------------------------

    async def _async_list_tools(self) -> list[dict[str, Any]]:
        assert self._client is not None
        try:
            response = await self._client.list_tools()
            tools = getattr(response, "tools", response)
            return [self._tool_to_dict(tool) for tool in tools]
        except Exception as exc:
            raise MCPError(f"No se pudo consultar el servidor MCP: {exc}") from exc

    async def _async_call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        assert self._client is not None
        try:
            result = await self._client.call_tool(name, arguments)
            return self._result_to_text(result)
        except Exception as exc:
            raise MCPError(
                f"No se pudo ejecutar la herramienta MCP «{name}»: {exc}"
            ) from exc

    async def _async_connect(self) -> list[dict[str, Any]]:
        Client, StdioServerParameters = self._load_sdk()
        params = self._build_params(StdioServerParameters)
        try:
            self._exit_stack = AsyncExitStack()
            self._client = await self._exit_stack.enter_async_context(Client(params))
            self._initial_tools = await self._async_list_tools()
            return list(self._initial_tools)
        except BaseException as exc:
            await self._async_close()
            root = self._unwrap_exception(exc)
            if root is exc:
                raise
            raise MCPError(str(root)) from exc

    async def _async_close(self) -> None:
        stack = self._exit_stack
        self._client = None
        self._exit_stack = None
        if stack is not None:
            try:
                await stack.aclose()
            except Exception:
                pass

    # -- infraestructura síncrona ------------------------------------------

    def _ensure_connected(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                raise MCPError("El cliente MCP ya está cerrado.")
            if self._loop_thread is None:
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
                self._loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            self._loop.close()

    def _submit(
        self,
        coroutine: Any,
        *,
        timeout: float,
        cancel_event: threading.Event | None = None,
    ) -> Any:
        loop = self._loop
        if loop is None or self._closed:
            raise MCPError("El cliente MCP no está conectado.")
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        except Exception as exc:
            raise MCPError(str(exc)) from exc

        deadline = time.monotonic() + timeout
        while True:
            if cancel_event is not None and cancel_event.is_set():
                future.cancel()
                raise MCPError("Operación MCP cancelada por el usuario.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                future.cancel()
                raise MCPError(
                    f"Tiempo agotado esperando al servidor MCP ({timeout:.0f}s)."
                )
            wait = remaining if cancel_event is None else min(
                _POLL_INTERVAL_SECONDS, remaining
            )
            try:
                return future.result(timeout=wait)
            except TimeoutError:
                continue
            except MCPError:
                raise
            except Exception as exc:
                raise MCPError(str(exc)) from exc

    def _resolve_env(self) -> dict[str, str] | None:
        """Entorno del subproceso MCP.

        Sin overrides, devuelve ``None`` para que el SDK aplique su
        entorno por defecto (PATH, HOME, TMPDIR, etc., sin filtrar
        secretos del proceso padre). Con overrides, parte de ese mismo
        entorno por defecto y añade los del usuario.
        """
        if not self.server.env:
            return None
        merged: dict[str, str] = {}
        try:
            from mcp.client.stdio import get_default_environment

            merged.update(get_default_environment())
        except Exception:
            import os

            for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SHELL", "USER"):
                value = os.environ.get(key)
                if value:
                    merged[key] = value
        merged.update(self.server.env)
        return merged

    # -- construcción de params ---------------------------------------------

    def _build_params(self, StdioServerParameters: Any) -> Any:
        """Construye los parámetros con introspección robusta.

        El SDK 2.x define ``StdioServerParameters`` como un pydantic
        BaseModel. Su ``__init__`` es ``(self, /, **data)`` y NO expone
        los campos como parámetros con nombre, así que
        ``inspect.signature`` no sirve. En su lugar se inspeccionan
        ``model_fields`` (pydantic v2) o ``__fields__`` (pydantic v1).
        Si tampoco están, se cae a una lista de campos conocidos.
        """
        accepted = self._accepted_fields(StdioServerParameters)

        kwargs: dict[str, Any] = {"command": self.server.command}
        if "args" in accepted:
            kwargs["args"] = list(self.server.args)
        if "env" in accepted:
            env = self._resolve_env()
            if env is not None:
                kwargs["env"] = env
        if "cwd" in accepted and self.server.cwd:
            kwargs["cwd"] = self.server.cwd
        return StdioServerParameters(**kwargs)

    @staticmethod
    def _accepted_fields(cls: Any) -> set[str]:
        """Campos aceptados por un pydantic BaseModel o, si no, firma
        del constructor."""
        fields = getattr(cls, "model_fields", None)
        if isinstance(fields, dict):
            return set(fields.keys())
        fields_v1 = getattr(cls, "__fields__", None)
        if isinstance(fields_v1, dict):
            return set(fields_v1.keys())
        try:
            sig = inspect.signature(cls.__init__)
            return set(sig.parameters.keys())
        except (TypeError, ValueError):
            return {"command", "args", "env", "cwd"}

    # -- carga diferida del SDK ---------------------------------------------

    @staticmethod
    def _load_sdk() -> tuple[Any, Any]:
        try:
            from mcp import Client
            from mcp.client.stdio import StdioServerParameters
        except ImportError as exc:
            raise MCPError(
                "El SDK de MCP no está instalado. Instálalo con:\n"
                "  pip install -r plugins/mcp/requirements.txt"
            ) from exc
        return Client, StdioServerParameters

    @staticmethod
    def _unwrap_exception(exc: BaseException) -> BaseException:
        """Extrae la excepción real de un ExceptionGroup anidado."""
        visited = set()
        current: BaseException | None = exc
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            sub = getattr(current, "exceptions", None)
            if isinstance(sub, (list, tuple)) and sub:
                current = sub[0]
                continue
            return current
        return exc

    # -- extracción de datos del SDK ----------------------------------------

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
            "inputSchema": getattr(
                tool, "inputSchema", getattr(tool, "input_schema", {})
            ),
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
