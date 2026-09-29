# Plugin shell — contrato y garantías

**Código**: `plugins/shell/` (`client.py`, `provider.py`)
**Herramienta expuesta**: `ejecutar_comando`
**Última revisión**: septiembre 2026 (auditoría externa, P2#5)

## Qué hace

Ejecuta un comando de shell simple dentro del workspace y devuelve
su salida (stdout + stderr combinados) al modelo.

    ejecutar_comando(command="pytest -q", cwd=".", timeout_seconds=60)

Ejemplos válidos: `ls -la`, `pytest -q`, `python -m compileall .`.

## Qué NO es

**No es un sandbox.** El comando corre en el host, con los permisos
del usuario que ejecuta ChatPerezoso, sin aislamiento de:

- **Filesystem**: el binario lanzado puede leer y escribir fuera del
  workspace. El `cwd` se valida dentro del workspace, pero un
  `cat /etc/passwd` sigue funcionando (no hay metacaracteres, pero
  `cat` con argumento absoluto sí pasa el filtro de sintaxis).
- **Red**: cualquier binario (`curl`, `git fetch`, etc.) puede salir
  a Internet.
- **Usuario**: no hay `sudo` ni escalada, pero se hereda el usuario
  de la sesión.

Lo que **sí** hay son restricciones de sintaxis y confirmación
obligatoria, no aislamiento. Un plugin "sandbox" de verdad requeriría
contenedores, `bubblewrap`, `firejail` o equivalentes; no está en el
alcance actual del proyecto.

## Garantías reales

### 1. Confirmación siempre obligatoria

`ShellProvider.requires_confirmation()` devuelve **True** para
`ejecutar_comando` sin excepciones. La UI pide confirmación explícita
al usuario antes de invocar el cliente.

**No hay modo autopilot para shell.** Aunque el chat esté en modo
autopilot con `allow_destructive=True`, el gate de confirmación sigue
aplicando.

### 2. Defensa en profundidad

`ShellProvider.call()` rechaza la llamada si `allow_destructive` es
False, con un mensaje explícito:

    ERROR: ejecutar_comando requiere confirmación explícita del usuario.

Esto cubre el caso de un llamante futuro que invoque el provider sin
pasar por el gate de la UI. Aunque hoy redundante, es la única
garantía si se añade un nuevo cliente.

### 3. Restricciones de sintaxis (en `ShellClient`)

- **Sin `shell=True`**: `subprocess.run(list, shell=False)`.
- **Sin metacaracteres**: rechaza `|`, `;`, `&`, `>`, `<`, backticks,
  `$()`, comillas mal balanceadas.
- **Timeout duro**: `DEFAULT_TIMEOUT_SECONDS` por defecto,
  `MAX_TIMEOUT_SECONDS` como tope máximo. El proceso se mata si
  excede.
- **Entorno mínimo**: el subproceso hereda un `env` reducido (sin
  variables sensibles tipo tokens).
- **`analyze_risk`**: heurística de patrones peligrosos (`rm -rf`,
  `dd`, `chmod 777` en rutas críticas) que se muestra al usuario en
  el diálogo de confirmación.

### 4. Cancelación cooperativa

`cancel_event: threading.Event` se propaga hasta el cliente. Si el
usuario cancela el diálogo, el proceso se aborta.

## Resumen del contrato

| Aspecto | Garantía |
|---|---|
| Confirmación | **Siempre** obligatoria |
| Autopilot | Bloqueado para shell |
| Shell injection | Filtro de metacaracteres (bloquea todo) |
| Timeout | Duro, tope `MAX_TIMEOUT_SECONDS` |
| Entorno | Reducido, sin variables sensibles |
| Sandbox filesystem | **No** |
| Sandbox red | **No** |
| Sandbox usuario | **No** |

La decisión de diseño es **conservadora sin ser sandbox**. Se asume
que el usuario confirma con criterio y que el riesgo es aceptable
para una herramienta de desarrollo personal. Un sandbox real se
estudiaría en el Bloque E (allowlist shell para autopilot), que
tampoco eliminaría el aislamiento, sino que definiría qué comandos
exactos se permiten sin confirmación.
