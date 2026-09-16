"""Servidor MCP inocuo para comprobar la integración por stdio.

No accede a archivos ni ejecuta comandos. Sirve exclusivamente para validar la
activación de MCP desde ChatPerezoso.
"""

from mcp.server import MCPServer


server = MCPServer(
    name="chatperezoso-demo",
    title="ChatPerezoso · servidor MCP de prueba",
    instructions="Servidor de prueba con una única herramienta de lectura.",
)


@server.tool(
    name="saludar",
    description="Devuelve un saludo de prueba; no modifica datos.",
)
def saludar(nombre: str = "mundo") -> str:
    return f"Hola, {nombre}. El servidor MCP de prueba responde correctamente."


if __name__ == "__main__":
    server.run(transport="stdio")
