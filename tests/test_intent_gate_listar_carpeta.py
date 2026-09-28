"""F7 (2026-09-28): regresión de "lee la carpeta"."""
from core.intent import ToolIntentGate
from core.tools import ToolRegistry
from core.workspace import Workspace


def _gate(tmp_path):
    ws = Workspace(tmp_path)
    return ToolIntentGate(ToolRegistry(ws).intent_rules())


def test_lee_la_carpeta_autoriza_listar_carpeta(tmp_path):
    g = _gate(tmp_path)
    assert g.tool_is_requested("listar_carpeta", "lee la carpeta de trabajo")


def test_ver_el_workspace_autoriza_listar_carpeta(tmp_path):
    g = _gate(tmp_path)
    assert g.tool_is_requested("listar_carpeta", "ver el workspace")


def test_lee_gui_no_autoriza_listar_carpeta(tmp_path):
    """lee gui.py debe autorizar leer_archivo, no listar_carpeta."""
    g = _gate(tmp_path)
    assert not g.tool_is_requested("listar_carpeta", "lee gui.py")
    assert g.tool_is_requested("leer_archivo", "lee gui.py")


def test_lee_la_carpeta_no_autoriza_leer_archivo(tmp_path):
    g = _gate(tmp_path)
    assert not g.tool_is_requested("leer_archivo", "lee la carpeta de trabajo")


def test_leer_sin_target_no_autoriza(tmp_path):
    g = _gate(tmp_path)
    assert not g.tool_is_requested("listar_carpeta", "lee")

# F7-bis (2026-09-28): "analiza la aplicacion" debe autorizar leer_archivo.
def test_analiza_la_aplicacion_autoriza_leer_archivo(tmp_path):
    g = _gate(tmp_path)
    assert g.tool_is_requested(
        "leer_archivo", "analiza la aplicación"
    )


def test_revisa_el_proyecto_autoriza_leer_archivo(tmp_path):
    g = _gate(tmp_path)
    assert g.tool_is_requested("leer_archivo", "revisa el proyecto")


def test_analiza_esto_no_autoriza_leer_archivo(tmp_path):
    """Sin target de workspace, no debe autorizar."""
    g = _gate(tmp_path)
    assert not g.tool_is_requested("leer_archivo", "analiza esto")
