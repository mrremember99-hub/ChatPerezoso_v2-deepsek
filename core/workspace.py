from __future__ import annotations

from pathlib import Path

MAX_READ_BYTES = 200_000
MAX_WRITE_BYTES = 1_000_000
MAX_LIST_ITEMS = 200
MAX_RECURSIVE_DEPTH = 6


# Directorios que no aportan al usuario al listar. Coincide con el
# conjunto del plugin search para que el listado y la busqueda
# ignoren lo mismo.
_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", "env", ".tox",
    "dist", "build", ".idea", ".vscode",
})


class WorkspaceError(Exception):
    pass


class Workspace:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        if not self.root.exists():
            self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise WorkspaceError(
                f"El workspace no es una carpeta: {self.root}"
            )

    def _path(self, relative: str) -> Path:
        # Modelos pequeños/medianos interpretan con frecuencia la raíz
        # del workspace como "/" del sistema de archivos (demostrado:
        # 5/7 modelos en docs/benchmark-2026-09-25.md devolvían path="/"
        # en listar_carpeta). Reinterpretar una ruta absoluta como
        # relativa a la raíz del workspace evita una ronda de tool
        # calling desperdiciada en el camino más común, sin relajar la
        # protección contra escapes reales: "../../etc/passwd" no
        # empieza por "/", así que sigue bloqueado por relative_to().
        if relative.startswith("/"):
            relative = relative.lstrip("/") or "."
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
            (p for p in folder.iterdir() if p.name not in _SKIP_DIRS),
            key=lambda p: (not p.is_dir(), p.name.lower()),
        )
        total = len(entries)
        truncated = total > MAX_LIST_ITEMS
        if truncated:
            entries = entries[:MAX_LIST_ITEMS]
        lines = []
        for item in entries:
            rel = item.relative_to(self.root)
            lines.append(f"{'[DIR] ' if item.is_dir() else '[FILE] '}{rel}")
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
                    (
                        p for p in current.iterdir()
                        if p.name not in _SKIP_DIRS
                    ),
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
        numbered: bool = False,
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
            sliced = text
            # Numeracion: si empezamos desde el principio, 1-based.
            first_line_no = 1
        else:
            sliced = self._slice_lines(text, start_line, end_line)
            # El header [lineas N-M de T] tiene la info; si no,
            # calculamos first_line_no desde start_line.
            first_line_no = max(1, int(start_line or 1))

        if not numbered:
            return sliced
        return self._number_lines(sliced, first_line_no)

    @staticmethod
    def _number_lines(text: str, first_line_no: int = 1) -> str:
        """Prefija cada linea con su numero (formato ``N| texto``).

        Patron Anthropic/Cursor/DeepSeek. Necesario para que el
        modelo pueda referenciar lineas concretas al usar
        insertar_en_archivo.

        Si la primera linea es el header de `_slice_lines`
        (\"[lineas N-M de T]\"), se deja SIN numerar y se empieza
        la numeracion desde la siguiente linea con first_line_no.
        """
        if not text:
            return text
        lines = text.splitlines(keepends=True)
        out: list[str] = []
        start_idx = 0
        if lines and lines[0].lstrip().startswith("[líneas"):
            out.append(lines[0])
            start_idx = 1
        for i, raw in enumerate(lines[start_idx:]):
            n = first_line_no + i
            out.append(f"{n}| {raw}")
        return "".join(out)

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

    def insert_in_file(
        self,
        path: str,
        insert_line: int,
        text: str,
    ) -> str:
        """Inserta `text` tras la linea `insert_line` (1-based).

        insert_line=0 -> al inicio del archivo.
        insert_line=N -> tras la linea N (1-indexada).
        insert_line=len(lines) -> al final.

        Patron Anthropic text editor `insert`. Desbloquea
        inserciones puntuales sin leer el archivo entero.
        """
        if not isinstance(text, str):
            raise WorkspaceError("text debe ser texto.")
        try:
            line_no = int(insert_line)
        except (TypeError, ValueError) as exc:
            raise WorkspaceError(
                "insert_line debe ser un numero entero."
            ) from exc
        if line_no < 0:
            raise WorkspaceError(
                "insert_line no puede ser negativo."
            )

        file = self._path(path)
        if not file.is_file():
            raise WorkspaceError(f"No es un archivo: {path}")
        if file.stat().st_size > MAX_READ_BYTES:
            raise WorkspaceError(
                f"Archivo demasiado grande para insertar "
                f"({MAX_READ_BYTES} bytes maximo)."
            )
        try:
            original = file.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceError(
                "El archivo no parece ser texto UTF-8."
            ) from exc

        # Partimos respetando el final de linea original.
        lines = original.splitlines(keepends=True)
        total = len(lines)
        if line_no > total:
            raise WorkspaceError(
                f"insert_line={line_no} fuera de rango "
                f"(archivo tiene {total} lineas; usa {total} para "
                "insertar al final)."
            )

        # Normalizar texto: siempre con salto de linea final.
        # Evita crear lineas pegadas sin querer.
        fragment = text if text.endswith("\n") else text + "\n"
        # Si el archivo no termina en salto, la linea N no lo tiene.
        # En ese caso metemos un \n extra antes para no concatenar.
        if line_no > 0 and line_no <= total:
            last = lines[line_no - 1]
            if not last.endswith("\n"):
                fragment = "\n" + fragment

        new_lines = (
            lines[:line_no] + [fragment] + lines[line_no:]
        )
        new_text = "".join(new_lines)
        data = new_text.encode("utf-8")
        if len(data) > MAX_WRITE_BYTES:
            raise WorkspaceError(
                "Resultado demasiado grande tras la insercion."
            )
        file.write_bytes(data)
        return (
            f"Texto insertado tras linea {line_no} en "
            f"{file.relative_to(self.root)}"
        )

    def create_file(self, path: str, content: str = "") -> str:
        file = self._path(path)
        if file.exists():
            # El error es instructivo a propósito: los modelos que
            # reciben "Ya existe: X" sin más no saben que deben usar
            # escribir_archivo. El mensaje les guía a la herramienta
            # correcta para no terminar abandonando la tarea.
            raise WorkspaceError(
                f"Ya existe: {path}. No uses crear_archivo para "
                f"sobrescribirlo. Usa escribir_archivo si quieres "
                f"reemplazar su contenido, o elige otro nombre."
            )
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

    def edit_file(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> str:
        """Reemplaza un fragmento exacto dentro de un archivo.

        `old_string` debe aparecer exactamente una vez, salvo que
        `replace_all=True`. Falla si no aparece o si aparece varias
        veces y no se pidio reemplazo global — patron str_replace
        (Claude Text Editor, Aider, OpenCode).
        """
        if not isinstance(old_string, str) or not old_string:
            raise WorkspaceError("old_string no puede estar vacio.")
        if not isinstance(new_string, str):
            raise WorkspaceError("new_string debe ser texto.")
        if old_string == new_string:
            raise WorkspaceError(
                "old_string y new_string son identicos, nada que hacer."
            )
        file = self._path(path)
        if not file.is_file():
            raise WorkspaceError(f"No es un archivo: {path}")
        if file.stat().st_size > MAX_READ_BYTES:
            raise WorkspaceError(
                f"Archivo demasiado grande para editar "
                f"({MAX_READ_BYTES} bytes maximo)."
            )
        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceError(
                "El archivo no parece ser texto UTF-8."
            ) from exc

        count = text.count(old_string)
        if count == 0:
            raise WorkspaceError(
                f"old_string no encontrado en {path}. Copia el "
                "fragmento exacto (incluye indentacion y saltos "
                "de linea)."
            )
        if count > 1 and not replace_all:
            raise WorkspaceError(
                f"old_string aparece {count} veces en {path}. "
                "Amplia el contexto para que sea unico, o pasa "
                "replace_all=True para reemplazar todas."
            )

        if replace_all:
            new_text = text.replace(old_string, new_string)
        else:
            new_text = text.replace(old_string, new_string, 1)

        data = new_text.encode("utf-8")
        if len(data) > MAX_WRITE_BYTES:
            raise WorkspaceError(
                "Resultado demasiado grande tras la edicion."
            )
        file.write_bytes(data)
        return (
            f"Archivo editado: {file.relative_to(self.root)} "
            f"({count} reemplazo(s))"
        )

    def delete_file(self, path: str) -> str:
        file = self._path(path)
        if not file.is_file():
            raise WorkspaceError(f"No es un archivo: {path}")
        file.unlink()
        return f"Archivo borrado: {file.relative_to(self.root)}"
