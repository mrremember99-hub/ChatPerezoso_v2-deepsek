╔══════════════════════════════════════════════════════════════════╗
║  PROYECTO: OVERPAPER (gui.py + core_processor.py)                ║
║  9 fases secuenciales. Cada una VERIFICADA antes de la siguiente. ║
╚══════════════════════════════════════════════════════════════════╝

REGLAS GLOBALES — aplican a TODAS las fases:

1. ACTÚA, NO ANUNCIES.
   Cuando vayas a leer un archivo o escribirlo, emite la tool call
   DIRECTAMENTE. No escribas texto tipo "voy a leer gui.py" sin la
   tool call en el mismo turno. Si escribes que vas a hacer algo, la
   tool call debe estar en ese mismo mensaje. Un "voy a" sin tool
   call = fase fallida.

2. LECTURA OBLIGATORIA ANTES DE ESCRIBIR.
   Antes de escribir_archivo sobre un archivo existente, DEBES haber
   usado leer_archivo sobre ese mismo archivo. Si el archivo no
   existe todavía, usa crear_archivo sin leer.

3. UNA SOLA VERIFICACIÓN. NO REINTENTES EN BUCLE.
   Al final de la fase ejecuta el comando de verificación. Una sola
   vez. Interpreta el resultado:
     · Output "(sin salida)" o vacío → FASE VERIFICADA.
     · Output con "Traceback" o "Error" → corrige UNA vez y vuelve
       a verificar. Si vuelve a fallar, FASE CON ERROR.
   PROHIBIDO reintentar el comando más de dos veces. Si lo
   ejecutaste y salió "(sin salida)", PARA. No vuelvas a ejecutarlo.

4. FORMATO DE CIERRE.
   Tu respuesta final de cada fase es EXACTAMENTE:
     "FASE VERIFICADA · salida: <cita literal>"
   o
     "FASE CON ERROR · <cita literal del error>"
   Nada más. Sin resumen, sin explicación.

5. RUTAS RELATIVAS.
   Los archivos viven en la raíz del workspace: "gui.py" y
   "core_processor.py".

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 FASE 1 — Crear gui.py (estructura visual, sin lógica)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Crea gui.py con Tkinter. Aplica las reglas globales.

Estructura:
- Ventana raíz, título "OVERPAPER", fondo "#1e1e1e".
- Dos columnas: izquierda fija (~300px) con controles, derecha
  expandible con el "Viewport" (un tk.Canvas sobre fondo #3c3c3c).

Fuentes — usa este patrón EXACTO (no uses try/except):
    from tkinter import font as tkfont
    available = set(tkfont.families())
    family = "Montserrat" if "Montserrat" in available else "Arial"
    self.font = (family, 10)
    self.font_bold = (family, 10, "bold")

Controles en la columna izquierda:
- Label "OVERPAPER" arriba (font_bold).
- Sección MODE: dos Radiobutton con variable self.mode_var
  (tk.StringVar, valor inicial "SINGLE IMAGE"), valores
  "SINGLE IMAGE" y "DUAL IMAGE".
- Sección "INPUT SOURCES": dos botones "LOAD IMAGE A" y
  "LOAD IMAGE B" (guardados en self.load_a_btn / self.load_b_btn).
  Debajo de cada botón, un tk.Canvas de 120x120 (guardados en
  self.preview_a / self.preview_b) con bg="#3c3c3c" y
  highlightthickness=0. Usa Canvas, NO Frame.
- Sección "SLATS (N)": Label "SLATS (N)" + un ttk.Scale con
  from_=2, to=64, orient=tk.HORIZONTAL, variable self.slats_value
  (tk.IntVar, valor inicial 8). Al lado, un Label que muestre el
  valor actual.
- Al final: dos botones, "[ PROCESS HD ]" (self.process_btn) y
  "[ SAVE OUTPUT ]" (self.save_btn).

En la columna derecha: dentro del Canvas principal, un texto
centrado "Viewport" (puede ser un Label arriba del Canvas, o el
propio Canvas con un create_text).

Todos los command= de botones y slider son `pass` o
`lambda: print("...")`. La lógica llega en fases siguientes.

Al final del archivo:
    if __name__ == "__main__":
        root = tk.Tk()
        app = OverpaperGUI(root)
        root.mainloop()

━━━ VERIFICACIÓN FASE 1 ━━━
Ejecuta UNA sola vez: python -m py_compile gui.py
Interpreta el resultado según la regla global 3.
Cierra según la regla global 4.

---

MISMO PROTOCOLO EN TODAS LAS FASES:
· Actúa, no anuncies. Tool calls sin preámbulo.
· Lee antes de escribir.
· UNA SOLA verificación. No bucle.
· Cierra con "FASE VERIFICADA · salida: <cita>" o
  "FASE CON ERROR · <cita>".

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 FASE 2 — Modo SINGLE/DUAL en gui.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee gui.py. Añade lógica de cambio de modo. EDICIÓN.

1. Los RadioButtons ya están vinculados a self.mode_var. Añade
   una traza con `self.mode_var.trace_add("write", self.on_mode_change)`
   después de crear los RadioButtons.

2. Define on_mode_change(self, *args):
   - mode = self.mode_var.get()
   - Si mode == "SINGLE IMAGE":
       self.load_b_btn.config(state=tk.DISABLED)
       # El preview_b es un Canvas. Los Canvas SÍ tienen delete y
       # create_text. Los Frame NO. Aquí usamos Canvas:
       self.preview_b.delete("all")
       self.preview_b.create_text(
           60, 60, text="BLOQUEADO", fill="gray",
           font=self.font
       )
   - Si mode == "DUAL IMAGE":
       self.load_b_btn.config(state=tk.NORMAL)
       self.preview_b.delete("all")
       self.preview_b.create_text(
           60, 60, text="SIN IMAGEN", fill="gray",
           font=self.font
       )
   - Limpia también el canvas principal:
       self.canvas.delete("all")

3. Estado inicial: al final de __init__, llama a
   self.on_mode_change() para que arranque con SINGLE aplicado.

Recuerda: preview_a, preview_b y canvas son tk.Canvas. Los Canvas
tienen .delete("all") y .create_text(...). Los Frame NO.

━━━ VERIFICACIÓN FASE 2 ━━━
Ejecuta UNA sola vez: python -m py_compile gui.py
Cierra según la regla global 4.

---

MISMO PROTOCOLO EN TODAS LAS FASES:
· Actúa, no anuncies. Tool calls sin preámbulo.
· Lee antes de escribir.
· UNA SOLA verificación. No bucle.
· Cierra con "FASE VERIFICADA · salida: <cita>" o
  "FASE CON ERROR · <cita>".

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 FASE 3 — Carga de imágenes A y B
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee gui.py. Añade carga de imágenes. EDICIÓN.

1. Imports al principio:
   from PIL import Image, ImageTk, UnidentifiedImageError
   from tkinter import filedialog

2. Atributos en __init__:
   self.img_a_hd = None
   self.img_a_thumb = None
   self.img_b_hd = None
   self.img_b_thumb = None

3. Conecta los botones:
   self.load_a_btn.config(command=self.load_image_a)
   self.load_b_btn.config(command=self.load_image_b)

4. Métodos:

   def load_image_a(self):
       path = filedialog.askopenfilename(
           filetypes=[
               ("Imágenes", "*.png *.jpg *.jpeg *.tiff *.tif"),
               ("Todos", "*.*"),
           ]
       )
       if not path:
           return
       try:
           img = Image.open(path)
           self.img_a_hd = img
           thumb = img.copy()
           thumb.thumbnail((120, 120), Image.LANCZOS)
           self.img_a_thumb = ImageTk.PhotoImage(thumb)
           self.preview_a.delete("all")
           self.preview_a.create_image(
               60, 60, image=self.img_a_thumb, anchor=tk.CENTER
           )
           self.status_label.config(text="Imagen A cargada")
       except (OSError, UnidentifiedImageError):
           self.status_label.config(text="No se pudo abrir la imagen")

   def load_image_b(self): mismo patrón → self.img_b_hd,
   self.img_b_thumb, self.preview_b, self.status_label.

5. Añade al pie de la columna izquierda un Label:
   self.status_label = tk.Label(
       self.left_frame, text="Listo", fg="gray", bg="#1e1e1e",
       font=self.font
   )
   self.status_label.pack(side=tk.BOTTOM, pady=10)

━━━ VERIFICACIÓN FASE 3 ━━━
Ejecuta UNA sola vez: python -m py_compile gui.py
Cierra según la regla global 4.

---

MISMO PROTOCOLO EN TODAS LAS FASES:
· Actúa, no anuncies.
· Lee antes de escribir.
· UNA SOLA verificación. No bucle.
· Cierra con "FASE VERIFICADA · salida: <cita>" o
  "FASE CON ERROR · <cita>".

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 FASE 4 — Crear core_processor.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Crea core_processor.py. Sin Tkinter. Solo Pillow.

Función principal:

    def process_interlace(img_a, img_b, slats_count, mode="DUAL"):
        """Devuelve una PIL.Image combinada."""

1. Homogeneización:
   - Si mode == "DUAL" y img_b tiene distinto tamaño que img_a:
     redimensiona img_b manteniendo aspecto para cubrir img_a y
     recorta desde el centro. Resultado: img_b del mismo tamaño que
     img_a.
   - Si mode == "SINGLE": img_b se ignora. Clona img_a y aplica
     ImageOps.mirror para obtener la versión espejada.

2. Algoritmo de franjas verticales:
   - ancho = img_a.width
   - base = ancho // slats_count
   - residuo = ancho % slats_count
   - Anchuras: las slats_count-1 primeras de tamaño base, la
     última de tamaño base + residuo.

3. Ensamblado:
   - Crea una nueva imagen RGB del tamaño de img_a.
   - Recorre las franjas de izquierda a derecha, con x acumulado.
   - Franja 0 (índice par): de img_a.
   - Franja 1 (índice impar): de img_b.
   - Alterna. Pega cada franja en la posición x acumulada.

4. Devuelve la imagen final.

Imports: from PIL import Image, ImageOps

━━━ VERIFICACIÓN FASE 4 ━━━
Ejecuta UNA sola vez: python -m py_compile core_processor.py
Cierra según la regla global 4.

---

MISMO PROTOCOLO EN TODAS LAS FASES:
· Actúa, no anuncies.
· Lee antes de escribir.
· UNA SOLA verificación. No bucle.
· Cierra con "FASE VERIFICADA · salida: <cita>" o
  "FASE CON ERROR · <cita>".

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 FASE 5 — generate_proxy y validate_images en core_processor.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee core_processor.py. Añade dos funciones. EDICIÓN.

1. def generate_proxy(image, max_size=800):
   - Si image.width <= max_size y image.height <= max_size:
     return image  (sin copiar)
   - Si alguno supera max_size: copia escalada manteniendo aspecto
     de forma que el lado más largo quede exactamente en max_size.
   - Usa Image.LANCZOS.
   - Devuelve la imagen nueva.

2. def validate_images(img_a, img_b, mode):
   - Devuelve (bool, str).
   - Si img_a is None → (False, "Falta imagen A")
   - Si mode == "DUAL" y img_b is None → (False, "Falta imagen B")
   - Si mode not in ("SINGLE", "DUAL") → (False, "Modo inválido")
   - En otro caso → (True, "")

3. Al final del archivo, añade:
   if __name__ == "__main__":
       from pathlib import Path
       from PIL import Image
       a = Image.new("RGB", (1200, 800), (255, 0, 0))
       b = Image.new("RGB", (1200, 800), (0, 0, 255))
       out = process_interlace(a, b, slats_count=8, mode="DUAL")
       out_path = Path(__file__).parent / "test_output.png"
       out.save(out_path)
       print(f"{out_path} guardado")
   No ejecutes el archivo.

━━━ VERIFICACIÓN FASE 5 ━━━
Ejecuta UNA sola vez: python -m py_compile core_processor.py
Cierra según la regla global 4.

---

MISMO PROTOCOLO EN TODAS LAS FASES:
· Actúa, no anuncies.
· Lee antes de escribir.
· UNA SOLA verificación. No bucle.
· Cierra con "FASE VERIFICADA · salida: <cita>" o
  "FASE CON ERROR · <cita>".

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 FASE 6 — Conectar carga con viewport en gui.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee gui.py. EDICIÓN. NO toques core_processor.py.

1. Import al principio:
   from core_processor import generate_proxy, process_interlace

2. Atributos nuevos en __init__:
   self.img_a_proxy = None
   self.img_b_proxy = None
   self._current_canvas_image = None

3. En load_image_a, después de generar la miniatura, añade:
   self.img_a_proxy = generate_proxy(self.img_a_hd)
   self.update_viewport_preview()

4. En load_image_b, igual:
   self.img_b_proxy = generate_proxy(self.img_b_hd)
   self.update_viewport_preview()

5. Añade el método update_viewport_preview(self):
   - mode = self.mode_var.get()
   - Si mode == "SINGLE" y self.img_a_proxy is None:
       self.canvas.delete("all")
       self.canvas.create_text(
           <ancho/2>, <alto/2>,
           text="Carga una imagen para previsualizar",
           fill="gray", font=self.font
       )
       return
   - Si mode == "DUAL" y (self.img_a_proxy is None o
     self.img_b_proxy is None):
       mismo mensaje indicando cuál falta.
       return
   - Si todo listo:
       try:
           result = process_interlace(
               self.img_a_proxy, self.img_b_proxy,
               int(self.slats_value.get()), mode=mode
           )
           self.render_to_canvas(result)
       except Exception:
           self.status_label.config(
               text="Error al generar el preview"
           )

6. Añade render_to_canvas(self, imagen):
   cw = self.canvas.winfo_width()
   ch = self.canvas.winfo_height()
   # Si el canvas aún no está mapeado, usa un valor razonable
   if cw < 10: cw = 800
   if ch < 10: ch = 600
   scale = min(cw / imagen.width, ch / imagen.height)
   new_w = max(1, int(imagen.width * scale))
   new_h = max(1, int(imagen.height * scale))
   resized = imagen.resize((new_w, new_h), Image.LANCZOS)
   photo = ImageTk.PhotoImage(resized)
   self.canvas.delete("all")
   self.canvas.create_image(
       cw // 2, ch // 2, image=photo, anchor=tk.CENTER
   )
   self._current_canvas_image = photo  # evitar GC

7. Al cambiar de modo (dentro de on_mode_change), tras limpiar el
   canvas, llama a self.update_viewport_preview().

━━━ VERIFICACIÓN FASE 6 ━━━
Ejecuta UNA sola vez: python -m py_compile gui.py
Cierra según la regla global 4.

---

MISMO PROTOCOLO EN TODAS LAS FASES:
· Actúa, no anuncies.
· Lee antes de escribir.
· UNA SOLA verificación. No bucle.
· Cierra con "FASE VERIFICADA · salida: <cita>" o
  "FASE CON ERROR · <cita>".

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 FASE 7 — Slider SLATS reactivo con debounce
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee gui.py. EDICIÓN.

1. En __init__, antes de crear el slider, añade:
   self._preview_job = None

2. El slider ya existe. Cámbiale el command:
   slats_slider.config(command=self.on_slats_changed)
   Y guarda la referencia en self.slats_slider.

3. Al lado del slider, un Label con textvariable=self.slats_value
   (ya existe). Refuérzalo: la variable tiene valor inicial 8.

4. Añade los métodos:

   def on_slats_changed(self, value):
       # value llega como str desde ttk.Scale
       try:
           n = int(float(value))
       except (TypeError, ValueError):
           return
       self.slats_value.set(n)
       if self._preview_job is not None:
           self.after_cancel(self._preview_job)
       self._preview_job = self.after(80, self._do_preview)

   def _do_preview(self):
       self._preview_job = None
       self.update_viewport_preview()

━━━ VERIFICACIÓN FASE 7 ━━━
Ejecuta UNA sola vez: python -m py_compile gui.py
Cierra según la regla global 4.

---

MISMO PROTOCOLO EN TODAS LAS FASES:
· Actúa, no anuncies.
· Lee antes de escribir.
· UNA SOLA verificación. No bucle.
· Cierra con "FASE VERIFICADA · salida: <cita>" o
  "FASE CON ERROR · <cita>".

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 FASE 8 — Botón PROCESS HD con threading
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee gui.py. EDICIÓN. NO toques core_processor.py.

1. Import:
   from core_processor import (
       generate_proxy, process_interlace, validate_images
   )
   import threading

2. Atributo en __init__:
   self.result_hd = None

3. Conecta el botón:
   self.process_btn.config(command=self.on_process_hd)

4. Métodos:

   def on_process_hd(self):
       ok, msg = validate_images(
           self.img_a_hd, self.img_b_hd, self.mode_var.get()
       )
       if not ok:
           self.status_label.config(text=msg)
           return
       self.process_btn.config(state=tk.DISABLED)
       self.status_label.config(text="Procesando HD...")
       threading.Thread(
           target=self._process_hd_worker, daemon=True
       ).start()

   def _process_hd_worker(self):
       try:
           result = process_interlace(
               self.img_a_hd, self.img_b_hd,
               int(self.slats_value.get()),
               mode=self.mode_var.get()
           )
           self.result_hd = result
           self.after(0, self._on_process_done)
       except (MemoryError, OSError) as exc:
           self.after(0, lambda: self._on_process_error(str(exc)))

   def _on_process_done(self):
       self.process_btn.config(state=tk.NORMAL)
       self.status_label.config(text="Completado")
       self.render_to_canvas(self.result_hd)

   def _on_process_error(self, msg):
       self.process_btn.config(state=tk.NORMAL)
       self.status_label.config(text=f"Error: {msg}")

━━━ VERIFICACIÓN FASE 8 ━━━
Ejecuta UNA sola vez: python -m py_compile gui.py
Cierra según la regla global 4.

---

MISMO PROTOCOLO EN TODAS LAS FASES:
· Actúa, no anuncies.
· Lee antes de escribir.
· UNA SOLA verificación. No bucle.
· Cierra con "FASE VERIFICADA · salida: <cita>" o
  "FASE CON ERROR · <cita>".

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 FASE 9 — SAVE OUTPUT y manejo de errores
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lee gui.py. EDICIÓN.

1. Import:
   from tkinter import messagebox

2. Conecta el botón:
   self.save_btn.config(command=self.on_save_output)

3. Añade el método:

   def on_save_output(self):
       if self.result_hd is None:
           self.status_label.config(text="Procesa HD antes de guardar")
           return
       path = filedialog.asksaveasfilename(
           defaultextension=".png",
           filetypes=[
               ("PNG", "*.png"),
               ("TIFF", "*.tiff"),
               ("Todos", "*.*"),
           ]
       )
       if not path:
           return
       try:
           if path.lower().endswith((".tiff", ".tif")):
               self.result_hd.save(path, compression="tiff_lzw")
           else:
               self.result_hd.save(path)
           import os
           self.status_label.config(
               text=f"Guardado: {os.path.basename(path)}"
           )
       except (OSError, PermissionError) as exc:
           messagebox.showerror(
               "Error al guardar", str(exc)
           )

━━━ VERIFICACIÓN FASE 9 ━━━
Ejecuta UNA sola vez: python -m py_compile gui.py
Cierra según la regla global 4.

---

╔══════════════════════════════════════════════════════════════════╗
║  Cuando las 9 fases estén aplicadas y verificadas, responde:     ║
║  "PROYECTO COMPLETADO"                                           ║
╚══════════════════════════════════════════════════════════════════╝