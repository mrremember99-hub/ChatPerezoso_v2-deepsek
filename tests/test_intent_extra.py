"""Tests de la heurística de intención: negación conjugada, fronteras de
palabra, puntuación pegada y reglas Git."""
from core.intent import ToolIntentGate
from core.tools import ToolRegistry
from core.workspace import Workspace
from plugins.git import GitProvider


def _gate(tmp_path):
    workspace = Workspace(tmp_path)
    registry = ToolRegistry(workspace)
    git = GitProvider(workspace)
    rules = {**registry.intent_rules(), **git.intent_rules()}
    ToolIntentGate.register_rules(rules)
    return ToolIntentGate(rules)


# -- negación ----------------------------------------------------------------

def test_negation_accepts_conjugated_imperative(tmp_path):
    gate = _gate(tmp_path)
    assert not gate.tool_is_requested("borrar_archivo", "no lo borres")
    assert not gate.tool_is_requested("borrar_archivo", "no lo borres por favor")
    assert not gate.tool_is_requested("borrar_archivo", "no quiero que lo borres")


def test_negation_accepts_infinitive(tmp_path):
    gate = _gate(tmp_path)
    assert not gate.tool_is_requested("borrar_archivo", "no borrar archivo.txt")
    assert not gate.tool_is_requested("borrar_archivo", "no vas a borrar archivo.txt")


def test_negation_does_not_bleed_to_other_tools(tmp_path):
    gate = _gate(tmp_path)
    text = "no borres viejo.txt, pero crea nuevo.txt"
    assert not gate.tool_is_requested("borrar_archivo", text)
    assert gate.tool_is_requested("crear_archivo", text)


def test_word_boundaries_prevent_false_positives(tmp_path):
    gate = _gate(tmp_path)
    assert not gate.tool_is_requested(
        "leer_archivo", "abre el archivador de documentos"
    )


def test_mentions_workspace_operation_requires_verb_and_target(tmp_path):
    gate = _gate(tmp_path)
    assert not gate._mentions_workspace_operation("el archivo está roto")
    assert not gate._mentions_workspace_operation("crea algo bonito")
    assert gate._mentions_workspace_operation("crea el archivo notas.txt")


# -- puntuación y acentos ----------------------------------------------------

def test_verb_with_leading_punctuation(tmp_path):
    """¿dónde debe matchear 'dónde' aunque lleve el signo pegado."""
    from plugins.search import SearchProvider

    rules = SearchProvider(Workspace(tmp_path)).intent_rules()
    gate = ToolIntentGate(rules)
    assert gate.tool_is_requested(
        "buscar_en_workspace", "¿dónde está la función saludar?"
    )
    # Sin tilde: sigue matcheando porque normalizamos ambos lados.
    assert gate.tool_is_requested(
        "buscar_en_workspace", "donde esta la funcion saludar"
    )


def test_verb_with_trailing_punctuation(tmp_path):
    gate = _gate(tmp_path)
    # La puntuación pegada al verbo no impide el match, pero el target
    # sigue siendo obligatorio para herramientas con requires_target=True.
    assert gate.tool_is_requested("leer_archivo", "lee, el archivo notas.txt")
    assert gate.tool_is_requested("borrar_archivo", "borra notas.txt.")
    # Sin target, la lectura no se autoriza aunque el verbo esté presente.
    assert not gate.tool_is_requested("leer_archivo", "lee, por favor")


def test_verb_with_surrounding_quotes(tmp_path):
    from plugins.search import SearchProvider

    rules = SearchProvider(Workspace(tmp_path)).intent_rules()
    gate = ToolIntentGate(rules)
    assert gate.tool_is_requested(
        "buscar_en_workspace", "busca \"TODO\" en el proyecto"
    )
    assert gate.tool_is_requested(
        "buscar_en_workspace", "encuentra 'def test_'"
    )


# -- Git ---------------------------------------------------------------------

def test_git_status_authorized_for_repo_mention(tmp_path):
    gate = _gate(tmp_path)
    assert gate.tool_is_requested("git_status", "muéstrame el estado del repo")
    assert gate.tool_is_requested("git_status", "¿qué cambios hay en el proyecto?")
    assert gate.tool_is_requested("git_status", "git status")


def test_git_diff_authorized_by_diff_or_cambios(tmp_path):
    gate = _gate(tmp_path)
    assert gate.tool_is_requested("git_diff", "muéstrame las diferencias")
    assert gate.tool_is_requested("git_diff", "diff del proyecto")


def test_git_log_authorized_by_historial(tmp_path):
    gate = _gate(tmp_path)
    assert gate.tool_is_requested("git_log", "muéstrame el historial de commits")
    assert gate.tool_is_requested("git_log", "qué commits hay")


def test_git_tools_not_authorized_by_generic_mention(tmp_path):
    gate = _gate(tmp_path)
    assert not gate.tool_is_requested("git_log", "¿qué es git?")
    assert not gate.tool_is_requested("git_status", "explica para qué sirve git")


def test_tools_for_request_exposes_git_tools_when_repo_mentioned(tmp_path):
    gate = _gate(tmp_path)
    tools = [{"type": "function", "function": {"name": "git_status"}}]
    assert gate.tools_for_request(tools, "estado del repo") == tools
