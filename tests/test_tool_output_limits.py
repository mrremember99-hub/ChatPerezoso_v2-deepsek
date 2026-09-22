"""Tests de los límites de output de herramientas (Fase 5)."""
from __future__ import annotations

import subprocess
import shutil

import pytest

from plugins.git.client import _MAX_OUTPUT_BYTES as GIT_MAX
from plugins.mcp.client import _MAX_RESULT_CHARS as MCP_MAX


# -- git ---------------------------------------------------------------------

pytestmark_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git no instalado"
)


@pytestmark_git
def test_git_diff_truncates_huge_output(tmp_path):
    """Un diff enorme se trunca al límite configurado."""
    from plugins.git import GitClient

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=T", "commit",
         "--allow-empty", "-q", "-m", "init"],
        cwd=tmp_path, check=True,
    )
    # Crear un archivo que genere un diff > 200 KB.
    big = tmp_path / "big.txt"
    big.write_text("linea inicial\n", encoding="utf-8")
    subprocess.run(["git", "add", "big.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=T",
         "commit", "-q", "-m", "big"],
        cwd=tmp_path, check=True,
    )
    big.write_text("x" * (GIT_MAX * 2) + "\n", encoding="utf-8")

    output = GitClient(tmp_path).diff()
    assert "truncada" in output
    assert len(output.encode("utf-8")) <= GIT_MAX + 100


@pytestmark_git
def test_git_status_not_truncated_for_normal_repo(tmp_path):
    """Un repo normal no activa el truncado."""
    from plugins.git import GitClient

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("hola", encoding="utf-8")
    output = GitClient(tmp_path).status()
    assert "truncada" not in output


# -- MCP ---------------------------------------------------------------------

def test_mcp_result_truncates_huge_text():
    from plugins.mcp.client import MCPClient

    class FakeResult:
        is_error = False
        content = [type("T", (), {"text": "x" * (MCP_MAX * 2)})()]

    text = MCPClient._result_to_text(FakeResult())
    assert "truncado" in text
    assert len(text) <= MCP_MAX + 200


def test_mcp_result_not_truncated_for_normal_text():
    from plugins.mcp.client import MCPClient

    class FakeResult:
        is_error = False
        content = [type("T", (), {"text": "respuesta normal"})()]

    text = MCPClient._result_to_text(FakeResult())
    assert "truncado" not in text
    assert "respuesta normal" in text


def test_mcp_error_result_still_prefixed_and_truncated():
    from plugins.mcp.client import MCPClient

    class FakeResult:
        is_error = True
        content = [type("T", (), {"text": "y" * (MCP_MAX * 2)})()]

    text = MCPClient._result_to_text(FakeResult())
    assert text.startswith("ERROR MCP:")
    assert "truncado" in text
