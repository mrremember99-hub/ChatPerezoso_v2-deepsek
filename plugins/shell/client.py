"""Ejecución de comandos simples sobre el workspace.

Reglas de seguridad (8 capas):
1. Sin shell=True: shlex.split + lista de argumentos.
2. Sin metacaracteres de shell.
3. Sin sudo, su, ni programas destructivos.
4. cwd dentro del workspace.
5. Timeout por defecto 30 s, máximo 300 s.
6. Salida limitada a 200 KB.
7. Entorno mínimo.
8. Confirmación del usuario obligatoria.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path

import psutil


class ShellError(RuntimeError):
    """Error controlado del plugin shell."""


DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 300
MAX_OUTPUT_BYTES = 200_000

_FORBIDDEN_METACHARS: tuple[str, ...] = (
    "&&", "||", ";", "|", ">", "<", "`", "$(", "${",
)

_FORBIDDEN_PROGRAMS = frozenset({
    "sudo", "su", "doas",
    "dd", "mkfs", "mkfs.ext4", "mkfs.xfs", "fdisk", "parted",
    "shutdown", "reboot", "halt", "poweroff", "init",
    "kill", "killall", "pkill",
    "iptables", "ufw", "firewall-cmd",
    "systemctl", "service", "launchctl",
    "crontab", "at", "batch",
})

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
     "invoca un shell con código inline"),
    (re.compile(r"\bpython3?\s+(-c|-m\s+pip)\b", re.IGNORECASE),
     "ejecuta Python con código inline o pip"),
    (re.compile(r"\b(perl|ruby|node|php)\s+-e\b", re.IGNORECASE),
     "invoca un intérprete con código inline"),
)


def analyze_risk(command: str) -> list[str]:
    return [reason for pattern, reason in _RISKY_PATTERNS if pattern.search(command)]


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
        program = parts[0].rsplit("/", 1)[-1]
        if program in _FORBIDDEN_PROGRAMS:
            raise ShellError(f"El comando «{program}» está bloqueado por seguridad.")

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
            # Sesión propia en POSIX: podemos matar todo el grupo con
            # psutil. En Windows, psutil también.
            start_new_session=(os.name == "posix"),
        )

        watcher = None
        if cancel_event is not None:
            watcher = threading.Thread(
                target=self._watch_cancel,
                args=(proc, cancel_event),
                daemon=True,
            )
            watcher.start()

        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._terminate(proc)
            return f"ERROR: el comando superó el timeout de {timeout} s."

        if cancel_event is not None and cancel_event.is_set():
            self._terminate(proc)
            return "OPERACIÓN CANCELADA POR EL USUARIO: el comando fue interrumpido."

        return self._format_output(proc.returncode, stdout, stderr)

    @staticmethod
    def _watch_cancel(proc: subprocess.Popen, cancel_event: threading.Event) -> None:
        while proc.poll() is None:
            if cancel_event.is_set():
                ShellClient._terminate(proc)
                return
            time.sleep(0.1)

    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        """Mata el proceso y TODOS sus hijos.

        ``proc.kill()`` solo mata el proceso directo. Con psutil recorremos
        el árbol completo, de hoja a raíz.
        """
        try:
            parent = psutil.Process(proc.pid)
        except psutil.NoSuchProcess:
            return
        children: list[psutil.Process] = []
        try:
            children = parent.children(recursive=True)
        except psutil.NoSuchProcess:
            pass
        for child in reversed(children):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        try:
            parent.kill()
        except psutil.NoSuchProcess:
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
        allowed = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SHELL", "USER")
        env: dict[str, str] = {}
        for key in allowed:
            value = os.environ.get(key)
            if value:
                env[key] = value
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["PAGER"] = "cat"
        env["GIT_PAGER"] = "cat"
        return env