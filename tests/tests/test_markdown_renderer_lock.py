"""Regresión M5: to_html() es thread-safe.

Antes, `_md.reset()` + `_md.convert()` mutaban el estado interno del
objeto `markdown.Markdown` global sin protección. Llamarlo desde dos
hilos concurrentes corrompía ese estado. Hoy solo se llama desde el
hilo UI, pero el contrato del módulo no lo garantizaba.

Ahora hay un `threading.Lock` que serializa las secciones críticas.
"""
from __future__ import annotations

import threading

from ui.rendering.markdown_renderer import to_html


def test_concurrent_to_html_does_not_corrupt_state():
    """Muchos hilos llamando a to_html no deben producir errores."""
    errors: list[BaseException] = []
    results: list[str] = []
    lock = threading.Lock()

    samples = [
        "# Título",
        "Texto normal con **negrita**.",
        "```python\nprint('hola')\n```",
        "- item 1\n- item 2\n- item 3",
        "| a | b |\n|---|---|\n| 1 | 2 |",
        "Texto con `código inline`.",
        "> cita de ejemplo",
    ]

    def worker(sample: str) -> None:
        try:
            html = to_html(sample)
            with lock:
                results.append(html)
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    # 50 hilos × 20 iteraciones sobre muestras alternas.
    threads = []
    for i in range(50):
        for _ in range(20):
            t = threading.Thread(
                target=worker,
                args=(samples[i % len(samples)],),
                daemon=True,
            )
            threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors, f"errores en to_html concurrente: {errors[:3]}"
    assert len(results) == len(threads)


def test_single_thread_still_works():
    """El caso normal sigue funcionando."""
    html = to_html("# Hola\n\nMundo.")
    assert "<h1>" in html
    assert "Mundo" in html
    