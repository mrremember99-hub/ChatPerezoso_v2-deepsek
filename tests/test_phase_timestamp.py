"""UI backlog: timestamp por fase al detectar FASE N VERIFICADA."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.rendering import plain_text as pt


class _FakeChat:
    """Sustituto minimo de QTextEdit: solo necesitamos las llamadas
    que hace insert_narration y _append_plain_text. Nada de esto se
    ejecuta en los tests: monkeypatcheamos insert_narration."""

    def textCursor(self):  # noqa: N802
        raise RuntimeError("no deberia tocarse en este test")


def _make_renderer(monkeypatch) -> tuple[object, list[str]]:
    r = pt.PlainTextRenderer.__new__(pt.PlainTextRenderer)
    # Estado minimo del renderer (los mismos campos que inicializa
    # __init__ pero solo los que toca _detect_phase_verified).
    r._session_start = None
    r._detect_buffer = ""

    captured: list[str] = []

    def fake_insert(text: str, active: bool = False) -> None:
        captured.append(text)

    monkeypatch.setattr(r, "insert_narration", fake_insert)
    return r, captured


def test_detecta_fase_en_un_solo_delta(monkeypatch):
    r, cap = _make_renderer(monkeypatch)
    r._session_start = 100.0
    monkeypatch.setattr(pt.time, "monotonic", lambda: 100.5)
    r._detect_phase_verified("FASE 3 VERIFICADA · salida: ok")
    assert len(cap) == 1
    assert cap[0].startswith("F3 · 00:00")


def test_detecta_fase_partida(monkeypatch):
    """El texto puede llegar troceado entre deltas."""
    r, cap = _make_renderer(monkeypatch)
    r._session_start = 0.0
    monkeypatch.setattr(pt.time, "monotonic", lambda: 75.0)
    r._detect_phase_verified("FASE ")
    r._detect_phase_verified("12 VERI")
    r._detect_phase_verified("FICADA")
    assert len(cap) == 1
    assert cap[0].startswith("F12 · 01:15")


def test_no_dispara_sin_frase_completa(monkeypatch):
    r, cap = _make_renderer(monkeypatch)
    r._session_start = 0.0
    monkeypatch.setattr(pt.time, "monotonic", lambda: 10.0)
    r._detect_phase_verified("FASE 5")
    r._detect_phase_verified("casi")
    assert cap == []


def test_una_emision_por_fase(monkeypatch):
    """Tras un match se limpia el buffer para no re-emitir."""
    r, cap = _make_renderer(monkeypatch)
    r._session_start = 0.0
    monkeypatch.setattr(pt.time, "monotonic", lambda: 5.0)
    r._detect_phase_verified("FASE 1 VERIFICADA")
    r._detect_phase_verified("FASE 1 VERIFICADA")
    # El segundo paso tambien matchea porque es texto nuevo. Eso es
    # correcto: dos emisiones distintas del modelo = dos narraciones.
    # Solo protegemos contra re-deteccion del mismo texto.
    assert len(cap) == 2


def test_sin_session_start_no_dispara(monkeypatch):
    r, cap = _make_renderer(monkeypatch)
    # _session_start = None (default)
    r._detect_phase_verified("FASE 5 VERIFICADA")
    assert cap == []


def test_formato_minutos_y_segundos(monkeypatch):
    r, cap = _make_renderer(monkeypatch)
    r._session_start = 0.0
    monkeypatch.setattr(pt.time, "monotonic", lambda: 125.0)
    r._detect_phase_verified("FASE 7 VERIFICADA")
    assert cap[0] == "F7 · 02:05"
