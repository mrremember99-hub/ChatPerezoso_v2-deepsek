from __future__ import annotations

from pathlib import Path

MAX_READ_BYTES = 200_000
MAX_WRITE_BYTES = 1_000_000
MAX_LIST_ITEMS = 200
MAX_RECURSIVE_DEPTH = 6


class WorkspaceError(Exception):
    pass


class Workspace:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        if not self.root.exists():
            self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise WorkspaceError("El workspace no es una carpeta.")

    def _path(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceError("Ruta fuera del workspace.") from exc
        return candidate

    # -- listar --------------------------------------------------------------

    def list_dir(self, path: str = ".", recursive: bool = False) -> str:
        folder = self._path(path)
        if not folder.is_dir():
            raise WorkspaceError(f"No es una carpeta: {path}")
        if recursive:
            return self._list_recursive(folder)
        return self._list_flat(folder)

    def _list_flat(self, folder: Path) -> str:
        entries = sorted(
            folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
        )
        total = len(entries)
        truncated = total > MAX_LIST_ITEMS
        if truncated:
            entries = entries[:MAX_LIST_ITEMS]
        lines = []
        for item in entries:
            rel = item.relative_to(self.root)
            lines.append(f"{'[DIR] ' if item.is_dir() else '[FILE]'}{rel}")
        if not lines:
            return "(carpeta vacía)"
        if truncated:
            lines.append(f"... (mostrando {MAX_LIST_ITEMS} de {total} entradas)")
        return "\n".join(lines)

    def _list_recursive(self, folder: Path) -> str:
        """Árbol compacto con indentación. Limita profundidad y total."""
        lines: list[str] = []
        truncated = False
        base_depth = len(folder.relative_to(self.root).parts)

        def walk(current: Path, depth: int) -> None:
            nonlocal truncated
            if truncated:
                return
            if depth - base_depth > MAX_RECURSIVE_DEPTH:
                lines.append(f"{'  ' * depth}...")
                return
            try:
                entries = sorted(
                    current.iterdir(),
                    key=lambda p: (not p.is_dir(), p.name.lower()),
                )
            except OSError:
                return
            for item in entries:
                if len(lines) >= MAX_LIST_ITEMS:
                    truncated = True
                    return
                rel = item.relative_to(folder)
                indent = "  " * (depth - base_depth)
                marker = "📁 " if item.is_dir() else "   "
                lines.append(f"{indent}{marker}{rel.name}")
                if item.is_dir() and not item.is_symlink():
                    walk(item, depth + 1)

        walk(folder, base_depth)
        if not lines:
            return "(carpeta vacía)"
        if truncated:
            lines.append(f"... (truncado a {MAX_LIST_ITEMS} entradas)")
        return "\n".join(lines)

    # -- leer ----------------------------------------------------------------

    def read_file(
        self,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        file = self._path(path)
        if not file.is_file():
            raise WorkspaceError(f"No es un archivo: {path}")
        if file.stat().st_size > MAX_READ_BYTES:
            raise WorkspaceError(
                f"Archivo demasiado grande para leer ({MAX_READ_BYTES} bytes máximo)."
            )
        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceError("El archivo no parece ser texto UTF-8.") from exc

        if start_line is None and end_line is None:
            return text

        return self._slice_lines(text, start_line, end_line)

    @staticmethod
    def _slice_lines(
        text: str,
        start_line: int | None,
        end_line: int | None,
    ) -> str:
        lines = text.splitlines(keepends=True)
        total = len(lines)

        start = max(1, int(start_line)) if start_line is not None else 1
        end = min(total, int(end_line)) if end_line is not None else total
        if end < start:
            raise WorkspaceError(
                f"Rango inválido: start_line={start_line} end_line={end_line}"
            )

        selected = lines[start - 1 : end]
        header = f"[líneas {start}-{end} de {total}]\n"
        return header + "".join(selected)

    # -- escribir ------------------------------------------------------------

    def create_file(self, path: str, content: str = "") -> str:
        file = self._path(path)
        if file.exists():
            raise WorkspaceError(f"Ya existe: {path}")
        data = content.encode("utf-8")
        if len(data) > MAX_WRITE_BYTES:
            raise WorkspaceError("Contenido demasiado grande.")
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
        return f"Archivo creado: {file.relative_to(self.root)}"

    def create_folder(self, path: str) -> str:
        folder = self._path(path)
        if folder.exists():
            raise WorkspaceError(f"Ya existe: {path}")
        folder.mkdir(parents=True)
        return f"Carpeta creada: {folder.relative_to(self.root)}"

    def write_file(self, path: str, content: str) -> str:
        file = self._path(path)
        if file.is_dir():
            raise WorkspaceError(f"Es una carpeta, no un archivo: {path}")
        data = content.encode("utf-8")
        if len(data) > MAX_WRITE_BYTES:
            raise WorkspaceError("Contenido demasiado grande.")
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
        return f"Archivo escrito: {file.relative_to(self.root)}"

    def delete_file(self, path: str) -> str:
        file = self._path(path)
        if not file.is_file():
            raise WorkspaceError(f"No es un archivo: {path}")
        file.unlink()
        return f"Archivo borrado: {file.relative_to(self.root)}"
