from __future__ import annotations

import pytest

from core.intent import ToolIntentGate
from core.workspace import Workspace
from plugins.search import SearchClient, SearchError, SearchProvider


# -- SearchClient ------------------------------------------------------------

def test_search_finds_simple_match(tmp_path):
    (tmp_path / "a.txt").write_text("hola mundo\nadiós mundo\n", encoding="utf-8")
    client = SearchClient(tmp_path)
    result = client.search("mundo")
    assert "a.txt" in result
    assert "hola mundo" in result


def test_search_is_case_insensitive_by_default(tmp_path):
    (tmp_path / "a.txt").write_text("HOLA\n", encoding="utf-8")
    result = SearchClient(tmp_path).search("hola")
    assert "HOLA" in result


def test_search_case_sensitive(tmp_path):
    (tmp_path / "a.txt").write_text("HOLA\nhola\n", encoding="utf-8")
    result = SearchClient(tmp_path).search("hola", case_sensitive=True)
    assert "1 coincidencia" in result


def test_search_no_matches(tmp_path):
    (tmp_path / "a.txt").write_text("nada\n", encoding="utf-8")
    result = SearchClient(tmp_path).search("xyz")
    assert "sin coincidencias" in result


def test_search_empty_query_raises(tmp_path):
    with pytest.raises(SearchError):
        SearchClient(tmp_path).search("")


def test_search_invalid_regex_raises(tmp_path):
    with pytest.raises(SearchError):
        SearchClient(tmp_path).search("[")


def test_search_respects_skip_dirs(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secreto\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.txt").write_text("secreto\n", encoding="utf-8")
    result = SearchClient(tmp_path).search("secreto")
    assert "src/a.txt" in result
    assert ".git" not in result


def test_search_skips_binary_extensions(tmp_path):
    (tmp_path / "foto.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "notas.txt").write_text("foto\n", encoding="utf-8")
    result = SearchClient(tmp_path).search("foto")
    assert "notas.txt" in result
    assert "foto.png" not in result


def test_search_skips_non_utf8_files(tmp_path):
    (tmp_path / "bin.dat").write_bytes(b"\xff\xfe\x00\x01")
    (tmp_path / "ok.txt").write_text("hola\n", encoding="utf-8")
    result = SearchClient(tmp_path).search("hola")
    assert "ok.txt" in result


def test_search_max_matches_limits_results(tmp_path):
    (tmp_path / "a.txt").write_text("\n".join(f"linea {i}" for i in range(100)))
    result = SearchClient(tmp_path).search("linea", max_matches=5)
    assert result.count("linea") == 5
    assert "truncados" in result


def test_search_path_traversal_blocked(tmp_path):
    outside = tmp_path.parent / "secreto.txt"
    outside.write_text("secreto", encoding="utf-8")
    client = SearchClient(tmp_path)
    with pytest.raises(SearchError):
        client.search("secreto", path="..")


def test_search_relative_path(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("dentro\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("fuera\n", encoding="utf-8")
    result = SearchClient(tmp_path).search("fuera", path="sub")
    assert "sin coincidencias" in result


# -- SearchProvider ----------------------------------------------------------

def test_provider_exposes_one_tool(tmp_path):
    provider = SearchProvider(Workspace(tmp_path))
    names = [d["function"]["name"] for d in provider.definitions()]
    assert names == ["buscar_en_workspace"]


def test_provider_never_requires_confirmation(tmp_path):
    assert not SearchProvider(Workspace(tmp_path)).requires_confirmation(
        "buscar_en_workspace"
    )


def test_provider_call_search(tmp_path):
    (tmp_path / "a.txt").write_text("hola mundo\n", encoding="utf-8")
    provider = SearchProvider(Workspace(tmp_path))
    result = provider.call("buscar_en_workspace", {"query": "mundo"})
    assert "a.txt" in result


def test_provider_call_missing_query(tmp_path):
    provider = SearchProvider(Workspace(tmp_path))
    result = provider.call("buscar_en_workspace", {})
    assert result.startswith("ERROR:")


def test_provider_call_unknown_tool(tmp_path):
    provider = SearchProvider(Workspace(tmp_path))
    result = provider.call("inventada", {})
    assert result.startswith("ERROR:")


def test_provider_declares_intent_rule(tmp_path):
    provider = SearchProvider(Workspace(tmp_path))
    rules = provider.intent_rules()
    assert "buscar_en_workspace" in rules
    rule = rules["buscar_en_workspace"]
    assert "busca" in rule.verbs
    assert rule.requires_target is False


# -- Intención ---------------------------------------------------------------

def test_search_authorized_by_buscar(tmp_path):
    rules = SearchProvider(Workspace(tmp_path)).intent_rules()
    ToolIntentGate.register_rules(rules)
    gate = ToolIntentGate(rules)
    assert gate.tool_is_requested("buscar_en_workspace", "busca 'TODO' en el proyecto")
    assert gate.tool_is_requested("buscar_en_workspace", "¿dónde está la función saludar?")
    assert gate.tool_is_requested("buscar_en_workspace", "encuentra los TODO")


def test_search_not_authorized_by_generic_question(tmp_path):
    rules = SearchProvider(Workspace(tmp_path)).intent_rules()
    gate = ToolIntentGate(rules)
    # "grep" ya no es verbo: preguntar por él no autoriza la búsqueda.
    assert not gate.tool_is_requested("buscar_en_workspace", "¿qué es grep?")
    assert not gate.tool_is_requested("buscar_en_workspace", "explica grep")


# -- filtro por extensión ----------------------------------------------------

def test_search_filters_by_extension(tmp_path):
    (tmp_path / "a.py").write_text("hola\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("hola\n", encoding="utf-8")
    (tmp_path / "c.txt").write_text("hola\n", encoding="utf-8")

    result = SearchClient(tmp_path).search("hola", extensions=["py", "md"])
    assert "a.py" in result
    assert "b.md" in result
    assert "c.txt" not in result


def test_search_accepts_extensions_with_dot(tmp_path):
    (tmp_path / "a.py").write_text("hola\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("hola\n", encoding="utf-8")
    result = SearchClient(tmp_path).search("hola", extensions=[".py"])
    assert "a.py" in result
    assert "b.md" not in result


def test_search_ignores_invalid_extension_entries(tmp_path):
    (tmp_path / "a.py").write_text("hola\n", encoding="utf-8")
    result = SearchClient(tmp_path).search("hola", extensions=[123, "py"])  # type: ignore[list-item]
    assert "a.py" in result


def test_provider_passes_extensions(tmp_path):
    (tmp_path / "a.py").write_text("hola\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("hola\n", encoding="utf-8")
    provider = SearchProvider(Workspace(tmp_path))
    result = provider.call(
        "buscar_en_workspace", {"query": "hola", "extensions": ["py"]}
    )
    assert "a.py" in result
    assert "b.txt" not in result
