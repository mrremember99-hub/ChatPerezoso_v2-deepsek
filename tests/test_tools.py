from core.tools import ToolRegistry
from core.workspace import Workspace


def test_registry_definitions(tmp_path):
    tools = ToolRegistry(Workspace(tmp_path)).definitions()
    names = {item["function"]["name"] for item in tools}
    assert names == {
        "listar_carpeta", "leer_archivo", "crear_archivo", "crear_carpeta",
        "escribir_archivo", "borrar_archivo",
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
    assert by_name["crear_archivo"]["parameters"]["required"] == ["path"]
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
    assert "debe ser texto" in tools.call("leer_archivo", {"path": 123})


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
