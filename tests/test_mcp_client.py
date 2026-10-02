

# ── X1.1: cleanup MCP no mata procesos ajenos sin hint ─────────────


def test_kill_orphans_no_mata_sin_workspace_hint(monkeypatch):
    """Sin un arg con '/' o '\\', no matamos nada. Preferimos
    dejar huerfanos a matar procesos MCP de otros proyectos."""
    import sys
    from plugins.mcp import client as mc

    # MCPClient necesita server con .args
    class FakeServer:
        command = "npx"
        args = ["-y", "@modelcontextprotocol/server-filesystem"]  # sin /

    # psutil falso: si alguien llama terminate, petamos el test
    terminated = []

    class FakeProc:
        def __init__(self, cmdline):
            self.info = {
                "pid": 99999,
                "name": "node",
                "cmdline": cmdline,
            }
        def terminate(self):
            terminated.append(self.info["cmdline"])
        def wait(self, timeout=None):
            pass
        def kill(self):
            terminated.append("KILL:" + " ".join(self.info["cmdline"]))

    import psutil as real_psutil
    def fake_process_iter(_fields):
        return [
            FakeProc(["node", "/usr/local/bin/mcp-server-filesystem"]),
            FakeProc(["node", "/otro/proyecto/mcp-server-otros"]),
        ]
    monkeypatch.setattr(real_psutil, "process_iter", fake_process_iter)

    # Creamos un MCPClient saltandonos __init__ (que arranca SDK).
    cli = mc.MCPClient.__new__(mc.MCPClient)
    cli.server = FakeServer()

    cli._kill_orphan_mcp_processes()
    assert terminated == [], f"No deberia matar nada, mato: {terminated}"


def test_kill_orphans_si_mata_con_hint(monkeypatch):
    """Con workspace_hint, solo mata los que coinciden."""
    import sys
    from plugins.mcp import client as mc

    class FakeServer:
        command = "npx"
        args = ["-y", "@scope/pkg"]  # paquete npm, no workspace
        # P3#11 (2026-10-02): el identificador del workspace
        # ahora viene de `cwd` (preferente) o del ultimo arg
        # que sea un directorio absoluto existente.
        cwd = "/Users/x/ws"

    terminated = []

    class FakeProc:
        def __init__(self, cmdline):
            self.info = {"pid": 1, "name": "node", "cmdline": cmdline}
        def terminate(self):
            terminated.append(" ".join(self.info["cmdline"]))
        def wait(self, timeout=None):
            pass
        def kill(self):
            terminated.append("KILL")

    import psutil as real_psutil
    def fake_process_iter(_fields):
        return [
            FakeProc(["node", "mcp-server-", "/Users/x/ws"]),   # match
            FakeProc(["node", "mcp-server-", "/Users/otro/ws"]),  # no match
        ]
    monkeypatch.setattr(real_psutil, "process_iter", fake_process_iter)

    cli = mc.MCPClient.__new__(mc.MCPClient)
    cli.server = FakeServer()

    cli._kill_orphan_mcp_processes()
    assert len(terminated) == 1
    assert "/Users/x/ws" in terminated[0]
