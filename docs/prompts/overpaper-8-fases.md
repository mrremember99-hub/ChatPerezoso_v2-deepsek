# PROYECTO: OVERPAPER_APP

Aplicación de escritorio en Python para generar efectos de interlacing
(franjas verticales alternas) entre dos imágenes.

## REGLAS GLOBALES (aplican a todas las fases)

- Todos los archivos van al workspace, en la raíz.
- Usa `crear_archivo` para escribir cada pieza. No anuncies lo que vas a
  hacer, hazlo directamente.
- Si un archivo ya existe, usa `escribir_archivo` en lugar de
  `crear_archivo`.
- Después de escribir cada archivo, confirma al usuario en una línea qué
  archivo creaste. Nada más.
- No verifiques reescribiendo el mismo contenido. Una sola verificación
  por fase es suficiente.
- Este proyecto usa Tkinter + CustomTkinter (no PySide6) y Pillow.
  Respeta esas dependencias.

## PROTOCOLO POR FASE

Cada fase construye una pieza independiente. Respeta estrictamente el
nombre del archivo y los requisitos de cada fase.

---

# FASE 1 — config.py (Estilos y Constantes)

Actúa como un desarrollador experto en Python.
Crea un archivo llamado `config.py` que contenga todas las configuraciones globales, paleta de colores y constantes de la aplicación OVERPAPER_APP.

Requisitos:
1. Tema: Modo oscuro estricto. Define colores hex para fondo principal (#121212), paneles (#1E1E1E), bordes (#2A2A2A), texto principal (#E0E0E0) y acentos (#333333).
2. Tipografía: Configura la familia 'Montserrat' (con fallback a 'Arial') y define tamaños estandarizados (TITULOS=12, LABELS=10, BOTONES=9).
3. Parámetros por defecto: SLATS_MIN = 2, SLATS_MAX = 64, SLATS_DEFAULT = 16, PROXY_MAX_SIZE = 800.
4. Entrega únicamente las constantes de Python organizadas en diccionarios o clases limpias. Sin lógica ni librerías pesadas.

---

# FASE 2 — gui_layout.py (Estructura de la GUI)

Actúa como un experto en Tkinter / CustomTkinter.
Crea el archivo `gui_layout.py` con la estructura visual de OVERPAPER_APP importando las constantes de `config.py`.

Requisitos estrictos de layout:
1. Crear una clase `AppLayout` con dos columnas principales:
   - Columna izquierda (ancho fijo, ~300px): Contenedor de controles.
   - Columna derecha (expandible): Viewport para el lienzo con fondo gris borrador.
2. Elementos de la columna izquierda:
   - Selector de modo: RadioButtons para ["SINGLE IMAGE", "DUAL IMAGE"].
   - Sección 'INPUT SOURCES': Espacio para 2 botones (`[ LOAD IMAGE A ]`, `[ LOAD IMAGE B ]`) y 2 marcos cuadrados para miniaturas.
   - Slider 'SLATS (N)' con rango de 2 a 64 y etiqueta que muestre el valor actual.
   - Dos botones de acción al final: `[ PROCESS HD ]` y `[ SAVE OUTPUT ]`.
   - Barra de estado (Label) en el pie de la columna izquierda.
3. Genera solo la maquetación visual sin métodos de eventos aún.

---

# FASE 3 — gui_events.py (Manejadores de Eventos)

Actúa como un desarrollador de software Python.
Crea el archivo `gui_events.py` para manejar el estado de la GUI de OVERPAPER_APP utilizando Pillow (PIL).

Requisitos:
1. Define la clase o funciones para los botones de carga A y B:
   - Abre `filedialog.askopenfilename` filtrando (.png, .jpg, .jpeg, .tiff).
   - Genera una miniatura (thumbnail max 120x120px) y dibújala en su cuadro de previsualización.
2. Control de estado por modo:
   - Al seleccionar "SINGLE IMAGE", deshabilita (`state='disabled'`) el botón B y oculta/limpia su miniatura.
   - Al seleccionar "DUAL IMAGE", rehabilita el botón B.
3. Conecta estos eventos con la maquetación de `gui_layout.py` de forma desacoplada.

---

# FASE 4 — core_processor.py (Algoritmo Matemático)

Escribe un módulo Python independiente llamado `core_processor.py` usando únicamente Pillow (PIL).
Crea la función principal: `process_interlace(img_a, img_b, slats_count, mode="DUAL") -> PIL.Image`

Lógica del algoritmo:
1. Homogeneización:
   - Modo "DUAL": Si `img_b` difiere en tamaño de `img_a`, redimensiona y recorta `img_b` desde el centro para igualar a `img_a`.
   - Modo "SINGLE": Ignora `img_b`; clona `img_a` y aplícale un espejo horizontal (`ImageOps.mirror`).
2. Algoritmo de franjas y residuos:
   - Ancho base = Ancho total // `slats_count`.
   - Residuo = Ancho total % `slats_count`.
   - Reparte el residuo entre las primeras franjas (+1px a cada una) para evitar rendijas vacías y mantener uniformidad visual.
3. Ensamblado:
   - Recorta (`crop`) franjas verticales alternando `img_a` (impares) e `img_b` (pares).
   - Pega (`paste`) cada franja en una nueva imagen en blanco y retorna el objeto `PIL.Image`.

---

# FASE 5 — proxy_engine.py (Motor de Previsualización)

Crea el archivo `proxy_engine.py` para optimizar el renderizado en tiempo real.

Requisitos:
1. Función `generate_proxy(image, max_size=800)`: Recibe una `PIL.Image` y, si supera `max_size`, retorna una copia reescalada manteniendo la relación de aspecto.
2. Integración:
   - Al cargar Imagen A o B, almacena en memoria la versión HD (`img_hd`) y la versión reducida (`img_proxy`).
3. Función `update_viewport_preview()`:
   - Ejecuta `process_interlace()` pasando únicamente los proxies.
   - Escala y redibuja la imagen devuelta en el Canvas de la columna derecha para adaptarla al tamaño de la ventana sin deformarla.

---

# FASE 6 — threading_worker.py (Procesamiento Asíncrono)

Crea el archivo `threading_worker.py` para gestionar ejecuciones pesadas en hilos secundarios.

Requisitos:
1. Conecta el slider 'SLATS (N)' con `update_viewport_preview()` para que se actualice al arrastrar el control.
2. Botón `[ PROCESS HD ]`:
   - Lanza un hilo secundario con la librería estándar `threading`.
   - Ejecuta `process_interlace()` utilizando las imágenes HD en resolución nativa.
   - Actualiza el mensaje en la barra de estado ("Procesando en HD...").
   - Al finalizar, reemplaza el lienzo con la imagen HD procesada y cambia el estado a "Completado".

---

# FASE 7 — file_manager.py (Exportación y Errores)

Crea el archivo `file_manager.py` para la exportación de archivos y captura de excepciones.

Requisitos:
1. Función del botón `[ SAVE OUTPUT ]`:
   - Despliega `filedialog.asksaveasfilename` con filtros `.png` y `.tiff`.
   - Si no hay un render HD procesado en memoria, muestra un mensaje de advertencia en la barra de estado.
2. Control de excepciones:
   - Envuelve lecturas y procesamientos en bloques `try / except`.
   - Captura errores de archivo corrupto y desbordamiento de memoria (`MemoryError`), mostrando el mensaje formateado en la barra de estado inferior.

---

# FASE 8 — main.py (Orquestador Final)

Crea el archivo principal `main.py` para ensamblar la aplicación OVERPAPER_APP.

Requisitos:
1. Importa todos los módulos anteriores (`config`, `gui_layout`, `gui_events`, `core_processor`, `proxy_engine`, `threading_worker`, `file_manager`).
2. Inicializa la ventana principal de Tkinter y aplica el bucle de eventos (`mainloop()`).
3. Asegura que todas las referencias entre la interfaz, eventos y motor matemático queden correctamente conectadas.

---

## CIERRE

Al terminar la última fase, responde con:
- Lista de archivos creados.
- Comando para ejecutar la app: `python main.py`.
- Dependencias a instalar: `pip install customtkinter pillow`.