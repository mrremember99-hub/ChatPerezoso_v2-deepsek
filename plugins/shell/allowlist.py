"""Allowlist de comandos auto-aprobables con autopilot+shell ON.

Cuando `auto_approve_tools` y `auto_approve_shell` estan activos, el
worker auto-aprueba `ejecutar_comando` sin dialogo. Sin esta allowlist,
un toggle pensado para iterar rapido con `pytest`/`ls`/`cat` se
convierte en "el modelo puede borrar lo que quiera sin preguntar".

Politica: solo programas read-only o de ejecucion de tests. Todo lo
que modifica estado (rm, mv, sed -i, sort -o, git push, pip install)
sigue pidiendo confirmacion manual aunque autopilot+shell esten ON.
La degradacion es a confirmacion, no a bloqueo: el flujo del usuario
sigue funcionando, solo con un dialogo extra.

Diseno deliberadamente conservador: cualquier duda → False.
"""
from __future__ import annotations

import shlex

from .client import ShellClient, ShellError

# Programas cuyo uso por defecto es read-only.
# NO incluye:
#   rm/mv/chmod/chown/tee/sed (escriben)
#   sort/uniq (aceptan argumento de salida: sort -o, uniq in out)
#   env (puede lanzar otro comando: env VAR=x cmd)
#   curl/wget/pip/npm/sudo/dd/mkfs (red, instalacion, escalada)
ALLOWED_PROGRAMS: frozenset[str] = frozenset({
    # Filesystem read-only
    "ls", "cat", "head", "tail", "wc", "grep", "file", "which",
    "diff", "tree", "pwd", "echo", "stat", "du", "df",
    "basename", "dirname", "readlink", "realpath",
    "date", "uname", "whoami", "id",
    # Tests / ejecucion controlada (flags validados aparte)
    "pytest", "python", "python3",
    # find: rechazado si lleva -exec/-delete/-fprint* (validado aparte)
    "find",
    # git: solo subcomandos read-only (validado aparte)
    "git",
})

# Subcomandos de git que no modifican el repositorio.
GIT_READONLY_SUBCOMMANDS: frozenset[str] = frozenset({
    "status", "log", "diff", "show", "branch", "remote",
    "config", "rev-parse", "ls-files", "blame", "describe",
    "tag", "stash",
})

# Modulos ejecutables via `python -m X`. Excluye pip (instala),
# venv (crea entorno), http.server (abre puerto), etc.
PYTHON_READONLY_MODULES: frozenset[str] = frozenset({
    "pytest", "unittest", "json.tool", "pydoc",
    # Verificacion de sintaxis: escriben solo .pyc en __pycache__/,
    # nunca tocan el .py fuente. Mismo criterio que pytest, que
    # tambien genera .pyc al importar los modulos de test.
    "py_compile", "compileall",
})

# Flags de find que ejecutan comandos o escriben ficheros.
_FIND_DANGEROUS_FLAGS: frozenset[str] = frozenset({
    "-exec", "-execdir", "-ok", "-okdir",
    "-delete",
    "-fprint", "-fprint0", "-fprintf", "-fls",
})


def is_command_allowed(command: str) -> bool:
    """True si el comando puede auto-aprobarse con autopilot+shell ON.

    Cualquier duda → False. La degradacion es a confirmacion manual,
    no a bloqueo: el usuario ve el dialogo normal.
    """
    if not isinstance(command, str) or not command.strip():
        return False

    # Reutilizar la validacion del ShellClient: metacaracteres,
    # programas prohibidos, shlex malformado. Una sola fuente de verdad.
    try:
        ShellClient._validate_command(command)
    except ShellError:
        return False

    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if not parts:
        return False

    program = parts[0].rsplit("/", 1)[-1].lower()
    if program not in ALLOWED_PROGRAMS:
        return False

    args = parts[1:]

    if program == "git":
        return _git_allowed(args)
    if program in {"python", "python3"}:
        return _python_allowed(args)
    if program == "find":
        return _find_allowed(args)
    if program == "tail":
        return _tail_allowed(args)
    return True


def _git_allowed(args: list[str]) -> bool:
    if not args:
        return False  # `git` sin subcomando: solo ayuda, no util
    sub = args[0].lower()
    if sub not in GIT_READONLY_SUBCOMMANDS:
        return False
    if sub == "stash":
        # `git stash` (list) OK. `git stash drop/pop/apply/...` modifica.
        if len(args) == 1:
            return True
        return len(args) >= 2 and args[1] == "list"
    return True


def _python_allowed(args: list[str]) -> bool:
    if not args:
        return False  # REPL interactivo
    # -c codigo inline: siempre rechazado (bypass del allowlist).
    if "-c" in args:
        return False
    # -i fuerza REPL tras script.
    if "-i" in args or "--interactive" in args:
        return False
    # -m modulo: validar contra la lista blanca.
    if "-m" in args:
        idx = args.index("-m")
        if idx + 1 >= len(args):
            return False
        return args[idx + 1] in PYTHON_READONLY_MODULES
    # Sin -m: tiene que haber un script (arg que no sea flag).
    for a in args:
        if not a.startswith("-"):
            return True
    return False


def _find_allowed(args: list[str]) -> bool:
    for a in args:
        if a in _FIND_DANGEROUS_FLAGS:
            return False
    return True


# tail sin -f/-F/--follow es read-only y termina solo. Con -f se queda
# colgado hasta el timeout (30 s), lo que consume el turno sin aportar
# nada util: el modelo no ve salida nueva, solo el ERROR de timeout.
_TAIL_FOLLOW_FLAGS: frozenset[str] = frozenset({
    "-f", "-F", "--follow", "--follow=name", "--follow=descriptor",
})


def _tail_allowed(args: list[str]) -> bool:
    for a in args:
        if a in _TAIL_FOLLOW_FLAGS:
            return False
        # Soporta --follow=NOMBRE (variante con argumento).
        if a.startswith("--follow="):
            return False
    return True
