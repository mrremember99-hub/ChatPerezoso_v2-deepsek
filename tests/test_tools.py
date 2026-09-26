from core.tools import ToolRegistry
from core.workspace import Workspace


def test_registry_definitions(tmp_path):
    tools = ToolRegistry(Workspace(tmp_path)).definitions()
    names = {item["function"]["name"] for item in tools}
    assert names == {
        "listar_carpeta", "leer_archivo", "crear_archivo", "crear_carpeta",
        "escribir_archivo", "editar_archivo", "borrar_archivo",
    }


def test_registry_call(tmp_path):
    tools = ToolRegistry(Workspace(tmp_path))
    (tmp_path / "hola.txt").write_text("hola", encoding="utf-8")
    assert tools.call("leer_archivo", {"path": "hola.txt"}) == "hola"
    bloqueada = tools.call("crear_archivo", {"path": "nuevo.txt", "content": "uno"})
    assert "operación destructiva bloqueada" in bloqueada
    assert not (tmp_path / "nuevo.txt").exists()
    assert "creado" in tools.call(
        "crear_archivo", {"path": "nuevo.txt", "content": "uno"}, allow_destructive=True
    )
    assert (tmp_path / "nuevo.txt").read_text(encoding="utf-8") == "uno"
    bloqueada = tools.call("escribir_archivo", {"path": "nuevo.txt", "content": "dos"})
    assert "operación destructiva bloqueada" in bloqueada
    assert (tmp_path / "nuevo.txt").read_text(encoding="utf-8") == "uno"
    assert "escrito" in tools.call(
        "escribir_archivo", {"path": "nuevo.txt", "content": "dos"}, allow_destructive=True
    )
    assert (tmp_path / "nuevo.txt").read_text(encoding="utf-8") == "dos"
    blocked = tools.call("borrar_archivo", {"path": "nuevo.txt"})
    assert "operación destructiva bloqueada" in blocked
    assert (tmp_path / "nuevo.txt").exists()
    assert "borrado" in tools.call(
        "borrar_archivo", {"path": "nuevo.txt"}, allow_destructive=True
    )
    assert not (tmp_path / "nuevo.txt").exists()


def test_write_result_includes_content_verified_from_disk(tmp_path):
    """El resultado de crear/escribir debe incluir el contenido real leído
    del disco, no solo un mensaje de éxito — para que el modelo (y el
    diálogo/caja de resultado) puedan detectar un contenido equivocado."""
    tools = ToolRegistry(Workspace(tmp_path))

    created = tools.call(
        "crear_archivo",
        {"path": "a.txt", "content": "contenido de prueba"},
        allow_destructive=True,
    )
    assert "Contenido verificado en disco" in created
    assert "contenido de prueba" in created

    written = tools.call(
        "escribir_archivo",
        {"path": "a.txt", "content": "contenido nuevo"},
        allow_destructive=True,
    )
    assert "Contenido verificado en disco" in written
    assert "contenido nuevo" in written
    assert "contenido de prueba" not in written


def test_write_result_truncates_long_verified_content(tmp_path):
    tools = ToolRegistry(Workspace(tmp_path))
    long_content = "x" * 3000
    result = tools.call(
        "crear_archivo",
        {"path": "grande.txt", "content": long_content},
        allow_destructive=True,
    )
    assert "…(truncado)" in result
    assert len(result) < len(long_content) + 500


def test_registry_call_folder(tmp_path):
    tools = ToolRegistry(Workspace(tmp_path))
    bloqueada = tools.call("crear_carpeta", {"path": "fotos"})
    assert "operación destructiva bloqueada" in bloqueada
    assert not (tmp_path / "fotos").exists()
    assert "creada" in tools.call("crear_carpeta", {"path": "fotos"}, allow_destructive=True)
    assert (tmp_path / "fotos").is_dir()


def test_registry_contract_is_complete(tmp_path):
    tools = ToolRegistry(Workspace(tmp_path))
    definitions = tools.definitions()
    by_name = {item["function"]["name"]: item["function"] for item in definitions}

    assert by_name["listar_carpeta"]["parameters"]["required"] == []
    assert by_name["leer_archivo"]["parameters"]["required"] == ["path"]
    assert by_name["crear_archivo"]["parameters"]["required"] == ["path", "content"]
    assert by_name["crear_carpeta"]["parameters"]["required"] == ["path"]
    assert by_name["escribir_archivo"]["parameters"]["required"] == ["path", "content"]
    assert by_name["borrar_archivo"]["parameters"]["required"] == ["path"]


def test_registry_rejects_invalid_arguments(tmp_path):
    tools = ToolRegistry(Workspace(tmp_path))
    assert "falta el argumento requerido: path" in tools.call("leer_archivo", {})
    assert "falta el argumento requerido: content" in tools.call(
        "escribir_archivo", {"path": "x.txt"}
    )
    assert "argumento no permitido" in tools.call(
        "leer_archivo", {"path": "x.txt", "extra": "no"}
    )
    # El mensaje usa el tipo declarado en el schema ("string" en vez de
    # "texto"), que es más consistente con la validación por tipo.
    assert "debe ser string" in tools.call("leer_archivo", {"path": 123})


def test_registry_blocks_destructive_operation_by_default(tmp_path):
    tools = ToolRegistry(Workspace(tmp_path))
    (tmp_path / "borrar.txt").write_text("contenido", encoding="utf-8")

    result = tools.call("borrar_archivo", {"path": "borrar.txt"})

    assert result.startswith("ERROR: operación destructiva bloqueada")
    assert (tmp_path / "borrar.txt").exists()


def test_registry_requires_explicit_destructive_authorization(tmp_path):
    tools = ToolRegistry(Workspace(tmp_path))
    (tmp_path / "borrar.txt").write_text("contenido", encoding="utf-8")

    result = tools.call(
        "borrar_archivo", {"path": "borrar.txt"}, allow_destructive=True
    )

    assert "borrado" in result
    assert not (tmp_path / "borrar.txt").exists()


def test_registry_marks_every_workspace_write_for_confirmation(tmp_path):
    tools = ToolRegistry(Workspace(tmp_path))

    assert tools.requires_confirmation("crear_archivo")
    assert tools.requires_confirmation("crear_carpeta")
    assert tools.requires_confirmation("escribir_archivo")
    assert tools.requires_confirmation("borrar_archivo")
    assert not tools.requires_confirmation("listar_carpeta")
    assert not tools.requires_confirmation("leer_archivo")


# -- validación de tipos -----------------------------------------------------

def test_registry_rejects_wrong_type(tmp_path):
    tools = ToolRegistry(Workspace(tmp_path))
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    result = tools.call("leer_archivo", {"path": "a.txt", "start_line": "dos"})
    assert "debe ser integer" in result


def test_registry_rejects_bool_for_int(tmp_path):
    tools = ToolRegistry(Workspace(tmp_path))
    result = tools.call("leer_archivo", {"path": "a.txt", "start_line": True})
    assert "debe ser integer" in result


def test_registry_accepts_valid_bool(tmp_path):
    tools = ToolRegistry(Workspace(tmp_path))
    result = tools.call("listar_carpeta", {"path": ".", "recursive": True})
    assert "ERROR" not in result


# -- rango de líneas ---------------------------------------------------------

def test_registry_reads_line_range(tmp_path):
    tools = ToolRegistry(Workspace(tmp_path))
    (tmp_path / "a.txt").write_text("uno\ndos\ntres\n", encoding="utf-8")
    result = tools.call("leer_archivo", {"path": "a.txt", "start_line": 2})
    assert "dos" in result
    assert "tres" in result
    assert "uno" not in result


# -- listado recursivo -------------------------------------------------------

def test_registry_recursive_listing(tmp_path):
    tools = ToolRegistry(Workspace(tmp_path))
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.txt").write_text("hola", encoding="utf-8")
    result = tools.call("listar_carpeta", {"recursive": True})
    assert "x.txt" in result


def test_crear_archivo_sin_content_no_crea_archivo_vacio(tmp_path):
    """H7 del out(4): content es obligatorio.

    Antes el dispatch usaba `arguments.get("content", "")` y creaba
    archivos vacios cuando el modelo omitia content.
    """
    from core.tools import ToolRegistry
    from core.workspace import Workspace

    tools = ToolRegistry(Workspace(tmp_path))
    try:
        tools.call(
            "crear_archivo",
            {"path": "sin_content.txt"},
            allow_destructive=True,
        )
    except (KeyError, Exception):
        pass  # esperado: KeyError -> capturada por ToolRegistry.call
    # Pase lo que pase, no se crea archivo vacio.
    assert not (tmp_path / "sin_content.txt").exists(), (
        "crear_archivo sin content creo un archivo vacio"
    )


# -- editar_archivo (feature 2026-09-26) --------------------------------

def test_editar_archivo_en_catalogo(tmp_path):
    tools = ToolRegistry(Workspace(tmp_path))
    names = {t["function"]["name"] for t in tools.definitions()}
    assert "editar_archivo" in names


def test_editar_archivo_via_registry(tmp_path):
    (tmp_path / "a.py").write_text("hola", encoding="utf-8")
    tools = ToolRegistry(Workspace(tmp_path))
    result = tools.call(
        "editar_archivo",
        {"path": "a.py", "old_string": "hola", "new_string": "adios"},
    )
    assert "ERROR" not in result
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "adios"


def test_editar_archivo_no_requiere_confirmacion(tmp_path):
    tools = ToolRegistry(Workspace(tmp_path))
    assert not tools.requires_confirmation("editar_archivo")
