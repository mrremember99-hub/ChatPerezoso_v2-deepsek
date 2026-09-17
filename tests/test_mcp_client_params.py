"""Regresión: _build_params debe pasar args, env y cwd a StdioServerParameters
incluso cuando este es un pydantic BaseModel (cuyo __init__ no expone los
campos con inspect.signature)."""
from __future__ import annotations

from plugins.mcp import MCPClient, MCPServerConfig


class _PydanticLikeParams:
    """Imita un pydantic BaseModel v2."""
    model_fields = {
        "command": None, "args": None, "env": None, "cwd": None,
    }

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _PydanticLikeParamsV1:
    """Imita un pydantic BaseModel v1."""
    __fields__ = {
        "command": None, "args": None, "env": None, "cwd": None,
    }

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _PlainParams:
    """Clase normal con firma explícita."""
    def __init__(self, command, args=None, env=None, cwd=None):
        self.command = command
        self.args = args
        self.env = env
        self.cwd = cwd


def test_build_params_pydantic_v2_includes_args():
    client = MCPClient(MCPServerConfig("python3", args=("server.py",)))
    params = client._build_params(_PydanticLikeParams)
    assert params.command == "python3"
    assert params.args == ["server.py"]


def test_build_params_pydantic_v1_includes_args():
    client = MCPClient(MCPServerConfig("python3", args=("server.py",)))
    params = client._build_params(_PydanticLikeParamsV1)
    assert params.args == ["server.py"]


def test_build_params_plain_class_includes_args():
    client = MCPClient(MCPServerConfig("python3", args=("server.py",)))
    params = client._build_params(_PlainParams)
    assert params.args == ["server.py"]


def test_build_params_includes_cwd_when_set():
    client = MCPClient(MCPServerConfig("python3", args=("x.py",), cwd="/tmp"))
    params = client._build_params(_PydanticLikeParams)
    assert params.cwd == "/tmp"


def test_build_params_skips_cwd_when_none():
    client = MCPClient(MCPServerConfig("python3", args=("x.py",)))
    params = client._build_params(_PydanticLikeParams)
    assert not hasattr(params, "cwd") or params.cwd is None


def test_build_params_skips_env_when_empty():
    client = MCPClient(MCPServerConfig("python3", args=("x.py",)))
    params = client._build_params(_PydanticLikeParams)
    # Sin env definido no se pasa env.
    assert "env" not in params.__dict__


def test_build_params_includes_env_when_set():
    client = MCPClient(
        MCPServerConfig("python3", args=("x.py",), env={"FOO": "bar"})
    )
    params = client._build_params(_PydanticLikeParams)
    assert params.env["FOO"] == "bar"
