"""Bloque de system prompt para el modo piloto automatico.

Cuando el usuario activa el piloto automatico, el cliente deja de
mostrar el dialogo de confirmacion para las tools (worker.
auto_approve=True). Pero el modelo no se entera: sigue su
instruccion generica de pedir permiso antes de actuar. Sin este
bloque, el modelo pregunta en prosa ("¿Te parece bien que lo lea
ahora?") y se pierden turnos esperando una respuesta que el
cliente ya no necesita.

Solucion: cuando auto_approve esta activo, inyectar este bloque
al final del system prompt. El modelo lo lee, deja de pedir
confirmacion y ejecuta las tools directamente.

Nota: el bloque NO menciona auto_approve_shell. El cliente sigue
aplicando esa doble puerta de forma independiente. Si el modelo
intenta shell con autopilot general ON pero shell OFF, el
cliente pedira el dialogo y el modelo se enterara por el rechazo.
Codificar la doble puerta aqui haria que modelos pequenos
tuviesen que distinguir "tool X vs shell" y se lian.
"""
from __future__ import annotations


BLOCK = (
    "MODO PILOTO AUTOMATICO ACTIVO.\n"
    "Las herramientas se ejecutan sin dialogo de confirmacion.\n"
    "No pidas permiso al usuario ni en texto ni esperando su\n"
    "respuesta. Si necesitas leer un archivo, leelo; si\n"
    "necesitas modificar, modifica; si necesitas listar, lista.\n"
    "No anuncies el plan antes de ejecutarlo: hazlo y luego\n"
    "reporta el resultado."
)


def inject(prompt: str, auto_approve: bool) -> str:
    """Devuelve prompt con el bloque de autopilot si esta activo.

    Casos:
      - auto_approve=False: prompt sin cambios (devolucion exacta).
      - auto_approve=True y prompt con contenido: prompt + "\n\n" + BLOCK.
      - auto_approve=True y prompt vacio o solo espacios: BLOCK.
    """
    if not auto_approve:
        return prompt
    if not prompt.strip():
        return BLOCK
    return prompt + "\n\n" + BLOCK
