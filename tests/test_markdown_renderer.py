"""Tests del renderizado de Markdown con Pygments."""
from __future__ import annotations

from ui.rendering.markdown_renderer import to_html


# ── Bloques de codigo con highlighting ────────────────────────────────

def test_python_code_block_has_highlighting():
    md = "```python\ndef foo():\n    return 42\n```"
    html = to_html(md)
    assert 'style="color:' in html
    assert "def" in html
    assert "42" in html


def test_code_block_preserves_indentation():
    md = "```python\ndef outer():\n    def inner():\n        return 1\n```"
    html = to_html(md)
    assert "&nbsp;" in html


def test_code_block_preserves_newlines():
    md = "```python\nline1\nline2\nline3\n```"
    html = to_html(md)
    assert "<br>" in html


def test_code_block_escapes_html_characters():
    md = "```python\nif a < b and c > d:\n    pass\n```"
    html = to_html(md)
    assert "&lt;" in html
    assert "&gt;" in html


def test_code_block_with_unknown_language_falls_back():
    md = "```foobar-inexistente\nsome code\n```"
    html = to_html(md)
    assert "some code" in html
    assert 'style="color:' not in html


def test_code_block_without_language_no_highlighting():
    md = "```\nplain code\n```"
    html = to_html(md)
    assert "plain code" in html
    assert 'style="color:' not in html


def test_multiple_code_blocks_each_highlighted():
    md = (
        "```python\nprint(\"hola\")\n```\n\n"
        "Texto entre bloques.\n\n"
        "```javascript\nconsole.log(\"mundo\")\n```"
    )
    html = to_html(md)
    assert html.count('style="color:') > 0
    assert "print" in html
    assert "console" in html


def test_inline_code_not_highlighted():
    md = "Usa ```print(x)``` para imprimir."
    html = to_html(md)
    assert "print(x)" in html
    assert 'style="color:' not in html


# ── Interaccion con otros elementos Markdown ──────────────────────────

def test_headings_and_lists_still_work():
    md = "# Titulo\n\n- uno\n- dos\n- tres"
    html = to_html(md)
    assert "<h1>" in html
    assert "<ul>" in html
    assert "<li>uno</li>" in html


# ── Seguridad ─────────────────────────────────────────────────────────

def test_script_tag_is_removed():
    md = "Texto normal <script>alert(1)</script> mas texto"
    html = to_html(md)
    assert "<script>" not in html


def test_style_tag_is_removed():
    md = "Texto <style>body { color: red; }</style>"
    html = to_html(md)
    assert "<style>" not in html


def test_code_block_does_not_break_on_complex_content():
    md = "```python\nimport re\npattern = re.compile(r\"<[^>]+>\")\ntext = \"a < b > c\"\nresult = pattern.findall(text)\n```"
    html = to_html(md)
    assert "pattern" in html
    assert "&lt;" in html


def test_empty_input_returns_empty():
    assert to_html("") == ""
    assert to_html("   ") == ""
