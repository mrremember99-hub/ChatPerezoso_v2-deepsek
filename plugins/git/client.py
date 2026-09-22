"""Cliente de subprocess para git. Sin dependencias externas.

Todas las operaciones son de solo lectura. Si en el futuro se añade alguna
que modifique el repositorio (commit, push, checkout, etc.), deberá pasar
por la confirmación explícita del usuario.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    """Error controlado del plugin Git."""


# Límite de salida de un comando git antes de truncar. Un `git diff`
# o `git show` de un repo grande puede devolver MBs. Sin este tope, el
# resultado entra íntegro en el historial de tool calls y rompe el
# contexto del modelo en la siguiente ronda. Es coherente con el
# límite de 200 KB del plugin shell.
_MAX_OUTPUT_BYTES = 200_000


# Caracteres válidos en una referencia de git (hash, rama, tag, HEAD~1,
# origin/main...). Debe empezar por alfanumérico para rechazar flags como
# "--output=/ruta", "--exec=...", etc.
_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/~^@{}-]{0,255}$")


class GitClient:
    def __init__(self, cwd: str | Path):
        self.cwd = Path(cwd)

    def is_repo(self) -> bool:
        try:
            self._run("rev-parse", "--git-dir")
            return True
        except GitError:
            return False

    def status(self) -> str:
        output = self._run("status", "--porcelain=v1", "--branch")
        return output.rstrip() or "(sin cambios)"

    def diff(self, *, staged: bool = False, path: str | None = None) -> str:
        args = ["diff"]
        if staged:
            args.append("--staged")
        if path:
            # El path se pasa tras -- para que git no lo interprete como flag.
            args.extend(["--", path])
        output = self._run(*args)
        return output.rstrip() or "(sin diferencias)"

    def log(self, *, limit: int = 10) -> str:
        # Un repo sin commits es un caso legítimo, no un error. Detectarlo
        # con rev-parse es determinista y no depende del idioma del mensaje
        # de git (que cambia con LANG).
        if not self._has_commits():
            return "(el repositorio no tiene commits todavía)"
        output = self._run(
            "log",
            f"-n{limit}",
            "--pretty=format:%h %ad %s",
            "--date=short",
        )
        return output.rstrip() or "(sin commits)"

    def show(self, ref: str = "HEAD", *, stat: bool = False) -> str:
        """Muestra un commit completo o un archivo de un commit.

        ``ref`` se valida estrictamente contra ``_REF_PATTERN``. Un valor
        que empiece por ``-`` (por ejemplo ``--output=/tmp/x``) es
        rechazado: sin esta validación, un ref malicioso podría hacer que
        git escriba archivos fuera del workspace o ejecute comandos.
        """
        if not ref or not ref.strip():
            ref = "HEAD"
        ref = ref.strip()
        if not _REF_PATTERN.match(ref):
            raise GitError(
                f"Referencia de git inválida: {ref!r}. "
                "Debe ser un hash, rama, tag o HEAD con modificadores."
            )
        args = ["show", ref]
        if stat:
            args.append("--stat")
        else:
            args.append("--format=fuller")
            args.append("-p")
        output = self._run(*args)
        return output.rstrip() or "(sin contenido)"

    def _has_commits(self) -> bool:
        try:
            self._run("rev-parse", "--verify", "HEAD")
            return True
        except GitError:
            return False

    def _run(self, *args: str, timeout: float = 15.0) -> str:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise GitError(
                "El comando «git» no está instalado o no está en el PATH."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise GitError(
                f"«git {args[0] if args else ''}» tardó demasiado ({timeout:.0f}s)."
            ) from exc

        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip()
            raise GitError(message or f"git {' '.join(args)} falló.")

        output = result.stdout
        encoded = output.encode("utf-8", errors="replace")
        if len(encoded) > _MAX_OUTPUT_BYTES:
            truncated = encoded[:_MAX_OUTPUT_BYTES].decode(
                "utf-8", errors="replace"
            )
            return (
                truncated
                + f"\n... (salida truncada a {_MAX_OUTPUT_BYTES} bytes)"
            )
        return output
