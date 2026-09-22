"""Regresión A2: el hard cap del buffer es real.

Verifica que:
  · push() bloquea cuando el buffer está lleno (backpressure real).
  · cancel_event desbloquea.
  · un delta >= max_chars se acepta (no se puede descartar texto
    ya generado) y se marca el buffer como lleno.
"""
from __future__ import annotations

import threading
import time

from ui.workers import TextDeltaBuffer


def test_push_blocks_until_drain_actually_frees_space():
    """Con buffer lleno, el siguiente push espera a que se drene."""
    buf = TextDeltaBuffer(max_chars=100)
    buf.push("a" * 100)  # llena exactamente

    done = threading.Event()

    def pusher():
        buf.push("b" * 50)
        done.set()

    t = threading.Thread(target=pusher, daemon=True)
    t.start()

    # Debe seguir bloqueado tras 200 ms.
    assert not done.wait(timeout=0.2), "push deberia haber bloqueado"

    # Drenar libera espacio.
    buf.drain()

    # El push debe completarse.
    assert done.wait(timeout=1.0), "push no se desbloqueo tras drain"


def test_cancel_event_unblocks_push():
    """cancel_event desbloquea el push."""
    buf = TextDeltaBuffer(max_chars=10)
    buf.push("x" * 20)

    cancel = threading.Event()
    returned: list[bool] = []

    def pusher():
        result = buf.push("y", cancel_event=cancel)
        returned.append(result)

    t = threading.Thread(target=pusher, daemon=True)
    t.start()
    t.join(timeout=0.2)
    assert t.is_alive(), "push deberia haber bloqueado"

    cancel.set()
    t.join(timeout=1.0)
    assert not t.is_alive(), "push no se desbloqueo con cancel"


def test_delta_bigger_than_max_is_accepted():
    """Un delta gigante no se descarta; se acepta y marca el buffer lleno."""
    buf = TextDeltaBuffer(max_chars=100)
    # Delta que no cabe ni vaciando: mayor que max_chars.
    result = buf.push("z" * 200)
    assert result is True  # paso de vacio a no vacio
    assert buf.pending_chars == 200


def test_hard_cap_is_respected_for_normal_chunks():
    """El buffer no debe crecer por encima de max_chars con chunks normales."""
    buf = TextDeltaBuffer(max_chars=100)
    buf.push("a" * 80)

    # Este push lo llevaria a 130 -> debe bloquear.
    done = threading.Event()
    threading.Thread(
        target=lambda: (buf.push("b" * 50), done.set()),
        daemon=True,
    ).start()

    time.sleep(0.1)
    assert buf.pending_chars == 80, (
        f"el buffer crecio por encima del cap: {buf.pending_chars}"
    )
    assert not done.is_set()
    