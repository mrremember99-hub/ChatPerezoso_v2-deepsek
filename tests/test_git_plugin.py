from __future__ import annotations

import shutil
import subprocess

import pytest

from core.workspace import Workspace
from plugins.git import GitClient, GitError, GitProvider


pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git no está instalado en el sistema",
)


def _init_repo(path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=T", "commit",
         "--allow-empty", "-q", "-m", "commit inicial"],
        cwd=path,
        check=True,
    )


# -- GitClient ---------------------------------------------------------------

def test_is_repo_false_outside_git(tmp_path):
    assert not GitClient(tmp_path).is_repo()


def test_is_repo_true_inside_git(tmp_path):
    _init_repo(tmp_path)
    assert GitClient(tmp_path).is_repo()


def test_status_reports_clean_repo(tmp_path):
    _init_repo(tmp_path)
    output = GitClient(tmp_path).status()
    # La cabecera "--branch" siempre aparece; no hay archivos modificados
    # después de un repo recién creado con un commit vacío.
    assert output.startswith("## ")


def test_status_reports_untracked_file(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "nuevo.txt").write_text("hola", encoding="utf-8")
    output = GitClient(tmp_path).status()
    assert "nuevo.txt" in output


def test_log_returns_commit_summary(tmp_path):
    _init_repo(tmp_path)
    output = GitClient(tmp_path).log(limit=5)
    assert "commit inicial" in output


def test_log_on_empty_repo_returns_friendly_message(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    output = GitClient(tmp_path).log()
    assert "no tiene commits" in output


def test_git_error_if_not_a_repo(tmp_path):
    with pytest.raises(GitError):
        GitClient(tmp_path).status()


# -- GitProvider -------------------------------------------------------------

def test_provider_definitions_empty_without_repo(tmp_path):
    assert GitProvider(Workspace(tmp_path)).definitions() == []


def test_provider_definitions_with_repo(tmp_path):
    _init_repo(tmp_path)
    names = {d["function"]["name"] for d in GitProvider(Workspace(tmp_path)).definitions()}
    assert names == {"git_status", "git_diff", "git_log", "git_show"}


def test_provider_never_requires_confirmation(tmp_path):
    _init_repo(tmp_path)
    provider = GitProvider(Workspace(tmp_path))
    for spec in provider.definitions():
        name = spec["function"]["name"]
        assert not provider.requires_confirmation(name)


def test_provider_declares_intent_rules_for_all_tools(tmp_path):
    _init_repo(tmp_path)
    provider = GitProvider(Workspace(tmp_path))
    rules = provider.intent_rules()
    assert set(rules.keys()) == {"git_status", "git_diff", "git_log", "git_show"}
    for rule in rules.values():
        assert rule.verbs
        assert rule.target_words


def test_provider_status(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "x.txt").write_text("hola", encoding="utf-8")
    provider = GitProvider(Workspace(tmp_path))
    assert "x.txt" in provider.call("git_status", {})


def test_provider_log_respects_limit(tmp_path):
    _init_repo(tmp_path)
    for i in range(5):
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=T", "commit",
             "--allow-empty", "-q", "-m", f"c{i}"],
            cwd=tmp_path,
            check=True,
        )
    provider = GitProvider(Workspace(tmp_path))
    output = provider.call("git_log", {"limit": 3})
    assert output.count("\n") <= 2


def test_provider_log_clamps_limit(tmp_path):
    _init_repo(tmp_path)
    provider = GitProvider(Workspace(tmp_path))
    assert "commit inicial" in provider.call("git_log", {"limit": 999999})


def test_provider_diff(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("hola\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=T", "commit", "-q", "-m", "a"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "a.txt").write_text("hola mundo\n", encoding="utf-8")
    provider = GitProvider(Workspace(tmp_path))
    output = provider.call("git_diff", {})
    assert "hola mundo" in output


def test_provider_unknown_tool(tmp_path):
    _init_repo(tmp_path)
    provider = GitProvider(Workspace(tmp_path))
    assert provider.call("git_inventado", {}).startswith("ERROR: herramienta desconocida")


# -- git_show ----------------------------------------------------------------

def test_provider_definitions_include_show(tmp_path):
    _init_repo(tmp_path)
    names = {d["function"]["name"] for d in GitProvider(Workspace(tmp_path)).definitions()}
    assert names == {"git_status", "git_diff", "git_log", "git_show"}


def test_show_head(tmp_path):
    _init_repo(tmp_path)
    result = GitProvider(Workspace(tmp_path)).call("git_show", {})
    assert "commit inicial" in result


def test_show_stat_mode(tmp_path):
    _init_repo(tmp_path)
    result = GitProvider(Workspace(tmp_path)).call("git_show", {"stat": True})
    assert "commit inicial" in result
    # Sin -p, no debería aparecer el diff completo.
    assert "diff --git" not in result


def test_show_invalid_ref(tmp_path):
    _init_repo(tmp_path)
    result = GitProvider(Workspace(tmp_path)).call("git_show", {"ref": "no-existe"})
    assert result.startswith("ERROR:")


def test_show_declares_intent_rule(tmp_path):
    _init_repo(tmp_path)
    rules = GitProvider(Workspace(tmp_path)).intent_rules()
    assert "git_show" in rules
