╔══════════════════════════════════════════════════════════════════╗
║ PROYECTO: OVERPAPER (gui.py + core_processor.py)                 ║
║ 20 fases secuenciales. Cada una VERIFICADA antes de la siguiente.║
╚══════════════════════════════════════════════════════════════════╝

REGLAS GLOBALES — aplican a TODAS las fases:

1. ACTÚA, NO ANUNCIES.
Cuando vayas a leer o escribir un archivo, emite la tool call
DIRECTAMENTE en el mismo turno. Un "voy a..." sin tool call =
fase fallida.

2. LECTURA OBLIGATORIA ANTES DE ESCRIBIR.
Antes de escribir_archivo sobre un archivo existente, DEBES
haberlo leído con leer_archivo. Si no existe, crear_archivo.

3. UNA SOLA VERIFICACIÓN. NO REINTENTES EN BUCLE.
Al final de cada fase ejecuta el comando de verificación UNA vez.
- Output vacío o "(sin salida)" → FASE VERIFICADA.
- Output con "Traceback" o "Error" → corrige UNA vez y verifica
  de nuevo. Si vuelve a fallar → FASE CON ERROR.
Si ya ejecutaste y salió "(sin salida)", PARA.

4. FORMATO DE CIERRE.
Tu respuesta final de cada fase es EXACTAMENTE:
"FASE VERIFICADA · salida: <cita literal>"
o
"FASE CON ERROR · <cita literal del error>"
Nada más. Sin resumen, sin explicación.

5. RUTAS RELATIVAS.
Los archivos viven en la raíz del workspace: "gui.py" y
"core_processor.py".

6. PYTHON 3 — LAMBDAS EN `except`.
`except ... as exc:` borra `exc` al salir del bloque. Si el valor
se usa en un callback diferido (self.after, threading), captura
`msg = str(exc)` ANTES de crear el lambda.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[ESTADO DEL WORKSPACE]
(vacío)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FASE 1 — Ventana básica
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Crea gui.py. Solo la ventana raíz: título "OVERPAPER", fondo
oscuro (#1e1e1e), sin contenido todavía.

Entry point estándar `if __name__ == "__main__":` que cree la
ventana y entre en mainloop.

Elige una forma limpia de resolver la fuente base (intenta
"Montserrat", cae a "Arial" si no está). No uses try/except
vacíos.

━━━ VERIFICACIÓN FASE 1 ━━━
python -m py_compile gui.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FASE 2 — Layout de dos columnas
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee gui.py. Añade layout de dos columnas:

- Izquierda: contenedor fijo de ~300px de ancho para controles.
- Derecha: un Canvas grande sobre fondo #3c3c3c que se expanda
  al redimensionar la ventana. Dentro, texto "Viewport"
  centrado.

La columna izquierda debe mantener su ancho al redimensionar; la
derecha se adapta.

━━━ VERIFICACIÓN FASE 2 ━━━
python -m py_compile gui.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FASE 3 — Sección MODE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee gui.py. En la columna izquierda, arriba del todo:

- Título "OVERPAPER".
- Sección "MODE" con dos Radiobutton: SINGLE y DUAL, ambos
  apuntando a una misma variable compartida (StringVar). Valor
  inicial: SINGLE.

De momento no reaccionan a nada (sin lógica).

━━━ VERIFICACIÓN FASE 3 ━━━
python -m py_compile gui.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FASE 4 — Sección INPUT SOURCES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee gui.py. Añade la sección "INPUT SOURCES":

- Dos botones: "LOAD IMAGE A" y "LOAD IMAGE B".
- Debajo de cada uno, un Canvas de 120x120 sobre fondo #3c3c3c
  sin borde, que servirá como miniatura.

Los botones no hacen nada todavía. Usa Canvas (no Frame) para
las miniaturas.

━━━ VERIFICACIÓN FASE 4 ━━━
python -m py_compile gui.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FASE 5 — Sección SLATS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee gui.py. Añade la sección "SLATS (N)":

- Label de título.
- Un slider horizontal (ttk.Scale) entre 2 y 64, valor inicial 8.
- A su lado, un Label que muestre en todo momento el valor
  actual del slider, actualizándose mientras el usuario mueve.

No dispares todavía ningún cálculo con el slider.

━━━ VERIFICACIÓN FASE 5 ━━━
python -m py_compile gui.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FASE 6 — Botones de acción y status
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee gui.py. Cierra la columna izquierda con:

- Botón "[ PROCESS HD ]".
- Botón "[ SAVE OUTPUT ]".
- Un Label de estado al fondo, fijado con `side=tk.BOTTOM`,
  texto inicial "Listo".

Los botones no hacen nada todavía.

━━━ VERIFICACIÓN FASE 6 ━━━
python -m py_compile gui.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FASE 7 — Cambio de modo SINGLE / DUAL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee gui.py. Conecta el selector de modo:

En SINGLE:
- El botón de cargar B se deshabilita.
- Su preview muestra "BLOQUEADO" en gris.

En DUAL:
- El botón B se habilita.
- Su preview muestra "SIN IMAGEN" en gris.

En ambos casos, el canvas principal se limpia. Debe aplicarse
al cambiar la selección y también una vez al arrancar.

━━━ VERIFICACIÓN FASE 7 ━━━
python -m py_compile gui.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FASE 8 — Carga de imagen A
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee gui.py. Haz que el botón "LOAD IMAGE A" abra un diálogo de
selección de archivo.

Al elegir un archivo:
- Guárdala como imagen HD.
- Genera una miniatura de 120x120 y muéstrala en el preview A.
- Actualiza el status: "Imagen A cargada".

Si la apertura falla (archivo ilegible, no es imagen), el status
muestra "No se pudo abrir la imagen" y la app sigue viva.

Formatos: PNG, JPG/JPEG, TIFF/TIF y cualquier otro que Pillow
soporte.

━━━ VERIFICACIÓN FASE 8 ━━━
python -m py_compile gui.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FASE 9 — Carga de imagen B
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee gui.py. Añade el mismo comportamiento al botón "LOAD IMAGE
B": diálogo, carga, miniatura en el preview B, status "Imagen B
cargada", manejo de errores idéntico.

━━━ VERIFICACIÓN FASE 9 ━━━
python -m py_compile gui.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FASE 10 — Núcleo de procesamiento
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Crea core_processor.py. Sin Tkinter, solo Pillow.

Función `process_interlace(img_a, img_b, slats_count,
mode="DUAL") -> Image`: combina dos imágenes por franjas
verticales alternas — franja 0 de A, franja 1 de B, etc.

Reglas:
- En DUAL, si B no tiene el mismo tamaño que A, adáptala
  cubriendo el área y recortando desde el centro (sin deformar).
- En SINGLE, ignora B y usa una versión espejada de A.
- Si `slats_count` no divide exacto el ancho, la última franja
  absorbe el residuo.

━━━ VERIFICACIÓN FASE 10 ━━━
python -m py_compile core_processor.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FASE 11 — Utilidades del procesador
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee core_processor.py. Añade dos utilidades:

- `generate_proxy(image, max_size=800)`: si la imagen ya cabe,
  devuélvela sin copiar. Si no, escálala manteniendo aspecto
  para que el lado más largo quepa en `max_size`. Sirve para
  previsualizar rápido sin cargar la HD.

- `validate_images(img_a, img_b, mode) -> (bool, str)`: valida
  que hay imágenes para el modo activo. Devuelve `(True, "")` o
  `(False, "mensaje")` con la causa.

Al final del archivo, un bloque `__main__` que genere dos
imágenes de prueba 1200x800 (roja y azul), las combine con 8
franjas en DUAL y guarde `test_output.png`. NO lo ejecutes.

━━━ VERIFICACIÓN FASE 11 ━━━
python -m py_compile core_processor.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FASE 12 — Conectar carga con preview
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee gui.py. NO toques core_processor.py.

Al cargar una imagen A o B, además de la miniatura, genera un
proxy con la utilidad correspondiente y dispara la actualización
del viewport principal.

Añade un método `update_viewport_preview` que:
- Consulte el modo actual.
- Si falta alguna imagen para el modo activo, muestre un mensaje
  claro ("Carga una imagen para previsualizar" o "Falta imagen
  A/B") y pare.
- Si todo está listo, combine los proxies con `process_interlace`
  usando el slats actual y pase el resultado al canvas.

De momento, sin dibujar todavía en el canvas (eso es la fase
siguiente). Solo la lógica de decidir qué hacer.

━━━ VERIFICACIÓN FASE 12 ━━━
python -m py_compile gui.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FASE 13 — render_to_canvas
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee gui.py. Añade el método `render_to_canvas(imagen)`:

- Lee el tamaño actual del canvas. Si es muy pequeño (aún no
  mapeado), usa un fallback razonable (por ejemplo 800x600).
- Escala la imagen manteniendo aspecto para que quepa entera.
- La dibuja centrada en el canvas.
- **Retén una referencia a la PhotoImage** en un atributo para
  evitar que el recolector de basura la libere (bug clásico de
  Tkinter donde la imagen desaparece tras la primera
  actualización).

Conecta `update_viewport_preview` con `render_to_canvas` para
que todo el flujo funcione end-to-end: cargar imagen → ver
preview combinado en el canvas. Al cambiar de modo también debe
refrescarse.

━━━ VERIFICACIÓN FASE 13 ━━━
python -m py_compile gui.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FASE 14 — Slider reactivo con debounce
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee gui.py. Haz que mover el slider actualice el preview en vivo.

Importante: no recalcular en cada tick del slider (serían decenas
por segundo). Aplica un **debounce** corto (~80 ms): cada vez que
el slider emite un valor, cancela el refresco pendiente y
reprograma uno nuevo. Cuando el usuario deja de mover, se
recalcula una sola vez.

━━━ VERIFICACIÓN FASE 14 ━━━
python -m py_compile gui.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FASE 15 — PROCESS HD en hilo aparte
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee gui.py. Implementa el botón PROCESS HD. NO toques
core_processor.py.

Al pulsar:
- Valida con `validate_images`. Si falla, muestra el mensaje y
  para.
- Deshabilita el botón, muestra "Procesando HD..." en el status.
- Lanza el procesamiento de las imágenes HD (sin proxies) en un
  **hilo separado** para que la UI no se congele.

Cuando termina:
- Rehabilita el botón.
- Guarda el resultado HD para poder guardarlo luego.
- Redibuja el canvas con la imagen completa.

Si falla (memoria, disco, etc.): rehabilita el botón, muestra el
error en el status sin crashear. Respeta la regla global 6 sobre
lambdas en `except`.

━━━ VERIFICACIÓN FASE 15 ━━━
python -m py_compile gui.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FASE 16 — SAVE OUTPUT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee gui.py. Implementa el botón SAVE OUTPUT.

Al pulsar:
- Si no hay resultado HD todavía, avisa "Procesa HD antes de
  guardar" y para.
- Abre diálogo de guardado. Formato por defecto PNG; ofrecer
  también TIFF con compresión sin pérdida.
- Si el usuario cancela, no hacer nada.
- Guarda el resultado. Si falla (permisos, disco), muestra un
  messagebox con el error sin crashear.
- Al éxito, status con el nombre del archivo guardado.

━━━ VERIFICACIÓN FASE 16 ━━━
python -m py_compile gui.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FASE 17 — Transformaciones en core_processor
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee core_processor.py. Amplía `process_interlace` con dos
parámetros opcionales nuevos: `transform` (str, por defecto
"NONE") y `param` (int o None).

Transformaciones a soportar:
- "NONE": sin cambios.
- "RANDOM": franjas mezcladas aleatoriamente.
- "REVERSE": orden invertido.
- "PAIRS_SWAP": cada par consecutivo se intercambia.
- "CYCLE_LEFT" / "CYCLE_RIGHT": rotación de `param` posiciones.
- "MIRROR_HORIZONTAL": reflejo horizontal dentro de cada franja.
- "MIRROR_VERTICAL": reflejo vertical de la imagen final.
- "ROTATE_180": rotación 180° de la imagen final.

Sugerencia estructural: separa el cálculo en tres pasos —
recortar franjas, aplicar transformación a la lista de franjas,
ensamblar. Así las transformaciones son intercambiables sin
duplicar lógica.

No cambies el comportamiento por defecto.

━━━ VERIFICACIÓN FASE 17 ━━━
python -m py_compile core_processor.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FASE 18 — Control TRANSFORM en la UI
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee gui.py. Añade una sección "TRANSFORM" entre SLATS y los
botones de acción. NO toques core_processor.py.

Contenido:
- Desplegable con las 9 opciones de transformación.
- Campo numérico pequeño (el N de las rotaciones), oculto por
  defecto.

Visibilidad del campo N: solo se muestra cuando la transformación
es CYCLE_LEFT o CYCLE_RIGHT. En las demás, se oculta.

Cuidado: `ttk.Combobox` no acepta `command=` como los botones.
Usa `bind("<<ComboboxSelected>>", ...)` para reaccionar al
cambio.

━━━ VERIFICACIÓN FASE 18 ━━━
python -m py_compile gui.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FASE 19 — Integrar transformaciones en todo el flujo
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee gui.py. NO toques core_processor.py.

La transformación elegida debe aplicarse tanto al preview como
al PROCESS HD. Es decir:

- Al cambiar la transformación o el N, el preview se redibuja
  aplicando el efecto.
- Al pulsar PROCESS HD, el resultado final también sale con el
  efecto aplicado.

Refresca el preview con el mismo **debounce** que ya usas para el
slider (no recalcular por cada pulsación del campo N).

━━━ VERIFICACIÓN FASE 19 ━━━
python -m py_compile gui.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FASE 20 — Botón UPDATE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee gui.py. Añade un botón "[ UPDATE ]" debajo de SAVE OUTPUT.

Al pulsarlo:
- Refresca el viewport con los parámetros actuales (modo, slats,
  transform, N), sin esperar al debounce.
- Actualiza el status a "Actualizando..." mientras recalcula y
  vuelve a "Listo" al terminar.

Sirve para forzar el refresco cuando el usuario quiere ver el
efecto de sus cambios inmediatamente.

━━━ VERIFICACIÓN FASE 20 ━━━
python -m py_compile gui.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

╔══════════════════════════════════════════════════════════════════╗
║ Cuando las 20 fases estén aplicadas y verificadas, responde:     ║
║ "PROYECTO COMPLETADO"                                            ║
╚══════════════════════════════════════════════════════════════════╝
