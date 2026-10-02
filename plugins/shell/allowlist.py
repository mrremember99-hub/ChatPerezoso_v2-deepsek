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

import re
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

# P3#1: rechazar cualquier flag corto que contenga c/m/i. Antes
# solo se comparaba el token exacto, y "python3 -Ic 'codigo'"
# bypaseaba la allowlist (el token era "-Ic", no "-c"). El
# patron cubre cadenas de flags combinados tipo -Ic, -cI, -cm,
# -Ii. NO matchea long flags (--interactive, --config).
_PY_FLAG_WITH_DANGEROUS_CHAR = re.compile(r"^-[A-Za-z]*[cimCIM]")

# P3#3: subcomandos git con doble cara (leer por defecto,
# escribir con flags). Solo se permite su forma de LISTADO
# explicita (args todos en _GIT_LISTING_ARGS, o sin args).
_GIT_DUAL_USE_SUBCOMMANDS: frozenset[str] = frozenset({
    "config", "branch", "tag", "remote",
})

# Flags/valores admitidos en subcomandos duales.
_GIT_LISTING_ARGS: frozenset[str] = frozenset({
    "--list", "-l", "-a", "-r", "-v", "--verbose",
    "--get", "--get-all", "--get-regexp", "--show-origin",
    "--global", "--local", "--system", "--show-signature",
    "list", "show",
})

# P3#3: cualquier arg que escriba a fichero. Rechazado en
# cualquier subcomando (git diff --output, git log -o, etc.).
_GIT_OUTPUT_FLAGS: tuple[str, ...] = (
    "--output=", "--output", "-o",
)

# P3#4: programas read-only que aceptan escribir con flags.
# tree -o FICHERO escribe; tree sin -o es seguro.
_TREE_OUTPUT_FLAGS: frozenset[str] = frozenset({
    "-o", "--output",
})

# P3#4: modulos python con side effects segun args.
# json.tool in out escribe out; json.tool in solo lee.
# pydoc -w MODULO escribe HTML.
_PYDOC_WRITE_FLAGS: frozenset[str] = frozenset({
    "-w", "--write",
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
    if program == "tree":
        return _tree_allowed(args)
    return True


def _git_allowed(args: list[str]) -> bool:
    if not args:
        return False  # `git` sin subcomando: solo ayuda, no util
    sub = args[0].lower()
    if sub not in GIT_READONLY_SUBCOMMANDS:
        return False
    # P3#3: rechazar cualquier arg que escriba a fichero.
    for a in args[1:]:
        for flag in _GIT_OUTPUT_FLAGS:
            if a == flag or a.startswith(flag + "="):
                return False
    if sub == "stash":
        # `git stash` (list) OK. `git stash drop/pop/apply/...` modifica.
        if len(args) == 1:
            return True
        return len(args) >= 2 and args[1] == "list"
    # P3#3: subcomandos duales (config/branch/tag/remote) solo en
    # forma de listado. Sin args = listado; con args, todos deben
    # estar en la allowlist.
    if sub in _GIT_DUAL_USE_SUBCOMMANDS:
        rest = args[1:]
        if not rest:
            return True
        return all(a in _GIT_LISTING_ARGS for a in rest)
    return True


def _python_allowed(args: list[str]) -> bool:
    if not args:
        return False  # REPL interactivo
    # P3#1: cualquier flag corto con c/m/i dentro es peligroso,
    # incluso combinado con otros (-Ic, -cI, -cm, -Ii). Antes
    # solo se comparaba el token exacto y los combinados pasaban.
    # "-m" aislado se excluye: se valida despues contra la
    # allowlist de modulos (solo -m exacto, no combinado).
    for a in args:
        if a == "-m":
            continue
        if _PY_FLAG_WITH_DANGEROUS_CHAR.match(a):
            return False
    # --interactive es long flag: el patron de arriba no lo pilla.
    if "--interactive" in args:
        return False
    # -m modulo: validar contra la lista blanca (ahora solo si
    # es exactamente "-m" separado; los combinados ya rechazados).
    if "-m" in args:
        idx = args.index("-m")
        if idx + 1 >= len(args):
            return False
        mod = args[idx + 1]
        if mod not in PYTHON_READONLY_MODULES:
            return False
        rest = args[idx + 2:]
        # P3#4: json.tool in out escribe out. Solo un arg
        # posicional (el de entrada) es seguro.
        if mod == "json.tool":
            positional = [a for a in rest if not a.startswith("-")]
            if len(positional) > 1:
                return False
        # P3#4: pydoc -w MODULO escribe HTML.
        if mod == "pydoc":
            for a in rest:
                if a in _PYDOC_WRITE_FLAGS:
                    return False
                if a.startswith("--write="):
                    return False
        return True
    # Sin -m: tiene que haber un script (arg que no sea flag).
    for a in args:
        if not a.startswith("-"):
            return True
    return False


def _tree_allowed(args: list[str]) -> bool:
    # P3#4: tree -o FICHERO escribe. Rechazar.
    for a in args:
        if a in _TREE_OUTPUT_FLAGS:
            return False
        if a.startswith("--output="):
            return False
    return True


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
