"""Convierte Markdown a HTML compatible con QTextEdit.

QTextEdit no es un navegador: soporta un subconjunto de HTML 4 y CSS 2.1.
Esta capa traduce el Markdown que generan los LLMs a ese subconjunto,
evitando etiquetas que QTextEdit ignora o renderiza mal.

Usa python-markdown con extensiones:
  · fenced_code  → bloques ```...```
  · tables       → tablas GFM
  · sane_lists   → evita comportamientos raros con listas anidadas

Bloques de código: los resalta con Pygments usando estilos inline
(noclasses=True), porque QTextEdit no aplica <style> con clases CSS
externas. Se preservan los saltos de línea, la indentación y los
caracteres especiales.

Fallback seguro: si Pygments no reconoce el lenguaje, el bloque se
deja como <code> plano, sin highlighting. Nunca falla por un lenguaje
desconocido.

Los estilos de los demás elementos (títulos, listas, tablas, etc.)
viven en ui/theme.py (DOCUMENT_STYLESHEET) y se aplican vía
QTextDocument.setDefaultStyleSheet.
"""
from __future__ import annotations

import html as html_lib
import logging
import re

import markdown
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound


logger = logging.getLogger(__name__)


_md = markdown.Markdown(
    extensions=["fenced_code", "tables", "sane_lists"],
    output_format="html",
)

# Formatter de Pygments con estilos inline (noclasses=True), sin <pre>
# (nowrap=True) y con saltos de línea como <br> para que QTextEdit los
# respete (QTextEdit no interpreta white-space: pre).
# Pygments escapa el `lineseparator` si se lo pasamos al formatter
# (lo convierte en &lt;br&gt;). Asi que usamos el default (\n) y
# sustituimos por <br> DESPUES, en _highlight_code_block.
_pygments_formatter = HtmlFormatter(
    noclasses=True,
    nowrap=True,
)

# Detecta bloques <code class="language-XXX">...</code> generados por
# la extensión fenced_code de python-markdown.
_CODE_BLOCK = re.compile(
    r'<code class="language-([\w.+-]+)">(.*?)</code>',
    re.DOTALL,
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

    Pasos:
      1. Eliminar <script> y <style> (QTextEdit no los ejecuta/aplica).
      2. Resaltar bloques de código con Pygments (estilos inline).
      3. Quitar cualquier <code class="..."> que haya quedado sin
         procesar (red de seguridad).
      4. Normalizar <br/> a <br>.
    """
    raw = re.sub(
        r"<script[^>]*>.*?</script>", "", raw, flags=re.DOTALL | re.IGNORECASE
    )
    raw = re.sub(
        r"<style[^>]*>.*?</style>", "", raw, flags=re.DOTALL | re.IGNORECASE
    )
    raw = _CODE_BLOCK.sub(_highlight_code_block, raw)
    # Red de seguridad: si algún <code class="..."> sobrevivió al paso
    # anterior (por ejemplo, un lenguaje con caracteres raros que el
    # regex no capturó), lo dejamos plano.
    raw = re.sub(r'<code class="[^"]*">', "<code>", raw)
    raw = raw.replace("<br/>", "<br>").replace("<br />", "<br>")
    return raw


def _highlight_code_block(match: re.Match) -> str:
    """Resalta un bloque de código con Pygments.

    El contenido del bloque está escapado por python-markdown (por
    ejemplo, "&lt;" en lugar de "<"), así que hay que deshacer el
    escape antes de pasárselo a Pygments. Pygments volverá a escapar
    su salida (bien, porque va dentro de HTML).

    Si el lenguaje no se reconoce o Pygments falla por cualquier
    motivo, devuelve el bloque original sin highlighting. Nunca lanza.
    """
    lang = match.group(1)
    escaped_code = match.group(2)

    # Deshacer el escape HTML que aplicó markdown.
    code = html_lib.unescape(escaped_code)

    # Normalizar tabuladores a espacios: QTextEdit colapsa \t.
    code = code.replace("\t", "    ")

    try:
        lexer = get_lexer_by_name(lang)
    except ClassNotFound:
        logger.debug("Pygments no reconoce el lenguaje %r", lang)
        return match.group(0)
    except Exception:
        logger.exception("Error obteniendo lexer para %r", lang)
        return match.group(0)

    try:
        rendered = highlight(code, lexer, _pygments_formatter)
    except Exception:
        logger.exception("Error resaltando bloque de código %r", lang)
        return match.group(0)

    # Sustituir saltos de linea por <br> DESPUES de Pygments: si se lo
    # pasamos como lineseparator, lo escapa a &lt;br&gt;.
    rendered = rendered.replace("\n", "<br>")
    rendered = _preserve_whitespace(rendered)
    return f"<code>{rendered}</code>"


def _preserve_whitespace(rendered: str) -> str:
    """Sustituye secuencias de 2+ espacios por &nbsp; para preservar la
    indentación.

    HTML colapsa múltiples espacios consecutivos en uno solo, y
    QTextEdit lo respeta. En bloques de código, la indentación es
    semántica. Sustituimos solo secuencias de 2+ espacios para no
    romper el wrapping de palabras individuales.
    """
    return re.sub(r"  +", lambda m: "&nbsp;" * len(m.group(0)), rendered)
