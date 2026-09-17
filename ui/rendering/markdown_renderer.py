"""Convierte Markdown a HTML compatible con QTextEdit.

QTextEdit no es un navegador: soporta un subconjunto de HTML 4 y CSS 2.1.
Esta capa traduce el Markdown que generan los LLMs a ese subconjunto,
evitando etiquetas que QTextEdit ignora o renderiza mal.

Usa python-markdown con extensiones:
  · fenced_code  → bloques ```...```
  · tables       → tablas GFM
  · sane_lists   → evita comportamientos raros con listas anidadas

Los estilos (colores, fuentes, márgenes) viven en ui/theme.py
(DOCUMENT_STYLESHEET) y se aplican vía QTextDocument.setDefaultStyleSheet.
"""
from __future__ import annotations

import re

import markdown


_md = markdown.Markdown(
    extensions=["fenced_code", "tables", "sane_lists"],
    output_format="html",
)


def to_html(text: str) -> str:
    """Convierte texto Markdown a HTML compatible con QTextEdit."""
    if not text or not text.strip():
        return ""

    _md.reset()
    raw = _md.convert(text)
    return _sanitize(raw)


def _sanitize(raw: str) -> str:
    """Adapta el HTML generado al subconjunto que QTextEdit soporta.

    QTextEdit no ejecuta scripts ni aplica <style>, así que los
    eliminamos. También normalizamos <br/> a <br> y quitamos las
    clases language-* de los bloques de código (no las usa).
    """
    raw = re.sub(
        r"<script[^>]*>.*?</script>", "", raw, flags=re.DOTALL | re.IGNORECASE
    )
    raw = re.sub(
        r"<style[^>]*>.*?</style>", "", raw, flags=re.DOTALL | re.IGNORECASE
    )
    raw = re.sub(r'<code class="[^"]*">', "<code>", raw)
    raw = raw.replace("<br/>", "<br>").replace("<br />", "<br>")
    return raw
