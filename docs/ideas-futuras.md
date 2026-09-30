# Ideas futuras — backlog sin priorizar

Anotaciones para no perder el hilo. No son compromisos, son
candidatos. Cuando alguna se priorice, se mueve al plan de
slices o a la deuda tecnica de `docs/harness-v3.md`.

## UI / UX

- **Fusionar botones "Enviar" y "Enviar todo"**: hay dos botones
  con proposito solapado. Antes de fusionar, aclarar que casos
  cubre cada uno (¿"Enviar todo" implica autopilot?). Posible
  outcome: el boton normal hereda autopilot cuando esta activado.
  Coste: ~30 min.

- **Zona "Sesión" con estadisticas**: hoy muestra datos del modelo
  (model, num_ctx, temperature...). Extender a estadisticas de
  sesion (tokens por turno, coste acumulado, latencia media,
  tool calls). MEJOR: esperar a S2 (durable) — el event log ya
  tendra todos los datos y la vista seria un fold sobre el log.
  Hacerlo antes = trabajo duplicado.

- **Menu contextual de archivos: anadir "borrar"**: hoy tiene
  abrir y copiar. Añadir borrar. IMPORTANTE: `borrar_archivo`
  esta en `_CONFIRMATION_REQUIRED`; el borrado desde UI debe
  pasar por el mismo gate de confirmacion. Coste: ~20 min.

- **Timestamps por fase**: cuando el modelo cierra una fase
  ("FASE N VERIFICADA"), mostrar al lado el tiempo transcurrido
  desde el inicio (o desde la fase anterior). Util para detectar
  fases lentas y para comparar runs. Encaja con el event log de
  S2: cada StepEnded ya tendra `ts`, solo hay que restar.
  Coste: ~15 min (parser + renderer). Esperar a S2 para no
  duplicar logica de tiempo.

## Harness v3 (post S1-ter)

- **S1-ter no cubre bucles funcionales (run #5 OVERPAPER)**: el
  detector actual busca repeticion de `(tool, args)`. En el run #5
  el modelo corrigio errores de sintaxis sin repetir la misma
  llamada (escribir con error -> leer -> editar -> OK). Cada
  intento usaba tools/args distintos, asi que S1-ter no disparo.
  El bucle "verificar -> reparar -> verificar -> reparar" es el
  caso que cubre VRR-Stop (S5). NO es bug de S1-ter.

- **Bootstrap diagnostico miente sobre el workspace**: `bootstrap.py`
  hardcodea `ROOT / "workspace"` y nunca lee `config.json`. El
  diagnostico muestra una ruta distinta a la que el ChatWorker usa
  realmente. Identificado en run #3 OVERPAPER, sigue pendiente.
  Fix: leer `AppConfig.load().workspace`. Coste: ~15 min.

- **Guard "Reduccion drastica" en escribir_archivo**: bloquea
  reescrituras que reducen >80% del tamano del archivo. Util en
  casos reales (run #5: modelo intento borrar gui.py), pero
  bloquea reinicios legitimos si el workspace no se limpio antes.
  Valorar relajar con flag `force=true` (requiere confirmacion) o
  dejarlo como esta. Por ahora: se documenta y se limpia el
  workspace a mano. Coste: ~30 min si se implementa.

- **Dialogo de confirmacion sin motivo (Overpaper run #2)**:
  cuando un comando cae a confirmacion por allowlist, el dialogo
  no lo dice. Requiere ampliar `Signal(str, object)` a
  `Signal(str, object, str)`. Coste: ~30 min.

## Consolidacion (heredado)

- **RAG chunking ponderado** (`core/rag_index.py::_chunk_text`):
  ponderar secciones (nombre x3, docstring x2, body x1). ~30 min
  + benchmark antes/despues.
- **D2 httpx.Client persistente**: DECIDIDO NO HACER (documentado
  en model_capabilities.py lineas 55-56).
- **ruff 52 residuales**: SIM105/RUF005/SIM1xx/SIM108
  intencionales. No tocar.
- **S-mypy-tests**: analizar tests con mypy.

---

**Nota**: no editar este fichero para "hacer seguimiento". Es una
lista de entrada, no un kanban. Cuando algo se hace, se borra de
aqui y aparece en el commit correspondiente.
