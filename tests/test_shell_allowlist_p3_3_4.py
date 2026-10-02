"""P3#3 + P3#4: git subcomandos duales y programas con flags de escritura."""
from __future__ import annotations

import pytest

from plugins.shell import is_command_allowed


# -- P3#3 git ---------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    # Listados legitimos: siguen pasando.
    "git branch",
    "git branch -a",
    "git branch --list",
    "git tag",
    "git tag --list",
    "git remote -v",
    "git remote",
    "git config --list",
    "git config --global --list",
    "git config --get user.name",
])
def test_git_dual_use_listados_ok(cmd):
    assert is_command_allowed(cmd) is True


@pytest.mark.parametrize("cmd", [
    # P3#3: formas mutantes con flags no listados.
    "git branch -D main",
    "git branch -m main2",
    "git branch --set-upstream-to=origin/main",
    "git tag -d v1",
    "git tag -a v1 -m x",
    "git tag -f v1",
    "git remote add origin url",
    "git remote remove origin",
    "git remote set-url origin url",
    "git config user.name foo",
    "git config core.fsmonitor 'rm -rf /'",
    "git config --unset user.name",
])
def test_git_dual_use_mutantes_denegados(cmd):
    assert is_command_allowed(cmd) is False


@pytest.mark.parametrize("cmd", [
    # P3#3: cualquier subcomando con --output / -o.
    "git diff --output=/tmp/x",
    "git log --output=/tmp/x",
    "git show --output=/tmp/x HEAD",
    "git log -o /tmp/x",
])
def test_git_output_flags_denegados(cmd):
    assert is_command_allowed(cmd) is False


# -- P3#4 tree --------------------------------------------------------

def test_tree_normal_ok():
    assert is_command_allowed("tree") is True
    assert is_command_allowed("tree -L 2") is True


@pytest.mark.parametrize("cmd", [
    "tree -o out.txt",
    "tree --output=out.txt",
    "tree --output out.txt",
])
def test_tree_output_denegado(cmd):
    assert is_command_allowed(cmd) is False


# -- P3#4 json.tool ---------------------------------------------------

def test_json_tool_lectura_ok():
    assert is_command_allowed("python -m json.tool data.json") is True


@pytest.mark.parametrize("cmd", [
    "python -m json.tool in.json out.json",
    "python3 -m json.tool in.json out.json",
])
def test_json_tool_output_denegado(cmd):
    assert is_command_allowed(cmd) is False


# -- P3#4 pydoc -------------------------------------------------------

def test_pydoc_lectura_ok():
    assert is_command_allowed("python -m pydoc json") is True


@pytest.mark.parametrize("cmd", [
    "python -m pydoc -w json",
    "python3 -m pydoc -w json",
    "python -m pydoc --write json",
])
def test_pydoc_write_denegado(cmd):
    assert is_command_allowed(cmd) is False
