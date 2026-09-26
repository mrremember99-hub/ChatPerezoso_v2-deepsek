from pathlib import Path

import pytest

from core.workspace import Workspace, WorkspaceError


def test_list_and_read(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hola", encoding="utf-8")
    ws = Workspace(tmp_path)
    assert "a.txt" in ws.list_dir()
    assert ws.read_file("a.txt") == "hola"


def test_path_traversal_is_blocked(tmp_path: Path):
    ws = Workspace(tmp_path)
    with pytest.raises(WorkspaceError):
        ws.read_file("../fuera.txt")


def test_list_and_read_stay_inside_workspace(tmp_path: Path):
    outside = tmp_path.parent / "fuera.txt"
    outside.write_text("secreto", encoding="utf-8")
    ws = Workspace(tmp_path)
    with pytest.raises(WorkspaceError):
        ws.read_file("../fuera.txt")


def test_create_write_delete(tmp_path: Path):
    ws = Workspace(tmp_path)
    assert "creado" in ws.create_file("x.txt", "uno")
    assert ws.read_file("x.txt") == "uno"
    assert "escrito" in ws.write_file("x.txt", "dos")
    assert ws.read_file("x.txt") == "dos"
    assert "borrado" in ws.delete_file("x.txt")


def test_create_folder(tmp_path: Path):
    ws = Workspace(tmp_path)
    assert "creada" in ws.create_folder("fotos")
    assert (tmp_path / "fotos").is_dir()
    assert "[DIR]" in ws.list_dir()


def test_create_folder_does_not_overwrite(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.create_folder("fotos")
    with pytest.raises(WorkspaceError):
        ws.create_folder("fotos")


def test_create_folder_creates_missing_parents(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.create_folder("a/b/c")
    assert (tmp_path / "a" / "b" / "c").is_dir()


def test_create_folder_stays_inside_workspace(tmp_path: Path):
    ws = Workspace(tmp_path)
    with pytest.raises(WorkspaceError):
        ws.create_folder("../fuera")


def test_write_operations_stay_inside_workspace(tmp_path: Path):
    ws = Workspace(tmp_path)
    with pytest.raises(WorkspaceError):
        ws.create_file("../fuera.txt", "no")
    with pytest.raises(WorkspaceError):
        ws.write_file("../fuera.txt", "no")


def test_create_does_not_overwrite(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.create_file("x.txt", "uno")
    with pytest.raises(WorkspaceError):
        ws.create_file("x.txt", "dos")


def test_write_limit(tmp_path: Path):
    ws = Workspace(tmp_path)
    with pytest.raises(WorkspaceError):
        ws.write_file("x.txt", "x" * (1_000_001))


# -- rango de líneas ---------------------------------------------------------

def test_read_file_with_range(tmp_path):
    ws = Workspace(tmp_path)
    (tmp_path / "a.txt").write_text("linea1\nlinea2\nlinea3\nlinea4\n", encoding="utf-8")
    result = ws.read_file("a.txt", start_line=2, end_line=3)
    assert "[líneas 2-3 de 4]" in result
    assert "linea2" in result
    assert "linea3" in result
    assert "linea1" not in result
    assert "linea4" not in result


def test_read_file_with_only_start_line(tmp_path):
    ws = Workspace(tmp_path)
    (tmp_path / "a.txt").write_text("a\nb\nc\n", encoding="utf-8")
    result = ws.read_file("a.txt", start_line=2)
    assert "b" in result
    assert "c" in result
    assert "[líneas 2-3 de 3]" in result


def test_read_file_with_only_end_line(tmp_path):
    ws = Workspace(tmp_path)
    (tmp_path / "a.txt").write_text("a\nb\nc\nd\n", encoding="utf-8")
    result = ws.read_file("a.txt", end_line=2)
    assert "a" in result
    assert "b" in result
    assert "c" not in result


def test_read_file_invalid_range(tmp_path):
    ws = Workspace(tmp_path)
    (tmp_path / "a.txt").write_text("a\nb\n", encoding="utf-8")
    with pytest.raises(WorkspaceError):
        ws.read_file("a.txt", start_line=5, end_line=2)


def test_read_file_range_beyond_eof_is_clamped(tmp_path):
    ws = Workspace(tmp_path)
    (tmp_path / "a.txt").write_text("a\nb\nc\n", encoding="utf-8")
    result = ws.read_file("a.txt", start_line=2, end_line=999)
    assert "[líneas 2-3 de 3]" in result


# -- listado recursivo -------------------------------------------------------

def test_list_dir_recursive_shows_tree(tmp_path):
    ws = Workspace(tmp_path)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "archivo.txt").write_text("x", encoding="utf-8")
    (tmp_path / "raiz.txt").write_text("y", encoding="utf-8")

    result = ws.list_dir(".", recursive=True)
    assert "sub" in result
    assert "archivo.txt" in result
    assert "raiz.txt" in result


def test_list_dir_flat_ignores_nested(tmp_path):
    ws = Workspace(tmp_path)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "archivo.txt").write_text("x", encoding="utf-8")

    result = ws.list_dir(".")
    assert "sub" in result
    assert "archivo.txt" not in result


def test_list_dir_recursive_respects_limit(tmp_path):
    ws = Workspace(tmp_path)
    for i in range(50):
        (tmp_path / f"f{i:03d}.txt").write_text("x", encoding="utf-8")
    result = ws.list_dir(".", recursive=True)
    assert "truncado" not in result or len(result.splitlines()) <= 201


# ── Seguridad: symlinks ────────────────────────────────────────────────

def test_symlink_to_outside_is_rejected(tmp_path):
    """Un symlink dentro del workspace apuntando fuera debe ser rechazado.

    `Workspace._path` usa `.resolve()` + `relative_to()`. Al resolver el
    symlink, el path resultante cae fuera del workspace y `relative_to`
    falla. Este test lo verifica explícitamente: si alguien "optimiza"
    quitando el `.resolve()`, este test lo pilla.
    """
    import os
    import pytest
    from core.workspace import Workspace, WorkspaceError

    outside = tmp_path / "fuera"
    outside.mkdir()
    secret = outside / "secreto.txt"
    secret.write_text("datos sensibles", encoding="utf-8")

    workspace_dir = tmp_path / "ws"
    workspace_dir.mkdir()

    link = workspace_dir / "atajo.txt"
    try:
        os.symlink(secret, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks no soportados en este sistema")

    ws = Workspace(workspace_dir)

    # Leer a través del symlink debe fallar
    with pytest.raises(WorkspaceError):
        ws.read_file("atajo.txt")


def test_symlink_to_outside_directory_is_rejected(tmp_path):
    """Un symlink a un directorio externo no debe poder listarse."""
    import os
    import pytest
    from core.workspace import Workspace, WorkspaceError

    outside = tmp_path / "fuera"
    outside.mkdir()
    (outside / "secreto.txt").write_text("datos", encoding="utf-8")

    workspace_dir = tmp_path / "ws"
    workspace_dir.mkdir()

    link = workspace_dir / "atajo_dir"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks no soportados en este sistema")

    ws = Workspace(workspace_dir)

    with pytest.raises(WorkspaceError):
        ws.list_dir("atajo_dir")


def test_symlink_inside_workspace_is_allowed(tmp_path):
    """Un symlink dentro del workspace debe funcionar con normalidad."""
    import os
    import pytest
    from core.workspace import Workspace

    workspace_dir = tmp_path / "ws"
    workspace_dir.mkdir()
    real = workspace_dir / "real.txt"
    real.write_text("contenido", encoding="utf-8")

    link = workspace_dir / "atajo.txt"
    try:
        os.symlink(real, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks no soportados en este sistema")

    ws = Workspace(workspace_dir)
    content = ws.read_file("atajo.txt")
    assert "contenido" in content


# -- N2 (auditoría 2026-09-26): rutas absolutas se reinterpretan --------

def test_list_dir_acepta_slash_como_raiz(tmp_path):
    """/ debe reinterpretarse como la raíz del workspace, no error."""
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("hola", encoding="utf-8")
    (root / "sub").mkdir()
    ws = Workspace(root)

    # Antes: WorkspaceError("Ruta fuera del workspace.")
    # Ahora: listado normal de la raíz.
    listing = ws.list_dir("/")
    assert "a.txt" in listing
    assert "sub" in listing


def test_read_file_acepta_slash_como_raiz(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "x.txt").write_text("hola", encoding="utf-8")
    ws = Workspace(root)

    assert ws.read_file("/x.txt") == "hola"


def test_slash_solo_se_reinterpreta_si_no_escapa(tmp_path):
    """Un "/" absoluto se acepta; "../../etc/passwd" sigue bloqueado."""
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("ok", encoding="utf-8")
    ws = Workspace(root)

    # Ruta absoluta reinterpretable: OK.
    assert "a.txt" in ws.list_dir("/")

    # Escape real: sigue bloqueado.
    import pytest
    with pytest.raises(WorkspaceError):
        ws.read_file("../../etc/passwd")
    with pytest.raises(WorkspaceError):
        ws.read_file("/../etc/passwd")


# -- edit_file (feature 2026-09-26) -------------------------------------

def test_edit_file_reemplaza_fragmento(tmp_path):
    (tmp_path / "a.py").write_text(
        "def foo():\n    return 1\n", encoding="utf-8"
    )
    ws = Workspace(tmp_path)
    result = ws.edit_file("a.py", "return 1", "return 2")
    assert "editado" in result.lower()
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == (
        "def foo():\n    return 2\n"
    )


def test_edit_file_falla_si_no_aparece(tmp_path):
    (tmp_path / "a.py").write_text("hola", encoding="utf-8")
    ws = Workspace(tmp_path)
    import pytest
    with pytest.raises(WorkspaceError):
        ws.edit_file("a.py", "no existe", "x")


def test_edit_file_falla_si_es_ambiguo(tmp_path):
    (tmp_path / "a.py").write_text("foo foo foo", encoding="utf-8")
    ws = Workspace(tmp_path)
    import pytest
    with pytest.raises(WorkspaceError, match="3 veces"):
        ws.edit_file("a.py", "foo", "bar")


def test_edit_file_replace_all(tmp_path):
    (tmp_path / "a.py").write_text("foo foo foo", encoding="utf-8")
    ws = Workspace(tmp_path)
    ws.edit_file("a.py", "foo", "bar", replace_all=True)
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "bar bar bar"


def test_edit_file_rechaza_old_string_vacio(tmp_path):
    (tmp_path / "a.py").write_text("hola", encoding="utf-8")
    ws = Workspace(tmp_path)
    import pytest
    with pytest.raises(WorkspaceError):
        ws.edit_file("a.py", "", "x")


def test_edit_file_rechaza_identicos(tmp_path):
    (tmp_path / "a.py").write_text("hola", encoding="utf-8")
    ws = Workspace(tmp_path)
    import pytest
    with pytest.raises(WorkspaceError):
        ws.edit_file("a.py", "hola", "hola")


def test_edit_file_permite_borrar_fragmento(tmp_path):
    (tmp_path / "a.py").write_text("linea1\nlinea2\nlinea3\n", encoding="utf-8")
    ws = Workspace(tmp_path)
    ws.edit_file("a.py", "linea2\n", "")
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "linea1\nlinea3\n"


# -- insert_in_file (feature 2026-09-26) --------------------------------

def test_insert_in_file_al_final(tmp_path):
    (tmp_path / "a.py").write_text("uno\ndos\n", encoding="utf-8")
    ws = Workspace(tmp_path)
    result = ws.insert_in_file("a.py", 2, "tres")
    assert "insertado" in result.lower()
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == (
        "uno\ndos\ntres\n"
    )


def test_insert_in_file_al_inicio(tmp_path):
    (tmp_path / "a.py").write_text("original\n", encoding="utf-8")
    ws = Workspace(tmp_path)
    ws.insert_in_file("a.py", 0, "# header")
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == (
        "# header\noriginal\n"
    )


def test_insert_in_file_tras_linea_media(tmp_path):
    (tmp_path / "a.py").write_text("1\n2\n3\n4\n", encoding="utf-8")
    ws = Workspace(tmp_path)
    ws.insert_in_file("a.py", 2, "X")
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == (
        "1\n2\nX\n3\n4\n"
    )


def test_insert_in_file_fuera_de_rango(tmp_path):
    (tmp_path / "a.py").write_text("uno\ndos\n", encoding="utf-8")
    ws = Workspace(tmp_path)
    import pytest
    with pytest.raises(WorkspaceError, match="fuera de rango"):
        ws.insert_in_file("a.py", 99, "x")


def test_insert_in_file_negativo(tmp_path):
    (tmp_path / "a.py").write_text("uno\n", encoding="utf-8")
    ws = Workspace(tmp_path)
    import pytest
    with pytest.raises(WorkspaceError):
        ws.insert_in_file("a.py", -1, "x")


def test_insert_in_file_archivo_sin_salto_final(tmp_path):
    # Archivo no termina en \n: la insercion debe anadir uno.
    (tmp_path / "a.py").write_text("uno\ndos", encoding="utf-8")
    ws = Workspace(tmp_path)
    ws.insert_in_file("a.py", 2, "tres")
    text = (tmp_path / "a.py").read_text(encoding="utf-8")
    assert "tres" in text
    # No debe quedar "dostres" pegado.
    assert "dostres" not in text


# -- read_file numbered -------------------------------------------------

def test_read_file_numbered(tmp_path):
    (tmp_path / "a.txt").write_text("uno\ndos\ntres\n", encoding="utf-8")
    ws = Workspace(tmp_path)
    result = ws.read_file("a.txt", numbered=True)
    assert "1| uno" in result
    assert "2| dos" in result
    assert "3| tres" in result


def test_read_file_numbered_con_rango(tmp_path):
    (tmp_path / "a.txt").write_text(
        "uno\ndos\ntres\ncuatro\n", encoding="utf-8"
    )
    ws = Workspace(tmp_path)
    result = ws.read_file("a.txt", start_line=2, end_line=3, numbered=True)
    # Los numeros coinciden con las lineas reales.
    assert "2| dos" in result
    assert "3| tres" in result
    assert "1| " not in result
    assert "4| " not in result


def test_read_file_numbered_por_defecto_false(tmp_path):
    (tmp_path / "a.txt").write_text("uno\n", encoding="utf-8")
    ws = Workspace(tmp_path)
    result = ws.read_file("a.txt")
    assert result == "uno\n"
