# ChatPerezoso v2

Aplicación de escritorio local, pequeña y robusta para conversar con Ollama y usar herramientas sobre una carpeta de trabajo seleccionada.

## Principios

- El núcleo hace solo: UI, Ollama, conversación, streaming, herramientas básicas y workspace.
- Las funciones adicionales viven en `plugins/` y pueden añadirse o retirarse sin modificar el núcleo.
- No hay agentes, memoria, benchmarks, diagnóstico avanzado, compresión de contexto ni sistemas adaptativos en el núcleo.
- Las operaciones de archivos están confinadas al workspace elegido.

## Arranque

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Ollama debe estar ejecutándose en `http://localhost:11434` por defecto.

## Herramientas iniciales

- `listar_carpeta`
- `leer_archivo`
- `crear_archivo`
- `escribir_archivo`
- `borrar_archivo`

Las operaciones de escritura y borrado requieren confirmación explícita del usuario.

## Estructura

```text
ChatPerezoso_v2/
├── core/
│   ├── config.py         # carga/guardado de config.json
│   ├── workspace.py      # sandbox de archivos (raíz confinada)
│   ├── tools.py          # ToolRegistry: contrato de herramientas del núcleo
│   ├── tool_provider.py  # Protocol común a ToolRegistry y MCPToolBridge
│   ├── intent.py         # heurística de intención (verbos en español)
│   └── ollama.py         # cliente de transporte (streaming + tool calling)
├── ui/
│   ├── main_window.py    # orquestación: construye la ventana y conecta señales
│   ├── chat_view.py      # ChatTranscript: todo el renderizado del historial
│   ├── workers.py        # QObject workers (modelo, MCP, chat) para threads
│   └── theme.py
├── plugins/    # extensiones opcionales (MCP incluido como ejemplo)
├── tests/      # pruebas del núcleo
└── workspace/  # workspace local por defecto
```

`config.json` es local de cada máquina (contiene rutas absolutas). No se
versiona: usa `config.example.json` como plantilla y `.gitignore` ya lo excluye.

### Diseño del chat

- Preguntas del usuario: burbuja, justificada a la derecha, 60% del ancho visible.
- Respuestas del asistente: sin burbuja, justificadas a la izquierda, 60% del ancho visible.
- Un mismo espacio separa cada turno (pregunta/respuesta/evento de herramienta);
  un espacio menor y también único separa los párrafos dentro de un mismo mensaje.
- Resultados de herramienta: cuerpo de texto pequeño, monoespaciado, con margen a la izquierda.

Estas reglas están centralizadas como constantes en `ui/chat_view.py`
(`ChatTranscript.BUBBLE_WIDTH_RATIO`, `GAP_MESSAGE`, `GAP_PARAGRAPH`) para
que cualquier ajuste futuro se haga en un solo sitio.


## Pruebas con Ollama real

La suite de integración `tests/test_ollama_real.py` está desactivada por defecto porque necesita una instancia real de Ollama.

Para ejecutarla en el Mac:

```bash
PEREZOSO_REAL_OLLAMA=1 python3 -m pytest -q
```

Opcionalmente se puede indicar el servidor y el modelo:

```bash
PEREZOSO_REAL_OLLAMA=1 \
PEREZOSO_OLLAMA_HOST=http://localhost:11434 \
PEREZOSO_OLLAMA_MODEL=nombre-del-modelo \
python3 -m pytest -q
```

La prueba de herramientas utiliza un workspace temporal y no modifica el workspace real del usuario.


## MCP opcional

ChatPerezoso puede conectar opcionalmente un servidor MCP por `stdio`. El plugin está aislado en `plugins/mcp/` y no es necesario para ejecutar el núcleo.

En la interfaz, `Añadir servidor MCP` solicita un identificador corto y el
comando del servidor, consulta sus herramientas y las incorpora al flujo de
herramientas. Se pueden activar varios servidores simultáneamente. Para evitar
colisiones entre servidores, las herramientas MCP se exponen al modelo como
`mcp__{id_servidor}__{herramienta}`.

El comando propuesto por defecto inicia `@modelcontextprotocol/server-filesystem`
con el workspace actual como raíz. También se puede introducir manualmente el
servidor de prueba `plugins/mcp/demo_server.py`.

La dependencia se instala por separado:

```bash
pip install -r plugins/mcp/requirements.txt
```


## Estado de estabilización

La rama actual corresponde al cierre técnico de Fase 4. Las herramientas solo se exponen ante una petición explícita sobre el workspace; las llamadas no solicitadas quedan bloqueadas y las llamadas textuales no se ejecutan. Se han corregido también la cancelación, la carga de modelos sin bloquear la UI, la activación MCP fuera del hilo principal y la confirmación de todas las operaciones que escriben en el workspace.

La suite automática local pasa **68 pruebas** y omite las pruebas que requieren PySide6, MCP u Ollama real cuando esas dependencias/servicios no están disponibles. Las pruebas de interfaz se marcan como omitidas en lugar de provocar un error de recolección en entornos sin PySide6.

### Validación final de Fase 4

En el entorno actual:

```text
68 passed, 6 skipped
python3 -m compileall -q .  # OK
```

En el Mac de desarrollo conviene ejecutar además:

```bash
pip install -r requirements.txt
pytest -q
PEREZOSO_REAL_OLLAMA=1 pytest -q
```

Y comprobar manualmente la activación/desactivación de varios servidores MCP y la confirmación de operaciones destructivas.


## Fase 3 — Resolución de duplicidad MCP/workspace

Cuando está activo un servidor MCP que expone `read_file` y/o `list_directory` (como `server-filesystem`), ChatPerezoso oculta las herramientas equivalentes del núcleo (`leer_archivo` y/o `listar_carpeta`) para que el modelo tenga una única herramienta para cada operación. Al desactivar el servidor, las herramientas del núcleo se restauran.

La barrera de intención reconoce esos nombres MCP como equivalentes semánticos de las operaciones de lectura/listado del workspace. Las demás herramientas MCP no alteran las herramientas del núcleo.
