"""Tests de aliases de argumentos en el nucleo de tools."""
from __future__ import annotations

from pathlib import Path

from core.tools import ToolRegistry
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


# -- prioridad: canonico gana -----------------------------------------


def test_canonico_gana_sobre_alias(tmp_path):
    (tmp_path / 'real.txt').write_text('contenido real', encoding='utf-8')
    (tmp_path / 'alias.txt').write_text('del alias', encoding='utf-8')
    tools = _tools(tmp_path)
    result = tools.call(
        'leer_archivo',
        {'path': 'real.txt', 'archivo': 'alias.txt'},
    )
    assert 'contenido real' in result
    assert 'del alias' not in result


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
