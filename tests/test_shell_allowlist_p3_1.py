"""P3#1: bypass allowlist via flags combinados (-Ic, -cm)."""
from __future__ import annotations

from plugins.shell import is_command_allowed


def test_dash_c_exacto_rechazado():
    assert not is_command_allowed("python3 -c 'print(1)'")


def test_dash_ic_rechazado():
    """Caso del hallazgo P3#1: -Ic combinado con codigo inline."""
    assert not is_command_allowed(
        'python3 -Ic "__import__(\'os\').system(\'ls\')"'
    )


def test_dash_ci_rechazado():
    assert not is_command_allowed('python3 -cI "print(1)"')


def test_dash_cm_rechazado():
    assert not is_command_allowed("python3 -cm json.tool")


def test_dash_im_rechazado():
    assert not is_command_allowed("python3 -Im pytest")


def test_dash_i_exacto_rechazado():
    assert not is_command_allowed("python3 -i script.py")


def test_dash_ii_combinado_rechazado():
    assert not is_command_allowed("python3 -Ii script.py")


def test_dash_m_exacto_valido_con_modulo_ok():
    assert is_command_allowed("python3 -m pytest")


def test_dash_m_exacto_rechazado_con_modulo_peligroso():
    assert not is_command_allowed("python3 -m pip install x")


def test_script_normal_sigue_pasando():
    assert is_command_allowed("python3 script.py")


def test_long_flag_interactive_rechazado():
    assert not is_command_allowed("python3 --interactive script.py")


def test_long_flag_config_no_matchea_falso_positivo():
    """--config no debe rechazarse por empezar por -c."""
    # No es python: usamos algo que no rompa el test.
    # Verificamos que el patron no matchea long flags.
    from plugins.shell.allowlist import _PY_FLAG_WITH_DANGEROUS_CHAR
    assert _PY_FLAG_WITH_DANGEROUS_CHAR.match("--config") is None
    assert _PY_FLAG_WITH_DANGEROUS_CHAR.match("--interactive") is None
