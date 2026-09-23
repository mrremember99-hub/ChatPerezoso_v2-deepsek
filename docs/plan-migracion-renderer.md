# Plan de migración del renderer (`QTextEdit` -> `QScrollArea` + widgets)

Estado: **fase de diseño**. Sin código cambiado.
Origen: hallazgos UI-02, UI-03, UI-05, UI-06, UI-07 del informe 6.

## Motivación

`PlainTextRenderer` gestiona hoy un único `QTextEdit` con un único
`QTextDocument` que contiene toda la conversación. Cada operación
(`on_text`, `insert_tool_card`, `insert_narration`, `_render_markdown_block`)
muta ese documento único, y el coste del layout crece con el tamaño
total del documento, no con el tamaño del mensaje nuevo.

Mitigaciones ya aplicadas:

- Fase 2.2: trocear Markdown durante streaming -> pico final de 26.6 ms a 0.1 ms.
- `setMaximumBlockCount(5000)`: cap duro del documento.
- Batching en ChatController (32 ms).
- `ensureCursorVisible` guardado por `at_bottom`.

## Lo que NO está resuelto

- Coste de mutación que crece con el tamaño total del documento.
- Pygments bloquea el hilo GUI al renderizar cada bloque de código.
- `_render_markdown_block` hace delete + insert (doble trabajo de layout).
- Restaurar 200 mensajes al arrancar es síncrono.
- Posiciones de cursor pueden quedar inválidas si `setMaximumBlockCount`
  elimina bloques antiguos.

## Arquitectura objetivo

    ChatPanel
    └── QScrollArea
        └── QWidget (container)
            └── QVBoxLayout
                ├── UserMessageWidget
                ├── AssistantMessageWidget
                ├── ToolCardWidget
                └── ...

El streaming de la respuesta activa afecta solo a su widget. El resto
de la conversación es estático. El coste de cada drain no crece con la
longitud de la conversación.

## Decisión arquitectónica

**Opción elegida:** un `QTextBrowser` por mensaje.

Motivos:

- Reutiliza `markdown_renderer.to_html()` sin tocar.
- Soporta HTML completo (tablas, código, indentación).
- Selección de texto funcional.
- Copiar/pegar natural.

Alternativas descartadas:

- **QLabel con RichText:** más ligero, pero downgrade funcional
  (sin tablas complejas, sin código con indentación).
- **Híbrido QLabel + QTextBrowser:** dos rutas de render, más bugs.
- **Lazy rendering desde el día 1:** complica el primer paso sin
  evidencia de que haga falta. Se puede añadir después si el benchmark
  lo justifica.

## Preservación de invariantes

| Invariante | Cómo se preserva |
| --- | --- |
| Streaming incremental | Delta al widget activo |
| Troceo de Markdown (Fase 2.2) | Cierre de widget + apertura al cruzar frontera |
| Guard de posiciones (H15) | Reemplazado por referencia al widget activo |
| setMaximumBlockCount(5000) | Eliminado: cada widget acotado por tamaño |
| remove_from_last_user | Identifica último widget de usuario y borra posteriores |
| Auto-scroll solo si abajo | ensureWidgetVisible con check previo |

## Riesgos conocidos

1. Selección cross-widget: no se puede seleccionar texto que cruce dos
   mensajes. Aceptado (ChatGPT/Claude tienen el mismo límite).
2. Buscar en conversación (Ctrl+F): se elimina de momento.
3. Rendimiento con 200 QTextBrowser: aceptable en M4 24GB. Se mide antes
   de decidir si hace falta lazy.
4. QScrollArea + layout complejo: hay que gestionar bien los sizeHint.
5. Markdown + Pygments en hilo GUI: no mejora con esta migración. Se
   resuelve aparte si llega a ser problema.

## Plan por fases

| Fase | Contenido | Tiempo |
| --- | --- | --- |
| B-1 | Interfaz abstracta ChatRenderer sin Qt | 1 h |
| B-2 | WidgetListRenderer en paralelo | 2 h |
| B-2b | Benchmark comparativo | 30 min |
| B-3 | Sustitución en ChatPanel | 1 h |
| B-4 | Limpieza y documentación | 30 min |
| B-5 | Extras (burbujas, hover) | opcional |

Total: ~5 h 30 min.

## Criterio de éxito

Antes de B-3 (sustitución), un benchmark que genere una conversación de
100 mensajes con 2 bloques de código cada uno y mida:

1. Tiempo de construir la lista completa.
2. Tiempo de añadir 1 mensaje nuevo (streaming).
3. Tiempo de scroll arriba/abajo.

**Criterio:** el tiempo de añadir un mensaje nuevo no debe crecer con el
número de mensajes previos. Si con 100 mensajes tarda igual que con 10,
la migración está justificada. Si no mejora respecto a QTextEdit actual,
se descarta y se documenta.

## Orden recomendado

1. Interfaz abstracta (B-1).
2. Implementación en paralelo (B-2).
3. Benchmark (B-2b).
4. Decidir con datos: sustituir (B-3) o descartar.
5. Limpieza (B-4).

## Contrato del renderer (B-Fase 1)

Cualquier renderer debe implementar el Protocol `ChatRenderer` de
`ui/rendering/protocol.py`. Esta es la lista completa de metodos y
propiedades:

| Miembro | Tipo | Uso |
| --- | --- | --- |
| `response_text` | property -> str | Texto completo acumulado del segmento |
| `response_start` | property -> int\|None | Posicion de inicio del segmento |
| `reset()` | metodo | Reset completo del renderer |
| `reset_response_segment()` | metodo | Cierra el segmento actual |
| `insert_user_message(text)` | metodo | Inserta el mensaje del usuario |
| `on_text(text)` | metodo | Delta de streaming |
| `insert_narration(text, active)` | metodo | Narracion del proceso |
| `insert_tool_card(result)` | metodo | Tarjeta de resultado de tool |
| `insert_error(message)` | metodo | Aviso de error |
| `final_text(fallback)` | metodo -> str | Cierra la respuesta, devuelve texto |
| `restore_assistant_message(text)` | metodo | Restaura mensaje historico |
| `remove_from_last_user()` | metodo | Borra desde el ultimo user (regenerar) |

El Protocol es `runtime_checkable`: `isinstance(x, ChatRenderer)`
verifica que los nombres existen, no las firmas. La verificacion
fuerte la hacen los tests funcionales de cada renderer.

### Verificacion

`tests/test_renderer_contract.py` verifica que `PlainTextRenderer`
cumple el Protocol. El futuro `WidgetListRenderer` debe pasar el
mismo test.

### Invariantes que el contrato NO captura

- Batching: el llamante (`ChatController`) es quien agrupa los
  deltas cada 32 ms. El renderer recibe bloques, no tokens.
- `on_text` es incremental: se llama varias veces durante el
  streaming. `final_text` cierra el ciclo.
- `insert_user_message` + `reset` + `on_text`... + `final_text` es
  el ciclo minimo de un turno.
