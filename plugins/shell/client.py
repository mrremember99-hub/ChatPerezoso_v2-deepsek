"""Ejecución de comandos simples sobre el workspace.

Reglas de seguridad, sin excepciones:

1. Sin ``shell=True``: el comando se parsea con ``shlex.split`` y se pasa
   como lista de argumentos. Nada de interpretación por el shell del sistema.
2. Sin metacaracteres de shell (``&&``, ``||``, ``;``, ``|``, ``>``, ``<``,
   `` ` ``, ``$(``, ``${``). Se rechaza cualquier comando que los contenga.
3. Sin sudo, su ni programas de sistema destructivos (``dd``, ``mkfs``,
   ``shutdown``, etc.). Ver ``_FORBIDDEN_PROGRAMS``.
4. ``cwd`` debe estar dentro del workspace. Cualquier ruta relativa que
   escape de la raíz se rechaza.
5. Timeout por defecto 30 s, máximo 300 s.
6. Salida limitada a 200 KB.
7. Entorno mínimo: solo PATH, HOME, LANG, TMPDIR, SHELL, USER.
8. La confirmación del usuario es siempre obligatoria (ver ``provider.py``).

El objetivo no es "permitir al usuario hacer cualquier cosa desde el chat".
Es "permitir al modelo proponer comandos acotados que el usuario aprueba
explícitamente, sin que un comando mal formado pueda escalar".
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path


class ShellError(RuntimeError):
    """Error controlado del plugin shell."""


# -- límites -----------------------------------------------------------------

DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 300
MAX_OUTPUT_BYTES = 200_000

# -- validación --------------------------------------------------------------

# Metacaracteres de shell. Rechazamos el comando entero si aparece alguno.
# Si el usuario quiere cadenas de comandos, que use su terminal.
_FORBIDDEN_METACHARS: tuple[str, ...] = (
    "&&", "||", ";", "|", ">", "<", "`", "$(", "${",
)

# Programas rechazados por nombre. Se compara con el primer token
# (el ejecutable). No es una lista blanca: cualquier otro binario puede
# ejecutarse, pero la confirmación del usuario es obligatoria.
_FORBIDDEN_PROGRAMS = frozenset({
    "sudo", "su", "doas",
    "dd", "mkfs", "mkfs.ext4", "mkfs.xfs", "fdisk", "parted",
    "shutdown", "reboot", "halt", "poweroff", "init",
    "kill", "killall", "pkill",
    "iptables", "ufw", "firewall-cmd",
    "systemctl", "service", "launchctl",
    "crontab", "at", "batch",
})

# Patrones de riesgo. No bloquean el comando, pero se muestran en la
# confirmación para que el usuario sepa lo que está aprobando.
_RISKY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\brm\s+-r", re.IGNORECASE), "rm recursivo: puede borrar árboles de archivos"),
    (re.compile(r"\brm\b", re.IGNORECASE), "rm borra archivos o carpetas"),
    (re.compile(r"\bchmod\s+-R\b", re.IGNORECASE), "chmod -R cambia permisos recursivamente"),
    (re.compile(r"\bchown\b", re.IGNORECASE), "chown cambia la propiedad de archivos"),
    (re.compile(r"\bmv\b", re.IGNORECASE), "mv puede mover o renombrar archivos"),
    (re.compile(r"\bcurl\b|\bwget\b", re.IGNORECASE), "descarga contenido de la red"),
    (re.compile(r"\bpip\s+install\b", re.IGNORECASE), "instala paquetes de Python"),
    (re.compile(r"\bnpm\s+install\b", re.IGNORECASE), "instala paquetes de Node"),
    (re.compile(r"\bpython3?\s+-c\b"), "ejecuta código Python inline"),
    (re.compile(r"\beval\b"), "eval ejecuta código dinámico"),
    (re.compile(r"/etc/"), "opera sobre /etc/"),
    (re.compile(r"\bgit\s+(push|reset|checkout|clean)\b", re.IGNORECASE),
     "comando Git que modifica el repositorio"),
    (re.compile(r"\b(bash|sh|zsh|fish|ksh)\s+-c\b", re.IGNORECASE),
     "invoca un shell con código inline (sortea las validaciones del plugin)"),
    (re.compile(r"\bpython3?\s+(-c|-m\s+pip)\b", re.IGNORECASE),
     "ejecuta Python con código inline o pip"),
    (re.compile(r"\b(perl|ruby|node|php)\s+-e\b", re.IGNORECASE),
     "invoca un intérprete con código inline"),
    (re.compile(r"\beval\b", re.IGNORECASE),
     "eval ejecuta código dinámico"),
)


def analyze_risk(command: str) -> list[str]:
    """Devuelve los motivos de riesgo del comando. Vacío si no hay."""
    return [reason for pattern, reason in _RISKY_PATTERNS if pattern.search(command)]


# -- cliente -----------------------------------------------------------------

class ShellClient:
    def __init__(self, workspace_root: str | Path):
        self.root = Path(workspace_root).expanduser().resolve()

    # -- API pública ---------------------------------------------------------

    def execute(
        self,
        command: str,
        *,
        cwd: str = ".",
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        cancel_event: threading.Event | None = None,
    ) -> str:
        """Ejecuta el comando y devuelve la salida formateada.

        Cualquier problema se devuelve como texto ``ERROR: ...`` para que el
        modelo lo lea; no se lanzan excepciones hacia el llamante salvo
        ``ShellError`` en casos internos graves (no debería ocurrir).
        """
        if not isinstance(command, str) or not command.strip():
            raise ShellError("El comando no puede estar vacío.")

        self._validate_command(command)

        timeout = self._clamp_timeout(timeout)
        work_dir = self._resolve_cwd(cwd)

        try:
            parts = shlex.split(command)
        except ValueError as exc:
            raise ShellError(f"Comando mal formado: {exc}") from exc
        if not parts:
            raise ShellError("El comando no contiene ningún token válido.")

        try:
            return self._run(parts, work_dir, timeout, cancel_event)
        except FileNotFoundError:
            return f"ERROR: el comando «{parts[0]}» no existe o no está en el PATH."
        except PermissionError:
            return f"ERROR: sin permisos para ejecutar «{parts[0]}»."

    # -- validación ----------------------------------------------------------

    @staticmethod
    def _validate_command(command: str) -> None:
        for token in _FORBIDDEN_METACHARS:
            if token in command:
                raise ShellError(
                    f"El comando contiene el metacaracter «{token}». "
                    "Ejecuta un solo comando simple, sin encadenamientos, "
                    "pipes ni redirecciones."
                )

        try:
            parts = shlex.split(command)
        except ValueError as exc:
            raise ShellError(f"Comando mal formado: {exc}") from exc
        if not parts:
            raise ShellError("El comando no contiene ningún token válido.")

        program = parts[0].rsplit("/", 1)[-1]  # admite rutas absolutas al binario
        if program in _FORBIDDEN_PROGRAMS:
            raise ShellError(
                f"El comando «{program}» está bloqueado por seguridad."
            )

    def _resolve_cwd(self, relative: str) -> Path:
        if not isinstance(relative, str) or not relative:
            relative = "."
        candidate = (self.root / relative).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ShellError("El directorio de trabajo está fuera del workspace.") from exc
        if not candidate.is_dir():
            raise ShellError(f"El directorio de trabajo no existe: {relative}")
        return candidate

    @staticmethod
    def _clamp_timeout(value: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return DEFAULT_TIMEOUT_SECONDS
        return max(1, min(number, MAX_TIMEOUT_SECONDS))

    # -- ejecución -----------------------------------------------------------

    def _run(
        self,
        parts: list[str],
        cwd: Path,
        timeout: int,
        cancel_event: threading.Event | None,
    ) -> str:
        proc = subprocess.Popen(
            parts,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            env=self._environment(),
        )

        deadline = time.monotonic() + timeout
        stdout = stderr = ""
        while True:
            if cancel_event is not None and cancel_event.is_set():
                self._terminate(proc)
                return "OPERACIÓN CANCELADA POR EL USUARIO: el comando fue interrumpido."
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._terminate(proc)
                return f"ERROR: el comando superó el timeout de {timeout} s."
            try:
                stdout, stderr = proc.communicate(timeout=min(0.5, remaining))
                break
            except subprocess.TimeoutExpired:
                continue

        return self._format_output(proc.returncode, stdout, stderr)

    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.communicate(timeout=2)
        except Exception:
            pass

    @staticmethod
    def _format_output(returncode: int, stdout: str, stderr: str) -> str:
        parts: list[str] = []
        if stdout:
            parts.append(stdout.rstrip())
        if stderr:
            if parts:
                parts.append("--- stderr ---")
            parts.append(stderr.rstrip())
        if not parts:
            parts.append("(sin salida)")
        body = "\n".join(parts)
        if returncode != 0:
            body += f"\n[exit code: {returncode}]"

        encoded = body.encode("utf-8", errors="replace")
        if len(encoded) > MAX_OUTPUT_BYTES:
            truncated = encoded[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
            body = truncated + f"\n... (salida truncada a {MAX_OUTPUT_BYTES} bytes)"
        return body

    @staticmethod
    def _environment() -> dict[str, str]:
        """Entorno mínimo. No heredamos variables del proceso padre más allá
        de las imprescindibles para que un binario encuentre su intérprete
        y sus dependencias básicas."""
        allowed = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SHELL", "USER")
        env: dict[str, str] = {}
        for key in allowed:
            value = os.environ.get(key)
            if value:
                env[key] = value
        # Aseguramos que git y otros programas no abran editores interactivos.
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["PAGER"] = "cat"
        env["GIT_PAGER"] = "cat"
        return env
