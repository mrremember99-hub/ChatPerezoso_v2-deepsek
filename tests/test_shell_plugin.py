from __future__ import annotations

import sys

import pytest

from core.intent import ToolIntentGate
from core.workspace import Workspace
from plugins.shell import ShellClient, ShellError, ShellProvider, analyze_risk


# -- validación --------------------------------------------------------------

def test_rejects_metachar_and(tmp_path):
    with pytest.raises(ShellError):
        ShellClient(tmp_path).execute("echo hola && echo mundo")


def test_rejects_metachar_pipe(tmp_path):
    with pytest.raises(ShellError):
        ShellClient(tmp_path).execute("ls | grep py")


def test_rejects_metachar_redirect(tmp_path):
    with pytest.raises(ShellError):
        ShellClient(tmp_path).execute("echo hola > archivo.txt")


def test_rejects_metachar_semicolon(tmp_path):
    with pytest.raises(ShellError):
        ShellClient(tmp_path).execute("echo a; echo b")


def test_rejects_backticks(tmp_path):
    with pytest.raises(ShellError):
        ShellClient(tmp_path).execute("echo `whoami`")


def test_rejects_command_substitution(tmp_path):
    with pytest.raises(ShellError):
        ShellClient(tmp_path).execute("echo $(whoami)")


def test_rejects_sudo(tmp_path):
    with pytest.raises(ShellError):
        ShellClient(tmp_path).execute("sudo ls")


def test_rejects_dd(tmp_path):
    with pytest.raises(ShellError):
        ShellClient(tmp_path).execute("dd if=/dev/zero of=archivo")


def test_rejects_shutdown(tmp_path):
    with pytest.raises(ShellError):
        ShellClient(tmp_path).execute("shutdown -h now")


def test_rejects_empty_command(tmp_path):
    with pytest.raises(ShellError):
        ShellClient(tmp_path).execute("")


# -- cwd ---------------------------------------------------------------------

def test_cwd_must_be_inside_workspace(tmp_path):
    with pytest.raises(ShellError):
        ShellClient(tmp_path).execute("ls", cwd="..")


def test_cwd_must_exist(tmp_path):
    with pytest.raises(ShellError):
        ShellClient(tmp_path).execute("ls", cwd="no-existe")


def test_cwd_subdir(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "archivo.txt").write_text("hola", encoding="utf-8")
    result = ShellClient(tmp_path).execute("ls", cwd="sub")
    assert "archivo.txt" in result


# -- ejecución ---------------------------------------------------------------

def test_executes_simple_command(tmp_path):
    result = ShellClient(tmp_path).execute("echo hola")
    assert "hola" in result
    assert result.startswith("hola")


def test_executes_list(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "b.txt").write_text("y", encoding="utf-8")
    result = ShellClient(tmp_path).execute("ls")
    assert "a.txt" in result
    assert "b.txt" in result


def test_nonzero_exit_is_reported(tmp_path):
    result = ShellClient(tmp_path).execute("ls no-existe-jamas")
    assert "exit code" in result


def test_unknown_program_is_reported(tmp_path):
    result = ShellClient(tmp_path).execute("programa-que-no-existe-xyz")
    assert "no existe" in result


def test_output_is_truncated(tmp_path):
    # "yes" con timeout corto produce mucho output antes de morir.
    client = ShellClient(tmp_path)
    result = client.execute(f"{sys.executable} -c \"print('x' * 1000, flush=True)\"")
    assert isinstance(result, str)


# -- riesgo ------------------------------------------------------------------

def test_analyze_risk_flags_rm(tmp_path):
    reasons = analyze_risk("rm archivo.txt")
    assert any("rm" in r for r in reasons)


def test_analyze_risk_flags_rm_rf(tmp_path):
    reasons = analyze_risk("rm -rf node_modules")
    assert any("recursivo" in r for r in reasons)


def test_analyze_risk_flags_git_push():
    reasons = analyze_risk("git push origin main")
    assert any("Git" in r for r in reasons)


def test_analyze_risk_empty_for_safe_command():
    assert analyze_risk("ls -la") == []
    assert analyze_risk("pytest -q") == []
    assert analyze_risk("python -m compileall .") == []


# -- provider ----------------------------------------------------------------

def test_provider_exposes_tool(tmp_path):
    provider = ShellProvider(Workspace(tmp_path))
    names = [d["function"]["name"] for d in provider.definitions()]
    assert names == ["ejecutar_comando"]


def test_provider_requires_confirmation_always(tmp_path):
    provider = ShellProvider(Workspace(tmp_path))
    assert provider.requires_confirmation("ejecutar_comando")


def test_provider_call_executes(tmp_path):
    provider = ShellProvider(Workspace(tmp_path))
    # El provider exige confirmación explícita (allow_destructive=True)
    # incluso antes de llegar al cliente. Sin él, bloquea.
    result = provider.call(
        "ejecutar_comando",
        {"command": "echo hola"},
        allow_destructive=True,
    )
    assert "hola" in result


def test_provider_call_rejects_missing_command(tmp_path):
    provider = ShellProvider(Workspace(tmp_path))
    result = provider.call("ejecutar_comando", {})
    assert result.startswith("ERROR:")


def test_provider_call_rejects_pipe(tmp_path):
    provider = ShellProvider(Workspace(tmp_path))
    result = provider.call("ejecutar_comando", {"command": "ls | grep py"})
    assert result.startswith("ERROR:")


def test_provider_call_unknown_tool(tmp_path):
    provider = ShellProvider(Workspace(tmp_path))
    result = provider.call("inventada", {})
    assert result.startswith("ERROR:")


def test_provider_declares_intent_rule(tmp_path):
    provider = ShellProvider(Workspace(tmp_path))
    rules = provider.intent_rules()
    assert "ejecutar_comando" in rules
    rule = rules["ejecutar_comando"]
    assert "ejecuta" in rule.verbs
    assert "compila" in rule.verbs
    assert rule.requires_target is False


# -- intención ---------------------------------------------------------------

def test_shell_authorized_by_explicit_verb(tmp_path):
    rules = ShellProvider(Workspace(tmp_path)).intent_rules()
    gate = ToolIntentGate(rules)
    assert gate.tool_is_requested("ejecutar_comando", "ejecuta los tests del proyecto")
    assert gate.tool_is_requested("ejecutar_comando", "corre pytest")
    assert gate.tool_is_requested("ejecutar_comando", "compila el proyecto")


def test_shell_not_authorized_by_generic_mention(tmp_path):
    rules = ShellProvider(Workspace(tmp_path)).intent_rules()
    gate = ToolIntentGate(rules)
    assert not gate.tool_is_requested("ejecutar_comando", "¿qué es un comando?")
    assert not gate.tool_is_requested("ejecutar_comando", "explica para qué sirve el shell")

def test_provider_call_blocks_without_allow_destructive(tmp_path):
    """Defensa en profundidad: aunque el ChatWorker pide confirmación,
    el propio provider se niega a ejecutar sin autorización."""
    provider = ShellProvider(Workspace(tmp_path))
    result = provider.call("ejecutar_comando", {"command": "echo hola"})
    assert "confirmación explícita" in result
