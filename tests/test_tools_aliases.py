"""Tests de aliases de argumentos en el nucleo de tools."""
from __future__ import annotations

from pathlib import Path

from core.tools import ToolRegistry, _normalise_args
from core.workspace import Workspace


def _tools(tmp_path: Path) -> ToolRegistry:
    return ToolRegistry(Workspace(tmp_path))


# -- path: aliases aceptados ------------------------------------------


def test_leer_archivo_acepta_archivo(tmp_path):
    ws = tmp_path / 'a.txt'
    ws.write_text('hola', encoding='utf-8')
    tools = _tools(tmp_path)
    result = tools.call('leer_archivo', {'archivo': 'a.txt'})
    assert 'hola' in result


def test_leer_archivo_acepta_ruta(tmp_path):
    (tmp_path / 'x.txt').write_text('ok', encoding='utf-8')
    tools = _tools(tmp_path)
    assert 'ok' in tools.call('leer_archivo', {'ruta': 'x.txt'})


def test_listar_carpeta_acepta_carpeta(tmp_path):
    (tmp_path / 'y.txt').write_text('x', encoding='utf-8')
    tools = _tools(tmp_path)
    result = tools.call('listar_carpeta', {'carpeta': '.'})
    assert 'y.txt' in result


# -- content: aliases aceptados ---------------------------------------


def test_crear_archivo_acepta_contenido(tmp_path):
    tools = _tools(tmp_path)
    result = tools.call(
        'crear_archivo',
        {'archivo': 'nuevo.txt', 'contenido': 'hola'},
        allow_destructive=True,
    )
    assert 'ERROR' not in result, result
    assert (tmp_path / 'nuevo.txt').read_text(encoding='utf-8') == 'hola'


def test_escribir_archivo_acepta_text(tmp_path):
    (tmp_path / 'e.txt').write_text('viejo', encoding='utf-8')
    tools = _tools(tmp_path)
    tools.call(
        'escribir_archivo',
        {'ruta': 'e.txt', 'text': 'nuevo'},
        allow_destructive=True,
    )
    assert (tmp_path / 'e.txt').read_text(encoding='utf-8') == 'nuevo'


# -- lineas: aliases aceptados ----------------------------------------


def test_leer_archivo_acepta_line_start_line_end(tmp_path):
    (tmp_path / 'l.txt').write_text('1\n2\n3\n4\n5', encoding='utf-8')
    tools = _tools(tmp_path)
    result = tools.call(
        'leer_archivo',
        {'archivo': 'l.txt', 'line_start': 2, 'line_end': 4},
    )
    assert '2' in result
    assert '4' in result
    # El output lleva cabecera [lineas 2-4 de 5], que contiene '5'.
    # Verificar las lineas reales, no la cabecera.
    lineas = [l for l in result.split('\n') if l and not l.startswith('[')]
    assert lineas == ['2', '3', '4'], lineas


# -- alias desconocido sigue rechazado --------------------------------


def test_alias_no_conocido_sigue_rechazado(tmp_path):
    tools = _tools(tmp_path)
    result = tools.call('leer_archivo', {'foo': 'bar'})
    # La validacion de required va antes que la de properties, asi
    # que el error es 'falta path'. El alias no reconocido no
    # autoriza nada, que es lo que importa.
    assert 'ERROR' in result


def test_alias_para_tool_que_no_lo_usa_se_rechaza(tmp_path):
    # `content` no es argumento de listar_carpeta.
    tools = _tools(tmp_path)
    result = tools.call('listar_carpeta', {'content': 'x'})
    assert 'ERROR' in result


# -- schema visible no cambia -----------------------------------------


def test_schema_solo_declara_canonicos(tmp_path):
    tools = _tools(tmp_path)
    by_name = {t['function']['name']: t['function'] for t in tools.definitions()}
    leer = by_name['leer_archivo']['parameters']['properties']
    assert 'path' in leer
    assert 'archivo' not in leer
    assert 'line_start' not in leer


# -- Aliases ampliados (feature 2026-09-26) -----------------------------

def test_alias_file_path_normaliza_a_path(tmp_path):
    (tmp_path / "a.txt").write_text("hola", encoding="utf-8")
    tools = _tools(tmp_path)
    assert "hola" in tools.call("leer_archivo", {"file_path": "a.txt"})


def test_alias_body_normaliza_a_content(tmp_path):
    tools = _tools(tmp_path)
    tools.call(
        "crear_archivo",
        {"path": "x.txt", "body": "cuerpo"},
        allow_destructive=True,
    )
    assert (tmp_path / "x.txt").read_text(encoding="utf-8") == "cuerpo"


def test_alias_old_str_normaliza_a_old_string(tmp_path):
    (tmp_path / "a.py").write_text("foo bar baz", encoding="utf-8")
    tools = _tools(tmp_path)
    result = tools.call(
        "editar_archivo",
        {"path": "a.py", "old_str": "bar", "new_str": "QUX"},
    )
    assert "editado" in result.lower()
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "foo QUX baz"


def test_editar_archivo_acepta_aliases_completos(tmp_path):
    (tmp_path / "a.py").write_text("hola mundo", encoding="utf-8")
    tools = _tools(tmp_path)
    tools.call(
        "editar_archivo",
        {
            "file_path": "a.py",
            "oldText": "mundo",
            "newText": "universo",
        },
    )
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "hola universo"


# -- D7: colisión de aliases (auditoría 2026-09-26) ---------------------

def test_canonico_y_alias_mismo_valor_se_colapsa(tmp_path):
    (tmp_path / "a.txt").write_text("ok", encoding="utf-8")
    tools = _tools(tmp_path)
    result = tools.call(
        "leer_archivo",
        {"path": "a.txt", "archivo": "a.txt"},
    )
    assert "ok" in result
    assert "ERROR" not in result


def test_canonico_y_alias_distinto_valor_es_error(tmp_path):
    (tmp_path / "real.txt").write_text("contenido real", encoding="utf-8")
    (tmp_path / "alias.txt").write_text("del alias", encoding="utf-8")
    tools = _tools(tmp_path)
    result = tools.call(
        "leer_archivo",
        {"path": "real.txt", "archivo": "alias.txt"},
    )
    assert result.startswith("ERROR")
    assert "conflicto" in result
    assert "path" in result


def test_normalise_args_solo_canonico_passthrough():
    spec = {"properties": {"path": {"type": "string"}}}
    assert _normalise_args({"path": "x"}, spec) == {"path": "x"}


def test_normalise_args_solo_alias_remap():
    spec = {"properties": {"path": {"type": "string"}}}
    assert _normalise_args({"archivo": "x"}, spec) == {"path": "x"}


def test_normalise_args_mismo_valor_colapsa():
    spec = {"properties": {"path": {"type": "string"}}}
    assert _normalise_args({"path": "x", "archivo": "x"}, spec) == {"path": "x"}


def test_normalise_args_distinto_valor_error():
    spec = {"properties": {"path": {"type": "string"}}}
    out = _normalise_args({"path": "a", "archivo": "b"}, spec)
    assert isinstance(out, str) and out.startswith("ERROR")
