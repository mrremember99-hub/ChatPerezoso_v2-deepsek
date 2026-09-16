from __future__ import annotations

from pathlib import Path

MAX_READ_BYTES = 200_000
MAX_WRITE_BYTES = 1_000_000
MAX_LIST_ITEMS = 200


class WorkspaceError(Exception):
    pass


class Workspace:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        if not self.root.exists():
            self.root.mkdir(parents=True)
        if not self.root.is_dir():
            raise WorkspaceError("El workspace no es una carpeta.")

    def _path(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceError("Ruta fuera del workspace.") from exc
        return candidate

    def list_dir(self, path: str = ".") -> str:
        folder = self._path(path)
        if not folder.is_dir():
            raise WorkspaceError(f"No es una carpeta: {path}")
        entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        if len(entries) > MAX_LIST_ITEMS:
            entries = entries[:MAX_LIST_ITEMS]
        lines = []
        for item in entries:
            rel = item.relative_to(self.root)
            lines.append(f"{'[DIR] ' if item.is_dir() else '[FILE]'}{rel}")
        return "\n".join(lines) or "(carpeta vacía)"

    def read_file(self, path: str) -> str:
        file = self._path(path)
        if not file.is_file():
            raise WorkspaceError(f"No es un archivo: {path}")
        if file.stat().st_size > MAX_READ_BYTES:
            raise WorkspaceError(f"Archivo demasiado grande para leer ({MAX_READ_BYTES} bytes máximo).")
        try:
            return file.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceError("El archivo no parece ser texto UTF-8.") from exc

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
