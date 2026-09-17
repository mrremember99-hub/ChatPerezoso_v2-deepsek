from __future__ import annotations

import json
from pathlib import Path

from core.history import HistoryStore


# -- load / save -------------------------------------------------------------

def test_load_missing_file_returns_none(tmp_path):
    assert HistoryStore(tmp_path / "no-existe.json").load() is None


def test_save_and_load_roundtrip(tmp_path):
    store = HistoryStore(tmp_path / "h.json")
    messages = [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "buenas"},
    ]
    store.save(messages, model="llama3", workspace="/tmp/ws")

    history = store.load()
    assert history is not None
    assert history.messages == messages
    assert history.model == "llama3"
    assert history.workspace == "/tmp/ws"
    assert history.saved_at


def test_save_filters_non_conversation_messages(tmp_path):
    """Los mensajes tool y system no forman parte de la conversación."""
    store = HistoryStore(tmp_path / "h.json")
    store.save([
        {"role": "user", "content": "a"},
        {"role": "tool", "content": "no guardar"},
        {"role": "system", "content": "no guardar"},
        {"role": "assistant", "content": "b"},
    ])
    history = store.load()
    assert history is not None
    roles = [m["role"] for m in history.messages]
    assert roles == ["user", "assistant"]


def test_load_corrupted_json_returns_none(tmp_path):
    path = tmp_path / "h.json"
    path.write_text("{no es json", encoding="utf-8")
    assert HistoryStore(path).load() is None


def test_load_non_dict_returns_none(tmp_path):
    path = tmp_path / "h.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert HistoryStore(path).load() is None


def test_load_filters_invalid_messages(tmp_path):
    path = tmp_path / "h.json"
    path.write_text(json.dumps({
        "messages": [
            {"role": "user", "content": "vale"},
            {"role": "tool", "content": "no"},
            {"role": "user"},                      # sin content
            {"role": "user", "content": 42},       # content no string
            "no soy dict",
            {"role": "assistant", "content": "ok"},
        ],
    }), encoding="utf-8")
    history = HistoryStore(path).load()
    assert history is not None
    assert len(history.messages) == 2
    assert history.messages[0] == {"role": "user", "content": "vale"}
    assert history.messages[1] == {"role": "assistant", "content": "ok"}


def test_load_empty_messages_returns_none(tmp_path):
    path = tmp_path / "h.json"
    path.write_text(json.dumps({"messages": []}), encoding="utf-8")
    assert HistoryStore(path).load() is None


def test_clear_removes_file(tmp_path):
    path = tmp_path / "h.json"
    store = HistoryStore(path)
    store.save([{"role": "user", "content": "x"}])
    assert path.exists()
    store.clear()
    assert not path.exists()


def test_clear_on_missing_file_is_silent(tmp_path):
    path = tmp_path / "no-existe.json"
    HistoryStore(path).clear()  # no debe lanzar


def test_save_creates_parent_directory(tmp_path):
    path = tmp_path / "sub" / "dir" / "h.json"
    HistoryStore(path).save([{"role": "user", "content": "x"}])
    assert path.exists()


def test_load_caps_message_count(tmp_path):
    path = tmp_path / "h.json"
    many = [{"role": "user", "content": str(i)} for i in range(500)]
    path.write_text(json.dumps({"messages": many}), encoding="utf-8")
    history = HistoryStore(path).load()
    assert history is not None
    assert len(history.messages) == 200
