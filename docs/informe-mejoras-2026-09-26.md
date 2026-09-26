# Informe de mejoras - sesion de auditoria en chat (2026-09-26)

Complementa (no repite) `docs/audit-2026-09.md`,
`docs/auditoria-2026-09-26-completa.md` y
`docs/auditoria-2026-09-26-delta.md`. Cubre lo que esos tres
documentos marcaban como no auditado: `core/history.py`,
`ui/controllers/{mcp,agent,diagnostics,model}_controller.py`,
`plugins/{shell,git,verificador}/client.py`, `ui/views/*.py`,
`ui/widgets.py`, `ui/diagnostics.py`.

Revision post-auditoria (misma fecha, sesion 2). Tras aplicar los
fixes, se verificaron los hallazgos contra el estado real del repo
con `git log`, `git stash` y ejecucion de la suite. Tres hallazgos
del informe original quedaron re-clasificados (M1, M2, M6) y uno
ya estaba resuelto (M8). Se marcan con [REVISADO].

## Resumen

Tabla de hallazgos (severidad original -> final, estado, commit):

- M1  MCPController._on_loaded/_on_error sin @Slot
      MEDIO -> BAJO | RESUELTO 95407a2
- M2  Carrera en ShellClient._terminate() al cancelar
      ALTO -> BAJO | RESUELTO 95407a2
- M3  DiagnosticsController._refresh_context() codigo muerto
      BAJO | RESUELTO 95407a2
- M4  ModelController sin ningun test
      MEDIO | Abierto
- M5  DiagnosticsController sin test de wiring real
      BAJO/MEDIO | Abierto
- M6  Tests de regresion para D7/M1/M2
      BAJO | PARCIAL (D7 fdff32d, M2 95407a2; M1 cosmetico sin test)
- M7  Zip parcheado sin tests/ ni JSONs de runtime
      INFORMATIVO | Sin accion de codigo
- M8  apply_d7.py en la raiz
      BAJO | RESUELTO 4eb64ab

Sin hallazgos en: core/history.py, plugins/git/client.py,
plugins/verificador/client.py, ui/views/*.py, ui/widgets.py,
ui/diagnostics.py, ui/controllers/agent_controller.py (colision
de nombres al renombrar agente es intencional y esta cubierta por
test_edit_active_rename_to_existing_name_drops_the_other),
ui/controllers/model_controller.py (codigo, no cobertura).

## M1 - @Slot ausente en mcp_controller.py (BAJO) [REVISADO]

Archivo: ui/controllers/mcp_controller.py, _on_loaded y _on_error.

MCPWorker corre en QThread propio via moveToThread y emite
finished = Signal(str, object, list) / error = Signal(str, str).
Conectadas a _on_loaded / _on_error sin @Slot(...).

Correccion respecto al informe original. Lo clasifique como
"mismo patron que D4", pero eso es falso. git log -S "@Slot" en
ui/controllers/mcp_controller.py no devuelve ningun commit:
nunca tuvieron @Slot. D4 fue una regresion (un refactor los
elimino); M1 nunca lo fue. PySide6 conecta cross-thread igual sin
el decorador (solo marshaling menos eficiente), severidad real
BAJO/informativo.

Aplicado (consistencia estilistica con app_controller.py, no bug):
- @Slot(str, object, list) sobre _on_loaded.
- @Slot(str, str) sobre _on_error.
- import Slot anadido.
- Commit 95407a2.

Verificacion: sin test (cosmetico).

## M2 - Carrera en ShellClient._terminate() (BAJO) [REVISADO]

Archivo: plugins/shell/client.py, _run() y _terminate().

Hipotesis: al cancelar, el hilo llamante (bloqueado en
proc.communicate(timeout=timeout)) y _watch_cancel (que llama a
_terminate() -> proc.communicate(timeout=2)) acaban llamando a
communicate() concurrentemente sobre el mismo Popen - uso no
soportado oficialmente.

Prueba empirica: 70 iteraciones del escenario (cancel a
150-200 ms, con sleep 3 y con yes) - 0 excepciones, 0 cuelgues,
100% mensaje de cancelacion. La llamada concurrente cae dentro
de un except Exception: pass.

Severidad final: BAJO/informativo.

Correccion respecto al informe original. El informe decia
"hardening aplicado". Era falso: git stash list, git branch -a y
git log --all -S "proc.wait" sin resultados. Aplicado ahora:

  antes:  proc.communicate(timeout=2)
  ahora:  proc.wait(timeout=2)

wait() no toca los pipes, no compite con el communicate() del
hilo llamante.

Verificacion: tests/test_shell_cancel_race.py (nuevo, 10
iteraciones sleep 2 + cancel a 150 ms). Commit 95407a2.

## M3 - Codigo muerto en DiagnosticsController (BAJO)

_refresh_context() era copia exacta de refresh_context().
Unico caller era un test. Borrado metodo + test huerfano en
95407a2.

## M4 - ModelController sin cobertura (MEDIO, abierto)

grep -rl "ModelController" tests/*.py sin resultados. Unico
controller de ui/controllers/ con threading real (QThread +
ModelWorker) sin cobertura: ni carga feliz, ni error, ni
shutdown() con deadline.

Sugerido: test con ModelWorker fake cubriendo: load() no
relanza si ya hay thread en curso, loaded/error se emiten,
_cleanup() limpia _thread/_worker, y shutdown(deadline=...)
devuelve False si el presupuesto se agota antes.

## M5 - DiagnosticsController sin test propio (BAJO/MEDIO, abierto)

test_diagnostics.py cubre SessionStats (dataclass), no el
controller que conecta state_changed/conversation_changed/
textual_tool_attempt. test_senior_fixes.py instancia
DiagnosticsController pero conviene auditar que cubre exactamente
ese test antes de asumir wiring probado end-to-end.

## M6 - Tests de regresion (BAJO) [REVISADO]

- D7: cubierto en fdff32d (4 tests unitarios + 2 integracion
  en tests/test_tools_aliases.py).
- M2: cubierto en 95407a2 (tests/test_shell_cancel_race.py).
- M1: sin test, deliberado (cosmetico).

## M7 - Proceso, no codigo

El zip parcheado no incluia tests/ ni los JSONs de runtime. No
bloqueo la auditoria de codigo, pero impidio correr la suite
contra el parche.

## M8 - apply_d7.py en la raiz (BAJO) [RESUELTO]

Eliminado en 4eb64ab + .gitignore con apply_*.py.

## Orden sugerido si se sigue

1. ~~M1~~ 95407a2
2. ~~M3~~ 95407a2
3. ~~M2~~ 95407a2
4. ~~M8~~ 4eb64ab
5. M4 - cobertura de ModelController (sesion corta).
6. M5 - cobertura de DiagnosticsController tras auditar
   test_senior_fixes.py.

## Fuera de alcance

No se relleno benchmarking internacional multilingue ni tabla
comparativa de proyectos de referencia: habria exigido inventar
cifras no verificables. Si se quiere, encargo aparte con busquedas
concretas.
