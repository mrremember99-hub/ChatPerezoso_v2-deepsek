# Rendering Markdown — qué HTML se permite

**Código**: `ui/rendering/markdown_renderer.py`
**Destino**: `QTextEdit` (Qt)
**Última revisión**: septiembre 2026 (auditoría externa, P2#8)

## Contexto

El modelo (Ollama) genera Markdown; lo convertimos a HTML para
`QTextEdit`. **QTextEdit no es un navegador**: soporta un subconjunto
de HTML 4 y CSS 2.1, no ejecuta JavaScript, no carga recursos
externos (imágenes remotas, CSS, fuentes) y no sigue redirecciones.

Esta capa traduce el Markdown al subconjunto que QTextEdit entiende.

## Pipeline

1. `python-markdown` con extensiones `fenced_code`, `tables`,
   `sane_lists`, `output_format="html"`.
2. `_sanitize(raw)`:
   - **Elimina** `<script>...</script>` y `<style>...</style>`.
   - Resalta bloques de código con Pygments (estilos inline).
   - Colapsa `<code class="...">` residual a `<code>` plano.
   - Normaliza `<br/>` y `<br />` a `<br>`.
3. Inserción en `QTextEdit` vía `QTextDocument.setHtml`.

## Qué HTML raw se permite

**Todo HTML raw excepto `<script>` y `<style>`.** El modelo puede
emitir directamente:

- `<a href="...">` → enlace (QTextEdit lo muestra subrayado, no
  navega a menos que se conecte `anchorClicked`, lo cual **no está
  hecho**).
- `<img src="...">` → **QTextEdit ignora `src` remoto**. No hay
  petición HTTP.
- `<table>`, `<tr>`, `<td>` → renderizado nativo.
- `<b>`, `<i>`, `<u>`, `<code>`, `<pre>` → soportados.
- `<div>`, `<span>` con `style="..."` inline → aplicado por
  `QTextDocument` (solo propiedades CSS 2.1 soportadas).
- `<iframe>`, `<embed>`, `<object>` → **QTextEdit no los renderiza**.

## Por qué el riesgo es bajo

El vector de ataque típico de HTML raw es XSS: `<script>` ejecuta JS
en el contexto del documento. **QTextEdit no ejecuta JavaScript.**
Un `<script>` embebido se ignora silenciosamente; `_sanitize` lo
elimina igualmente como defensa en profundidad por si el destino de
render cambia en el futuro (p. ej. a un `QWebEngineView`).

El segundo vector es exfiltración vía `<img src="http://evil/">`: el
navegador haría la petición con la IP del usuario. **QTextEdit no
carga recursos externos.** Un `<img src="...">` se renderiza como
imagen rota (o vacío) sin petición HTTP.

El tercer vector es CSS malicioso que oculte contenido o cambie el
layout. Solo se aplican las propiedades CSS 2.1 soportadas por
`QTextDocument`; `<style>` se elimina por completo, y estilos inline
se limitan a lo que Qt interpreta.

## Qué NO se permite (y por qué)

| Etiqueta | Acción | Razón |
|---|---|---|
| `<script>` | Eliminada | XSS en cualquier destino HTML |
| `<style>` | Eliminada | CSS arbitrario puede ocultar/suplantar UI |
| `<link rel="stylesheet">` | No procesada | QTextDocument no carga CSS externo |

Todo lo demás pasa. Si en el futuro se migra el render a
`QWebEngineView` (que sí ejecuta JS), **habría que ampliar la
allowlist** y probablemente migrar a `bleach` o `nh3`. Hoy no es el
caso.

## Cambios futuros

Si se añade un visor web (QWebEngineView) o exportación a HTML que
se abra en un navegador real, `_sanitize` deja de ser suficiente.
En ese escenario:

1. Migrar a `bleach` o `nh3` con allowlist explícita.
2. Test de regresión con payloads XSS conocidos.
3. Marcar este documento como obsoleto y reescribir con la nueva
   política.

Hoy el contexto (`QTextEdit`) neutraliza las amenazas residuales, y
por eso la política es "todo pasa excepto script/style" en lugar de
una allowlist estricta.
