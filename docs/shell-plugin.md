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
- **Entorno reducido**: el subproceso hereda solo
  `PATH`, `HOME`, `LANG`, `LC_ALL`, `TMPDIR`, `SHELL`, `USER`, más
  `GIT_TERMINAL_PROMPT=0`, `PAGER=cat` y `GIT_PAGER=cat`. Nada de
  tokens, SSH_AUTH_SOCK, AWS_*, etc. No es un sandbox, pero reduce
  la superficie respecto al entorno completo del usuario.
- **`analyze_risk`**: heurística de patrones peligrosos (`rm -rf`,
  `dd`, `chmod 777` en rutas críticas) que se muestra al usuario en
  el diálogo de confirmación.

### 4. Cancelación cooperativa

`cancel_event: threading.Event` se propaga hasta el cliente. Si el
usuario cancela el diálogo, el proceso se aborta.

## Autopilot + allowlist (Bloque E)

Cuando `auto_approve_tools` y `auto_approve_shell` estan activos a la
vez, `ejecutar_comando` se auto-aprueba **solo si el comando pasa la
allowlist** (`plugins/shell/allowlist.py`). Un comando fuera de la
lista **degrada a confirmacion manual**: el usuario ve el dialogo
normal, no se bloquea.

### Motivo

Sin la allowlist, ese par de flags era "el modelo puede ejecutar
cualquier binario del PATH sin preguntar" — incluido `rm`, `mv`,
`git push` o `pip install`. Pensados para iterar rapido con `pytest`
y `ls`, activaban en realidad un piloto automatico sin freno.

Con la allowlist, el par de flags significa "auto-aprobar los
comandos de lectura y de test, seguir preguntando por lo demas".

### Que se auto-aprueba

- **Filesystem read-only**: `ls`, `cat`, `head`, `tail`, `wc`,
  `grep`, `find` (sin `-exec`/`-delete`/`-fprint*`), `file`,
  `which`, `diff`, `tree`, `pwd`, `echo`, `stat`, `du`, `df`,
  `basename`, `dirname`, `readlink`, `realpath`, `date`, `uname`,
  `whoami`, `id`.
- **Tests / ejecucion controlada**: `pytest`, `python`, `python3`
  (rechaza `-c` y `-i`; `-m` solo modulos de la lista blanca:
  `pytest`, `unittest`, `json.tool`, `pydoc`).
- **git read-only**: `status`, `log`, `diff`, `show`, `branch`,
  `remote`, `config`, `rev-parse`, `ls-files`, `blame`, `describe`,
  `tag`, `stash` (sin args o con `list`).

### Que NO se auto-aprueba (aunque el par de flags este ON)

- **Mutantes del filesystem**: `rm`, `mv`, `cp`, `chmod`, `chown`,
  `sed`, `tee`, y en general cualquier programa que no figure en
  `ALLOWED_PROGRAMS` (incluye `sort` y `uniq`, incluso en uso
  read-only, por prudencia: tienen flags que escriben ficheros).
- **Red**: `curl`, `wget`.
- **Instalacion**: `pip install`, `npm install`.
- **Escalada**: `sudo`, `su`, `doas`.
- **git mutante**: `push`, `reset`, `checkout`, `clean`, `commit`,
  `rebase`, `merge`, `stash drop/pop/apply`.
- **Metacaracteres**: `&&`, `||`, `;`, `|`, `>`, `<`, backticks,
  `$(`, `${` — los rechaza `_validate_command` y por tanto tambien
  la allowlist.
- **Bypass conocidos**: `env VAR=x ls` (lanza otro comando),
  `python -c ...` (codigo inline), `find . -exec ...` (ejecuta
  comandos), `sort -o out.txt` (escribe fichero).

### Como se degrada

No es bloqueo: es degradacion a confirmacion. El worker calcula
`auto` con `_is_auto_approved`, y si la tool es `ejecutar_comando` y
`is_command_allowed(command)` devuelve False, vuelve a
`auto = False`. El flujo sigue como si autopilot estuviera OFF para
ese comando concreto: aparece el dialogo de confirmacion de siempre.

Eso evita dos males: por un lado, no rompe la sesion del usuario con
un "comando bloqueado" criptico; por otro, no auto-aprueba lo que no
deberia.

### Como extender la allowlist

Editar `plugins/shell/allowlist.py`. Las reglas viven en tres sitios:

- `ALLOWED_PROGRAMS`: el conjunto de programas permitidos.
- `GIT_READONLY_SUBCOMMANDS`: subcomandos de git permitidos.
- `PYTHON_READONLY_MODULES`: modulos `python -m X` permitidos.

Los casos especiales (git, python, find) tienen funciones dedicadas
(`_git_allowed`, `_python_allowed`, `_find_allowed`). Cualquier
programa que acepte escribir ficheros como efecto colateral
(`sort -o`, `tee`, `sed -i`) debe quedar fuera, aunque el uso
"tipico" sea read-only.

## Resumen del contrato

| Aspecto | Garantía |
|---|---|
| Confirmación | **Siempre** obligatoria |
| Autopilot | Bloqueado para shell |
| Autopilot + shell ON | Solo allowlist; resto -> confirmacion |
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
