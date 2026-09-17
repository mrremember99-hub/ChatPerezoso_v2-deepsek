"""Búsqueda de texto recursiva sobre el workspace.

Usa ``regex`` en lugar de ``re`` para poder aplicar timeout por línea y
evitar ReDoS. El resto de límites (tamaño de archivo, número de matches,
profundidad) se mantienen.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

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

        try:
            pattern = regex.compile(
                query,
                regex.IGNORECASE if not case_sensitive else 0,
            )
        except regex.error as exc:
            raise SearchError(f"Expresión regular inválida: {exc}") from exc

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
                    try:
                        if pattern.search(
                            line, timeout=_REGEX_LINE_TIMEOUT_SECONDS
                        ):
                            display = line
                            if len(display) > _MAX_LINE_LENGTH:
                                display = display[:_MAX_LINE_LENGTH] + "…"
                            hits.append((line_number, display))
                            if len(hits) >= limit:
                                break
                    except TimeoutError:
                        # Regex patológico: se salta la línea y se sigue.
                        continue
        except UnicodeDecodeError:
            return []
        return hits