"""Tests de scripts.overpaper_tracker (funciones puras)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import overpaper_tracker as t  # noqa: E402


def test_sha256_file(tmp_path):
    p = tmp_path / "x.txt"
    p.write_text("hola", encoding="utf-8")
    h1 = t._sha256_file(p)
    p.write_text("hola", encoding="utf-8")
    assert t._sha256_file(p) == h1
    p.write_text("adios", encoding="utf-8")
    assert t._sha256_file(p) != h1


def test_sha256_file_inexistente(tmp_path):
    assert t._sha256_file(tmp_path / "nope") == ""


def test_list_workspace_filtra_extensiones(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "ROOT", tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "a.py").write_text("x=1", encoding="utf-8")
    (ws / "b.md").write_text("# h", encoding="utf-8")
    (ws / "c.bin").write_bytes(b"\x00\x01")
    got = t._list_workspace(ws)
    assert "a.py" in got
    assert "b.md" in got
    assert "c.bin" not in got


def test_list_workspace_inexistente(tmp_path):
    assert t._list_workspace(tmp_path / "nope") == {}


def test_load_workspace_root_sin_config(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "ROOT", tmp_path)
    assert t._load_workspace_root() == tmp_path / "workspace"


def test_load_workspace_root_con_config(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "ROOT", tmp_path)
    (tmp_path / "config.json").write_text(
        json.dumps({"workspace": str(tmp_path / "custom_ws")}),
        encoding="utf-8",
    )
    got = t._load_workspace_root()
    assert got == (tmp_path / "custom_ws").resolve()


def test_tracker_detecta_creado_y_modificado(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "ROOT", tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()
    run_dir = tmp_path / "run"
    tr = t.Tracker(run_dir, interval=0.01)
    tr._last_workspace = {}
    (ws / "f.py").write_text("v1", encoding="utf-8")
    tr._snapshot_workspace()
    (ws / "f.py").write_text("v2 mas largo", encoding="utf-8")
    tr._snapshot_workspace()
    lines = (run_dir / "workspace_timeline.jsonl").read_text(encoding="utf-8").splitlines()
    kinds = [json.loads(ln)["kind"] for ln in lines if ln.strip()]
    assert kinds == ["created", "modified"]


def test_tracker_detecta_borrado(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "ROOT", tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "f.py").write_text("x", encoding="utf-8")
    run_dir = tmp_path / "run"
    tr = t.Tracker(run_dir, interval=0.01)
    tr._snapshot_workspace()
    (ws / "f.py").unlink()
    tr._snapshot_workspace()
    lines = (run_dir / "workspace_timeline.jsonl").read_text(encoding="utf-8").splitlines()
    kinds = [json.loads(ln)["kind"] for ln in lines if ln.strip()]
    assert kinds == ["created", "deleted"]


def test_analyze_genera_informe(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "ROOT", tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "workspace_timeline.jsonl").write_text(
        json.dumps({"ts": "2026-09-29T00:00:00", "kind": "created", "path": "a.py"}) + "\n"
        + json.dumps({"ts": "2026-09-29T00:00:01", "kind": "modified", "path": "a.py"}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "app.log").write_text(
        "arrancando\nTraceback (most recent call last):\nValueError: x\n",
        encoding="utf-8",
    )
    t._analyze(run_dir, exit_code=0, duration_s=12.5)
    md = (run_dir / "analysis.md").read_text(encoding="utf-8")
    assert "Creados: 1" in md
    assert "Modificados: 1" in md
    assert "Traceback" in md
