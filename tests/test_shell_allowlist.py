"""Tests de plugins.shell.allowlist.is_command_allowed."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.shell.allowlist import is_command_allowed  # noqa: E402


# -- permitidos ---------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    # Filesystem read-only
    "ls",
    "ls -la",
    "cat README.md",
    "head -20 file.txt",
    "tail -f log.txt",
    "wc -l file.py",
    "grep -n foo bar.py",
    "grep -rn TODO .",
    "file setup.py",
    "which python3",
    "diff a.py b.py",
    "tree",
    "tree -L 2",
    "pwd",
    "echo hello world",
    "stat file.py",
    "du -sh .",
    "basename /a/b/c.py",
    "dirname /a/b/c.py",
    "date",
    "uname -a",
    "whoami",
    # Tests / ejecucion de python
    "pytest",
    "pytest -q tests/",
    "python -m pytest tests/",
    "python3 -m pytest -q",
    "python script.py",
    "python3 script.py",
    "python -m json.tool data.json",
    # find sin flags peligrosos
    "find . -name '*.py'",
    "find . -type f -name '*.md'",
    # git read-only
    "git status",
    "git log --oneline -5",
    "git diff HEAD",
    "git show HEAD",
    "git branch",
    "git branch -a",
    "git stash",
    "git stash list",
    # Case insensitive en el programa
    "LS -la",
    "GIT status",
    # Ruta absoluta al programa
    "/bin/ls -la",
    "/usr/bin/python3 script.py",
])
def test_permitidos(cmd):
    assert is_command_allowed(cmd) is True, f"deberia permitirse: {cmd}"


# -- denegados por programa ---------------------------------------------

@pytest.mark.parametrize("cmd", [
    "",
    "   ",
    "rm file",
    "rm -rf /tmp/x",
    "mv a b",
    "cp a b",
    "chmod 777 file",
    "chown user file",
    "curl http://example.com",
    "wget http://example.com",
    "pip install foo",
    "pip3 install foo",
    "npm install",
    "sudo ls",
    "SUDO ls",
    "/usr/bin/sudo ls",
    "dd if=/dev/zero of=x",
    "mkfs.ext4 /dev/sda",
    "sed 's/a/b/' file",
    "sed -i 's/a/b/' file",
    "sort -o out.txt in.txt",
    "uniq in.txt out.txt",
    "env VAR=x ls",
    "tee out.txt",
])
def test_denegados_por_programa(cmd):
    assert is_command_allowed(cmd) is False, f"NO deberia permitirse: {cmd}"


# -- denegados por metacaracteres (delegados a _validate_command) -------

@pytest.mark.parametrize("cmd", [
    "ls; rm -rf /",
    "ls && cat /etc/passwd",
    "ls || true",
    "ls | grep x",
    "ls > out.txt",
    "cat < in.txt",
    "echo `whoami`",
    "echo $(whoami)",
    "echo ${HOME}",
])
def test_denegados_por_metacaracteres(cmd):
    assert is_command_allowed(cmd) is False, f"NO deberia permitirse: {cmd}"


# -- git subcomandos mutantes -------------------------------------------

@pytest.mark.parametrize("cmd", [
    "git",
    "git push",
    "git push origin main",
    "git reset --hard",
    "git checkout main",
    "git clean -fd",
    "git commit -m x",
    "git rebase main",
    "git merge other",
    "git stash drop",
    "git stash pop",
    "git stash apply",
])
def test_git_mutantes_denegados(cmd):
    assert is_command_allowed(cmd) is False, f"NO deberia permitirse: {cmd}"


# -- python flags peligrosos --------------------------------------------

@pytest.mark.parametrize("cmd", [
    "python",
    "python3",
    "python -i",
    "python3 --interactive",
    "python -c x",
    "python3 -c print(1)",
    "python -m pip install foo",
    "python3 -m pip install foo",
    "python -m venv .venv",
    "python -m http.server",
])
def test_python_peligrosos_denegados(cmd):
    assert is_command_allowed(cmd) is False, f"NO deberia permitirse: {cmd}"


# -- find flags peligrosos ----------------------------------------------

@pytest.mark.parametrize("cmd", [
    "find . -exec rm {} +",
    "find . -execdir rm {} +",
    "find . -ok rm {} +",
    "find . -delete",
    "find . -fprint out.txt",
    "find . -fprintf out.txt x",
])
def test_find_peligrosos_denegados(cmd):
    assert is_command_allowed(cmd) is False, f"NO deberia permitirse: {cmd}"


# -- defensivo: tipos inesperados ---------------------------------------

@pytest.mark.parametrize("cmd", [None, 42, [], {}])
def test_no_string_devuelve_false(cmd):
    assert is_command_allowed(cmd) is False
