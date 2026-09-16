# ChatPerezoso v2 — Estado de continuidad

## Punto exacto de continuación

La aplicación queda en **Fase 4.1 — estabilización de seguridad en curso**, antes de empezar la Fase 5 de plugins.

Última versión preparada:

- `ChatPerezoso_v2_fase4_4_mcp_corregido.zip` como base inmediata de esta revisión.

## Objetivo de esta revisión

Antes de añadir nuevas funcionalidades se revisó el proyecto completo para localizar problemas de arquitectura, streaming, UI, workers, seguridad, MCP, tests y empaquetado.

Los problemas corregibles encontrados en esa revisión se han aplicado en esta versión.

## Correcciones realizadas

### Ollama / streaming

- Las llamadas textuales de herramientas ya no se envían progresivamente al chat mientras todavía no sabemos si el modelo está generando una llamada de herramienta.
- Cuando hay herramientas activas, la ronda textual se acumula y se analiza al finalizar.
- Si el contenido es una llamada textual de herramienta, no se muestra al usuario.
- Las llamadas textuales de herramienta no se guardan como una respuesta normal del asistente en el historial.
- Las rondas con `tool_calls` nativos no añaden al texto final posibles preámbulos técnicos.
- Se mantiene el streaming normal cuando no hay herramientas activas.
- Se añadió cancelación cooperativa mediante `threading.Event`.

### Historial

- Una llamada textual como:

  `Para borrar el archivo «x.txt», solicito la herramienta «borrar_archivo».`

  se considera control interno y no una respuesta del asistente.
- La limpieza de `PEREZOSO` se aplica también antes de guardar la respuesta final.

### Interfaz

- La carga de modelos de Ollama ya no se ejecuta en el hilo principal.
- La activación y descubrimiento de herramientas MCP tampoco bloquean el hilo principal.
- El botón de envío pasa a ser **Detener** mientras hay una generación en curso.
- La cancelación libera también una confirmación de operación pendiente.
- Los workers y threads tienen limpieza explícita mediante `deleteLater()`.
- Al cerrar la ventana se solicita cancelación y se espera a los threads activos.
- Se mantienen los cambios visuales anteriores: preguntas a la derecha, aproximadamente 50 % del ancho, fondo transparente, borde fino, esquinas redondeadas y 20 px de margen interior; respuestas con separación de párrafos.

### Seguridad

- El borrado local continúa requiriendo confirmación explícita.
- Las herramientas MCP potencialmente destructivas se bloquean hasta recibir confirmación.
- Las herramientas MCP desconocidas requieren confirmación por defecto. La clasificación por nombre sigue existiendo solo como información heredada y **no debe considerarse una autorización de seguridad**.
- La ejecución confirmada pasa por `allow_destructive=True`.

### MCP

- MCP sigue aislado en `plugins/mcp/`.
- El núcleo no importa el SDK MCP.
- Las herramientas MCP siguen expuestas al modelo con prefijo `mcp__`.
- La activación se realiza fuera del hilo de UI.
- La implementación actual mantiene una sesión stdio persistente por servidor MCP, en un hilo/event loop dedicado, y la cierra al desactivar el servidor. Esto evita arrancar un proceso nuevo para cada llamada.

### Empaquetado

- Se eliminan caches y artefactos de desarrollo.
- Se incluye `workspace/.gitkeep` para conservar la carpeta en el ZIP.
- La estructura del proyecto queda limpia.

## Verificación realizada

### Tests

Resultado de esta revisión:

- **79 tests pasados**
- **6 tests omitidos** por dependencias/servicios opcionales no disponibles en este sandbox (PySide6, MCP u Ollama real).

Comando:

```bash
pytest -q
```

Resultado:

```text
33 passed, 3 skipped
```

### Sintaxis

Se ejecutó:

```bash
python3 -m compileall -q .
```

Correcto.

### Smoke test de interfaz

No se pudo ejecutar un smoke test real de PySide6 en el entorno de verificación porque este entorno no tiene `PySide6` instalado.

Esto **no significa que la UI esté rota**; significa que la prueba visual debe ejecutarse en el Mac de desarrollo.

## Bloque A — autorización de herramientas

Se ha aplicado la primera corrección de seguridad de esta continuación:

- La autorización de una llamada se decide a partir de la petición original del usuario, no por la mera aparición del nombre de la herramienta.
- Una pregunta informativa como `¿qué hace borrar_archivo?` no autoriza el borrado.
- Una mención como `el texto menciona leer_archivo` no autoriza la lectura.
- Una negación explícita como `no borres archivo.txt` bloquea el borrado.
- La negación se evalúa por herramienta, de modo que `no borres viejo.txt, pero crea nuevo.txt` puede autorizar la creación sin autorizar el borrado.
- Para MCP, mencionar `mcp__...` no basta: se exige una acción explícita (`usa`, `utiliza`, `llama`, etc.) o un alias semántico de una operación de workspace.
- La barrera sigue sin ser el mecanismo de confirmación destructiva: una llamada autorizada por intención todavía debe pasar por la confirmación de la interfaz cuando corresponda.

Pruebas específicas añadidas para estas reglas.

## Lo que queda pendiente antes de Fase 5

1. Probar en el Mac con PySide6 instalado.
2. Ejecutar la suite con MCP y Ollama reales:

```bash
PEREZOSO_REAL_OLLAMA=1 pytest -q
```

3. Probar manualmente:
   - conversación normal;
   - respuesta con varios párrafos;
   - `listar_carpeta`;
   - `leer_archivo`;
   - `crear_archivo`;
   - `escribir_archivo`;
   - borrado con confirmación;
   - cancelación durante generación;
   - cancelación durante confirmación;
   - activación MCP;
   - herramienta MCP de lectura;
   - herramienta MCP potencialmente destructiva.

4. Comprobar visualmente que las preguntas conservan:
   - alineación derecha;
   - 50 % del canvas;
   - borde fino;
   - esquinas redondeadas;
   - fondo transparente;
   - 20 px de margen interior.

5. Si todo funciona en el Mac, cerrar Fase 4.1 y pasar a Fase 5.

## Fase 5 prevista

Solo después de cerrar Fase 4.1:

- Git
- Memoria
- Automatización
- Diagnósticos
- Benchmarks
- otros plugins independientes

No introducir nuevas funcionalidades antes de validar la estabilidad de la versión actual.

## Regla de trabajo para el siguiente chat

No rehacer el análisis completo desde cero.

Partir de:

> `ChatPerezoso_v2_fase4_1_estabilizado.zip`

y de este documento.

Primero validar en el Mac los puntos pendientes de la sección **Lo que queda pendiente antes de Fase 5**. Si aparece un fallo, corregirlo y volver a ejecutar tests antes de añadir cualquier funcionalidad nueva.
