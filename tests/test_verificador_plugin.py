"""Tests del plugin verificador de sintaxis."""
from __future__ import annotations

import pytest

from core.workspace import Workspace
from plugins.verificador import VerificadorProvider, check_syntax


# -- client -----------------------------------------------------------


def test_check_syntax_python_valido(tmp_path):
    f = tmp_path / "ok.py"
    f.write_text("def f():\n    return 1\n", encoding="utf-8")
    assert check_syntax(f) == []


def test_check_syntax_python_roto(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("def f(:\n    return 1\n", encoding="utf-8")
    issues = check_syntax(f)
    assert len(issues) == 1
    assert issues[0].line == 1


def test_check_syntax_archivo_inexistente(tmp_path):
    f = tmp_path / "no_existe.py"
    issues = check_syntax(f)
    assert len(issues) == 1
    assert "no se pudo leer" in issues[0].message


def test_check_syntax_lenguaje_no_soportado(tmp_path):
    f = tmp_path / "app.js"
    f.write_text("const x = ;", encoding="utf-8")
    assert check_syntax(f) == []


# -- provider: catalogo -----------------------------------------------


def test_provider_definitions_una_tool(tmp_path):
    p = VerificadorProvider(Workspace(tmp_path))
    defs = p.definitions()
    assert len(defs) == 1
    assert defs[0]["function"]["name"] == "verificar_sintaxis"


def test_provider_nunca_requiere_confirmacion(tmp_path):
    p = VerificadorProvider(Workspace(tmp_path))
    assert not p.requires_confirmation("verificar_sintaxis")


def test_provider_intent_rules_vacio(tmp_path):
    p = VerificadorProvider(Workspace(tmp_path))
    assert p.intent_rules() == {}


# -- provider: call ---------------------------------------------------


def test_provider_verifica_archivo_valido(tmp_path):
    p = VerificadorProvider(Workspace(tmp_path))
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    out = p.call("verificar_sintaxis", {"archivo": "ok.py"})
    assert out == ""


def test_provider_reporta_error(tmp_path):
    p = VerificadorProvider(Workspace(tmp_path))
    (tmp_path / "bad.py").write_text("def f(:\n", encoding="utf-8")
    out = p.call("verificar_sintaxis", {"archivo": "bad.py"})
    assert "bad.py" in out
    assert "1 problema" in out


def test_provider_acepta_varios_nombres_de_campo(tmp_path):
    p = VerificadorProvider(Workspace(tmp_path))
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    assert p.call("verificar_sintaxis", {"nombre": "ok.py"}) == ""
    assert p.call("verificar_sintaxis", {"path": "ok.py"}) == ""
    assert p.call("verificar_sintaxis", {"ruta": "ok.py"}) == ""


def test_provider_falta_archivo(tmp_path):
    p = VerificadorProvider(Workspace(tmp_path))
    out = p.call("verificar_sintaxis", {})
    assert "ERROR" in out


def test_provider_archivo_inexistente(tmp_path):
    p = VerificadorProvider(Workspace(tmp_path))
    out = p.call("verificar_sintaxis", {"archivo": "no_existe.py"})
    assert "ERROR" in out
    assert "no encontrado" in out


def test_provider_herramienta_desconocida(tmp_path):
    p = VerificadorProvider(Workspace(tmp_path))
    out = p.call("otra_cosa", {})
    assert "ERROR" in out


def test_verificar_archivo_directo(tmp_path):
    p = VerificadorProvider(Workspace(tmp_path))
    (tmp_path / "bad.py").write_text("def f(:\n", encoding="utf-8")
    out = p.verificar_archivo("bad.py")
    assert "bad.py" in out
    assert "problema" in out


# -- worker: hook ----------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _FakeTools:
    def requires_confirmation(self, name):
        return False

    def call(self, name, arguments, **kwargs):
        return "ok"

    def definitions(self):
        return []


class _FakeClient:
    pass


def _make_worker(hook):
    from ui.workers import ChatWorker
    return ChatWorker(
        client=_FakeClient(),  # type: ignore[arg-type]
        model="test",
        messages=[],
        tools=_FakeTools(),
        verificador_hook=hook,
    )


def _ok_result(name="crear_archivo"):
    from core.tool_result import ToolResult
    return ToolResult(tool_name=name, summary="ok", detail="")


def _err_result(name="crear_archivo"):
    from core.tool_result import ToolResult
    return ToolResult(tool_name=name, summary="error", is_error=True)


def test_worker_sin_hook_no_verifica(qapp):
    w = _make_worker(None)
    tr = _ok_result()
    assert w._maybe_verify("crear_archivo", {"nombre": "x.py"}, tr) is tr


def test_worker_con_hook_anexa_verificacion(qapp):
    def hook(rel):
        return f"{rel}: 1 problema"
    w = _make_worker(hook)
    tr = _ok_result()
    out = w._maybe_verify("crear_archivo", {"nombre": "x.py"}, tr)
    assert "[VERIFICACIÓN]" in out.detail
    assert "x.py" in out.detail


def test_worker_hook_silencioso_si_ok(qapp):
    def hook(rel):
        return ""
    w = _make_worker(hook)
    tr = _ok_result()
    assert w._maybe_verify("crear_archivo", {"nombre": "x.py"}, tr).detail == ""


def test_worker_hook_solo_en_escrituras(qapp):
    def hook(rel):
        return "no deberia llamarse"
    w = _make_worker(hook)
    tr = _ok_result("leer_archivo")
    out = w._maybe_verify("leer_archivo", {"nombre": "x.py"}, tr)
    assert out.detail == ""


def test_worker_hook_no_anexa_si_error(qapp):
    def hook(rel):
        return "no deberia llamarse"
    w = _make_worker(hook)
    tr = _err_result()
    assert w._maybe_verify("crear_archivo", {"nombre": "x.py"}, tr).detail == ""


def test_worker_hook_no_rompe_flujo_si_falla(qapp):
    def hook(rel):
        raise RuntimeError("boom")
    w = _make_worker(hook)
    tr = _ok_result()
    out = w._maybe_verify("crear_archivo", {"nombre": "x.py"}, tr)
    assert out.detail == ""


# -- config -----------------------------------------------------------


def test_config_verificador_default_off(tmp_path, monkeypatch):
    from core import config as config_mod
    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "c.json")
    assert config_mod.AppConfig.load().verificador_enabled is False


def test_config_verificador_round_trip(tmp_path, monkeypatch):
    from core import config as config_mod
    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "c.json")
    config_mod.AppConfig(verificador_enabled=True).save()
    assert config_mod.AppConfig.load().verificador_enabled is True
    