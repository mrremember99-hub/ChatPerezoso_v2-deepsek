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
    assert defs[0]["function"]["name"] == "verificar_codigo"


def test_provider_nunca_requiere_confirmacion(tmp_path):
    p = VerificadorProvider(Workspace(tmp_path))
    assert not p.requires_confirmation("verificar_codigo")


def test_provider_intent_rules_tiene_regla(tmp_path):
    """El proveedor declara regla para verificar_codigo.

    Antes devolvia {} y el gate bloqueaba la tool siempre (H4 del
    informe out(1)).
    """
    p = VerificadorProvider(Workspace(tmp_path))
    rules = p.intent_rules()
    assert "verificar_codigo" in rules
    rule = rules["verificar_codigo"]
    assert "verifica" in rule.verbs
    assert "comprueba" in rule.verbs


def test_gate_autoriza_verifica_con_archivo(tmp_path):
    """El gate autoriza cuando el usuario dice 'verifica X.py'."""
    from core.intent import ToolIntentGate

    rules = VerificadorProvider(Workspace(tmp_path)).intent_rules()
    gate = ToolIntentGate(rules)
    assert gate.tool_is_requested("verificar_codigo", "verifica foo.py")
    assert gate.tool_is_requested(
        "verificar_codigo", "comprueba el archivo principal"
    )


def test_gate_no_autoriza_sin_verbo(tmp_path):
    from core.intent import ToolIntentGate

    rules = VerificadorProvider(Workspace(tmp_path)).intent_rules()
    gate = ToolIntentGate(rules)
    assert not gate.tool_is_requested(
        "verificar_codigo", "crea un archivo nuevo"
    )


# -- provider: call ---------------------------------------------------


def test_provider_verifica_archivo_valido(tmp_path):
    p = VerificadorProvider(Workspace(tmp_path))
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    out = p.call("verificar_codigo", {"archivo": "ok.py"})
    assert out.startswith("OK")


def test_provider_reporta_error(tmp_path):
    p = VerificadorProvider(Workspace(tmp_path))
    (tmp_path / "bad.py").write_text("def f(:\n", encoding="utf-8")
    out = p.call("verificar_codigo", {"archivo": "bad.py"})
    assert "bad.py" in out
    assert "problema" in out


def test_provider_acepta_varios_nombres_de_campo(tmp_path):
    p = VerificadorProvider(Workspace(tmp_path))
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    assert p.call("verificar_codigo", {"nombre": "ok.py"}).startswith("OK")
    assert p.call("verificar_codigo", {"path": "ok.py"}).startswith("OK")
    assert p.call("verificar_codigo", {"ruta": "ok.py"}).startswith("OK")


def test_provider_falta_archivo(tmp_path):
    p = VerificadorProvider(Workspace(tmp_path))
    out = p.call("verificar_codigo", {})
    assert "ERROR" in out


def test_provider_archivo_inexistente(tmp_path):
    p = VerificadorProvider(Workspace(tmp_path))
    out = p.call("verificar_codigo", {"archivo": "no_existe.py"})
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
    
    

# -- calidad (ruff + mypy, opcional) ----------------------------------


def test_check_quality_python_valido(tmp_path):
    from plugins.verificador import check_quality
    f = tmp_path / "ok.py"
    f.write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    issues = check_quality(f)
    assert isinstance(issues, list)


def test_check_quality_archivo_no_python(tmp_path):
    from plugins.verificador import check_quality
    f = tmp_path / "x.js"
    f.write_text("const x = 1;\n", encoding="utf-8")
    assert check_quality(f) == []


def test_check_quality_detecta_import_sin_usar(tmp_path):
    import shutil
    from plugins.verificador import check_quality
    if shutil.which("ruff") is None:
        import pytest
        pytest.skip("ruff no instalado")
    f = tmp_path / "x.py"
    f.write_text("import os\n\nx = 1\n", encoding="utf-8")
    issues = check_quality(f)
    codes = {i.code for i in issues}
    assert "F401" in codes


# -- secretos ---------------------------------------------------------


def test_scan_secrets_aws_key(tmp_path):
    from plugins.verificador import scan_secrets
    f = tmp_path / "x.py"
    f.write_text("AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'\n", encoding="utf-8")
    issues = scan_secrets(f)
    assert len(issues) == 1
    assert issues[0].kind == "AWS access key"
    assert issues[0].line == 1


def test_scan_secrets_github_token(tmp_path):
    from plugins.verificador import scan_secrets
    f = tmp_path / "x.py"
    f.write_text(
        "TOKEN = 'ghp_" + "A" * 40 + "'\n", encoding="utf-8"
    )
    issues = scan_secrets(f)
    assert any(i.kind == "GitHub token" for i in issues)


def test_scan_secrets_sin_falsos_positivos(tmp_path):
    from plugins.verificador import scan_secrets
    f = tmp_path / "x.py"
    f.write_text("x = 'hola mundo'\ny = 42\n", encoding="utf-8")
    assert scan_secrets(f) == []


def test_scan_secrets_enmascara(tmp_path):
    from plugins.verificador import scan_secrets
    f = tmp_path / "x.py"
    f.write_text("K = 'AKIAIOSFODNN7EXAMPLE'\n", encoding="utf-8")
    issues = scan_secrets(f)
    assert "…" in issues[0].snippet
    assert "AKIAIOSFODNN7EXAMPLE" not in issues[0].snippet


# -- conflictos git ---------------------------------------------------


def test_check_conflicts_detecta_marcadores(tmp_path):
    from plugins.verificador import check_conflicts
    f = tmp_path / "x.py"
    f.write_text(
        "a = 1\n<<<<<<< HEAD\nb = 2\n=======\nb = 3\n>>>>>>> otra\n",
        encoding="utf-8",
    )
    issues = check_conflicts(f)
    assert len(issues) == 3
    markers = {i.marker for i in issues}
    assert markers == {"<<<<<<<", "=======", ">>>>>>>"}


def test_check_conflicts_sin_falsos_positivos(tmp_path):
    from plugins.verificador import check_conflicts
    f = tmp_path / "x.py"
    f.write_text("# ======== seccion ========\nx = 1\n", encoding="utf-8")
    assert check_conflicts(f) == []


# -- orquestador ------------------------------------------------------


def test_verify_all_devuelve_4_claves(tmp_path):
    from plugins.verificador import verify_all
    f = tmp_path / "x.py"
    f.write_text("x = 1\n", encoding="utf-8")
    r = verify_all(f)
    assert set(r.keys()) == {"syntax", "quality", "secret", "conflict"}


def test_verify_all_archivo_roto_reporta(tmp_path):
    from plugins.verificador import verify_all
    f = tmp_path / "x.py"
    f.write_text("def f(:\n", encoding="utf-8")
    r = verify_all(f)
    assert len(r["syntax"]) >= 1


def test_verify_all_archivo_con_secreto(tmp_path):
    from plugins.verificador import verify_all
    f = tmp_path / "x.py"
    f.write_text("K = 'AKIAIOSFODNN7EXAMPLE'\n", encoding="utf-8")
    r = verify_all(f)
    assert len(r["secret"]) >= 1


# -- provider: nueva API ----------------------------------------------


def test_provider_tool_se_llama_verificar_codigo(tmp_path):
    p = VerificadorProvider(Workspace(tmp_path))
    defs = p.definitions()
    assert defs[0]["function"]["name"] == "verificar_codigo"


def test_provider_call_ok_si_limpio(tmp_path):
    p = VerificadorProvider(Workspace(tmp_path))
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    out = p.call("verificar_codigo", {"archivo": "ok.py"})
    assert out.startswith("OK")


def test_provider_call_reporta_secreto(tmp_path):
    p = VerificadorProvider(Workspace(tmp_path))
    (tmp_path / "x.py").write_text(
        "K = 'AKIAIOSFODNN7EXAMPLE'\n", encoding="utf-8"
    )
    out = p.call("verificar_codigo", {"archivo": "x.py"})
    assert "[secret]" in out