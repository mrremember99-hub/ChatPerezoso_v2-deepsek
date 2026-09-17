"""Servidor MCP inocuo para comprobar la integración por stdio.

No accede a archivos ni ejecuta comandos. Sirve exclusivamente para validar
la activación de MCP desde ChatPerezoso.

Ejecútalo con::

    python plugins/mcp/demo_server.py

El servidor arranca y espera peticiones JSON-RPC por stdin. La app lo lanza
como subproceso cuando el usuario activa el servidor MCP.
"""

from mcp.server import MCPServer


server = MCPServer(
    "chatperezoso-demo",
    instructions="Servidor de prueba con una única herramienta de lectura.",
)


@server.tool()
def saludar(nombre: str = "mundo") -> str:
    """Devuelve un saludo de prueba; no modifica datos."""
    return f"Hola, {nombre}. El servidor MCP de prueba responde correctamente."


if __name__ == "__main__":
    server.run(transport="stdio")
