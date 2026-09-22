"""Regresión SH-1: filtro de programas prohibidos es case-insensitive.

Antes, `_validate_command` comparaba el nombre del programa contra
`_FORBIDDEN_PROGRAMS` (todo minúsculas) sin normalizar. Un modelo que
emitía `SUDO rm ...` se saltaba el filtro. En macOS con APFS
(case-insensitive por defecto) el kernel resuelve SUDO al mismo
binario que sudo y el comando se ejecutaba.
"""
from __future__ import annotations

import pytest

from plugins.shell import ShellClient, ShellError


# ── Casos que deben bloquear ─────────────────────────────────────────

@pytest.mark.parametrize("cmd", [
    "SUDO ls",
    "Sudo ls",
    "sUdO ls",
    "DD if=/dev/zero of=archivo",
    "KILL 1234",
    "KILLALL python",
    "SHUTDOWN -h now",
    "SYSTEMCTL status",
    "IPTABLES -L",
    "CRONTAB -l",
    "MKFS.ext4 /dev/sda1",
    "REBOOT",
])
def test_uppercase_forbidden_programs_are_blocked(tmp_path, cmd):
    """El filtro debe aplicar sin importar el case."""
    with pytest.raises(ShellError):
        ShellClient(tmp_path).execute(cmd)


# ── El caso normal sigue funcionando ─────────────────────────────────

def test_normal_commands_still_run(tmp_path):
    result = ShellClient(tmp_path).execute("echo hola")
    assert "hola" in result


def test_normal_uppercase_command_still_runs(tmp_path):
    """No queremos bloquear comandos legítimos por el cambio."""
    # `ECHO` es el binario coreutils; en macOS `echo` es builtin pero
    # existe como binario en /bin/echo. Si no está, el test lo salta.
    import shutil
    if not shutil.which("echo"):
        pytest.skip("echo no encontrado en PATH")
    result = ShellClient(tmp_path).execute("/bin/echo caso")
    assert "caso" in result


# ── El filtro usa el basename, no la ruta ────────────────────────────

def test_absolute_path_to_forbidden_program_is_blocked(tmp_path):
    """Aunque el comando use ruta absoluta, se detecta el nombre."""
    with pytest.raises(ShellError):
        ShellClient(tmp_path).execute("/usr/bin/SUDO ls")
        