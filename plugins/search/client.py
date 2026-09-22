"""Búsqueda de texto recursiva sobre el workspace.

Motor preferido: ``google-re2`` (tiempo lineal garantizado, sin
backtracking → sin ReDoS por diseño). Si no está instalado, se usa
``regex`` con timeout por línea como mitigación parcial.

RE2 no soporta lookahead/lookbehind, backreferences, ni ``\\Z`` / ``\\A``
de Python. Si un patrón usa alguna de esas features, se devuelve un
error claro al usuario. El resto del comportamiento (límites de tamaño,
número de matches, profundidad) es idéntico en ambos motores.
"""
from __future__ import annotations

import contextlib as _contextlib
import os
import threading as _threading
from pathlib import Path

# Motor preferido: re2 (lineal, sin ReDoS).
try:
    import re2 as _re2
    _RE2_AVAILABLE = True
except ImportError:
    _re2 = None
    _RE2_AVAILABLE = False

# Fallback siempre disponible: regex con timeout por línea.
import regex


class SearchError(RuntimeError):
    """Error controlado del plugin de búsqueda."""


_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", "env", ".tox", "dist", "build",
    ".idea", ".vscode",
})

_BINARY_EXTENSIONS = frozenset({
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".bin", ".o", ".a",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svgz",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".mp3", ".mp4", ".mov", ".avi", ".mkv", ".webm", ".wav", ".flac",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
})

_MAX_FILE_BYTES = 1_000_000
_MAX_MATCHES = 200
_MAX_LINE_LENGTH = 300
_MAX_FILES_SCANNED = 5000
_MAX_LINE_BYTES_FOR_REGEX = 10_000
_CANCEL_CHECK_INTERVAL = 200
_REGEX_LINE_TIMEOUT_SECONDS = 0.5





# Lock global para serializar las redirecciones de fd 2. La
# redireccion de file descriptors es a nivel de proceso: si dos
# hilos la hacen a la vez, uno restaura mientras el otro todavia
# cree que stderr esta silenciado.
_STDERR_SILENCE_LOCK = _threading.Lock()


@_contextlib.contextmanager
def _silenced_c_stderr():
    """Silencia fd 2 durante una llamada a una extensión C que escribe
    a stderr directamente.

    re2 usa absl logging, que escribe a fd 2 sin pasar por sys.stderr.
    Sin esto, un patrón inválido del usuario imprime dos líneas rojas
    de la librería C++ antes del mensaje limpio de la app.

    No usa sys.stderr porque esa es una capa Python; re2 escribe al
    descriptor de archivo subyacente. Hay que redirigir el fd real.
    """
    with _STDERR_SILENCE_LOCK:
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
        except OSError:
            # Si no podemos abrir /dev/null, seguimos sin silenciar.
            yield
            return
        try:
            old_stderr = os.dup(2)
            os.dup2(devnull, 2)
        except OSError:
            os.close(devnull)
            yield
            return
        try:
            yield
        finally:
            try:
                os.dup2(old_stderr, 2)
            finally:
                os.close(old_stderr)
                os.close(devnull)


def _compile_pattern(query: str, case_sensitive: bool):
    """Compila el patrón con el motor disponible.

    Devuelve ``(pattern, is_re2)``. Si re2 está disponible y el patrón
    es compatible, usa re2 (lineal, sin ReDoS). Si no, cae al motor
    ``regex`` con timeout por línea.
    """
    if _re2 is not None:
        options = _re2.Options()
        options.case_sensitive = case_sensitive
        try:
            # re2 (la parte C++) imprime a fd 2 los errores de parsing.
            # Silenciamos el fd durante la compilación para que el
            # usuario solo vea el mensaje limpio de la app.
            with _silenced_c_stderr():
                return _re2.compile(query, options), True
        except Exception as exc:
            # Cualquier fallo de compilación de re2 se traduce a
            # SearchError. El mensaje explica la limitación concreta.
            raise SearchError(
                f"Expresión regular no soportada por RE2: {exc}. "
                "RE2 no admite lookahead, lookbehind, backreferences, "
                "ni \\Z / \\A de Python. Reformula el patrón o "
                "desinstala google-re2 para usar el motor con "
                "backtracking (más lento, con timeout)."
            ) from exc
    try:
        return regex.compile(
            query,
            regex.IGNORECASE if not case_sensitive else 0,
        ), False
    except regex.error as exc:
        raise SearchError(f"Expresión regular inválida: {exc}") from exc


class SearchClient:
    def __init__(self, workspace_root: str | Path):
        self.root = Path(workspace_root).expanduser().resolve()

    # -- API pública ---------------------------------------------------------
    def search(
        self,
        query: str,
        *,
        path: str = ".",
        case_sensitive: bool = False,
        max_matches: int = 50,
        extensions: list[str] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> str:
        if not query:
            raise SearchError("La consulta de búsqueda no puede estar vacía.")
        base = self._resolve(path)
        if not base.exists():
            raise SearchError(f"La ruta no existe: {path}")
        if not base.is_dir():
            raise SearchError(f"La ruta no es una carpeta: {path}")

        max_matches = max(1, min(int(max_matches), _MAX_MATCHES))
        normalized_exts = self._normalize_extensions(extensions)

        pattern, is_re2 = _compile_pattern(query, case_sensitive)

        matches: list[str] = []
        scanned = 0
        truncated = False
        files_limit_hit = False

        for file in self._iter_files(base, normalized_exts):
            if cancel_event is not None and cancel_event.is_set():
                return "(búsqueda cancelada por el usuario)"
            if scanned >= _MAX_FILES_SCANNED:
                files_limit_hit = True
                break
            scanned += 1
            try:
                hits = self._search_file(
                    file,
                    pattern,
                    max_matches - len(matches),
                    is_re2=is_re2,
                    cancel_event=cancel_event,
                )
            except OSError:
                continue
            if cancel_event is not None and cancel_event.is_set():
                return "(búsqueda cancelada por el usuario)"
            for line_number, line in hits:
                rel = file.relative_to(self.root)
                matches.append(f"{rel}:{line_number}: {line}")
                if len(matches) >= max_matches:
                    truncated = True
                    break
            if truncated:
                break

        if not matches:
            if files_limit_hit:
                return (
                    f"(sin coincidencias en los primeros {_MAX_FILES_SCANNED} "
                    "archivos; acota la ruta con `path` si sabes dónde buscar)"
                )
            return f"(sin coincidencias en {scanned} archivo(s))"

        header = f"{len(matches)} coincidencia(s) en {scanned} archivo(s):"
        body = "\n".join(matches)
        if truncated:
            body += f"\n... (resultados truncados a {max_matches})"
        elif files_limit_hit:
            body += (
                f"\n... (se han recorrido solo {_MAX_FILES_SCANNED} archivos; "
                "acota la ruta con `path` para buscar en el resto)"
            )
        return f"{header}\n{body}"

    # -- interno -------------------------------------------------------------
    def _resolve(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise SearchError("Ruta fuera del workspace.") from exc
        return candidate

    def _iter_files(self, base: Path, extensions: frozenset[str]):
        for current_dir, dirs, files in os.walk(base):
            dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
            for name in sorted(files):
                file = Path(current_dir) / name
                suffix = file.suffix.lower()
                if suffix in _BINARY_EXTENSIONS:
                    continue
                if extensions and suffix not in extensions:
                    continue
                try:
                    if file.stat().st_size > _MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue
                yield file

    @staticmethod
    def _normalize_extensions(extensions: list[str] | None) -> frozenset[str]:
        if not extensions:
            return frozenset()
        result: set[str] = set()
        for raw in extensions:
            if not isinstance(raw, str):
                continue
            ext = raw.strip().lower()
            if not ext:
                continue
            if not ext.startswith("."):
                ext = "." + ext
            result.add(ext)
        return frozenset(result)

    @staticmethod
    def _search_file(
        file: Path,
        pattern,
        limit: int,
        *,
        is_re2: bool,
        cancel_event: threading.Event | None = None,
    ) -> list[tuple[int, str]]:
        hits: list[tuple[int, str]] = []
        try:
            with file.open("r", encoding="utf-8", errors="strict") as handle:
                for line_number, raw in enumerate(handle, start=1):
                    if (
                        cancel_event is not None
                        and line_number % _CANCEL_CHECK_INTERVAL == 0
                        and cancel_event.is_set()
                    ):
                        break
                    line = raw.rstrip("\n")
                    if len(line) > _MAX_LINE_BYTES_FOR_REGEX:
                        continue
                    if is_re2:
                        # re2 es lineal por diseño: no necesita timeout.
                        if not pattern.search(line):
                            continue
                    else:
                        # Fallback: regex con timeout por línea.
                        try:
                            if not pattern.search(
                                line, timeout=_REGEX_LINE_TIMEOUT_SECONDS
                            ):
                                continue
                        except TimeoutError:
                            # Patrón patológico: se salta la línea.
                            continue
                    display = line
                    if len(display) > _MAX_LINE_LENGTH:
                        display = display[:_MAX_LINE_LENGTH] + "…"
                    hits.append((line_number, display))
                    if len(hits) >= limit:
                        break
        except UnicodeDecodeError:
            return []
        return hits
