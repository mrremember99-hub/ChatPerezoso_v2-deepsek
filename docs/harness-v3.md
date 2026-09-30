# Harness v3 — Especificación técnica

**Estado**: diseño cerrado, pendiente de implementación.
**Reemplaza**: el ciclo de agente monolítico en `ui/workers.py`.
**No toca**: `ui/views/`, `plugins/`, `core/config.py`, `agents.py`,
`history.py`, `workspace.py`, `ast_index.py`, `rag_index.py`,
`ollama.py`, `scripts/`.

## Índice

- [§0 Principios de diseño](#0-principios-de-diseño)
- [§1 Contrato público](#1-contrato-público)
- [§2 Modelo de eventos](#2-modelo-de-eventos)
- [§3 Loop detection multi-patrón](#3-loop-detection-multi-patrón)
- [§4 Durable execution](#4-durable-execution)
- [§5 Tool schema compilation](#5-tool-schema-compilation)
- [§6 Verificación integrada](#6-verificación-integrada)
- [§7 Health monitoring](#7-health-monitoring)
- [§8 Completion verification](#8-completion-verification)
- [§9 Arquitectura de migración](#9-arquitectura-de-migración)
- [§10 Plan de implementación](#10-plan-de-implementación)
- [§11 Criterios de aceptación](#11-criterios-de-aceptación)

---

## 0. Principios de diseño

Estos principios guían cada decisión del harness. Si una decisión
los contradice, la decisión es la que está mal, no el principio.

### 0.1. El harness es una librería, no una app

No sabe de Qt, ni de ventanas, ni de señales. Se testea entero con
`pytest` sin `qapp`. La UI es un cliente más.

### 0.2. La UI es un renderizador de eventos

No decide nada. Recibe eventos, los pinta, y si el usuario
interactúa (confirma, cancela, aprueba), emite una respuesta de
vuelta al harness. Cero lógica de negocio en la UI.

### 0.3. Todo es un evento

Cada acción — desde un delta de token hasta un checkpoint — se
registra como un evento inmutable **antes** de proceder. El estado
del run es un fold sobre el log de eventos. Esto da: resume tras
crash, replay para debug, time-travel, observabilidad gratis.

### 0.4. El modelo es una dependencia, no un componente

El harness no sabe si el LLM es Ollama, OpenAI o un mock de test.
Recibe un `ModelClient` con una interfaz mínima. Cambiar de
proveedor no toca el harness.

### 0.5. La política es explícita y configurable

Autopilot, allowlist, verificación, confirmaciones, umbrales de
loop — todo vive en `HarnessPolicy`, no disperso por el código.
Cambiar comportamiento = cambiar un objeto.

### 0.6. Fail-safe por defecto

Ante duda (loop, schema ambiguo, error de tool), el harness
**degrada a más control humano**, no a menos. Nunca auto-aprueba
algo que no está explícitamente permitido.

### 0.7. Construcción incremental con `main` intacto

El harness vive en `core/harness/`. La UI actual sigue funcionando
hasta que la migración esté completa. Cero big-bang. Cada slice es
deployable y reversible.

### 0.8. Evidencia antes que fe

Cuando el modelo dice "hecho", el harness verifica. Cuando el
modelo dice "verificado", el harness comprueba que la verificación
se ejecutó de verdad. Nunca se acepta una declaración sin
evidencia.

### 0.9. El detector no mata, inyecta

Cuando hay un bucle, el harness no aborta. Inyecta un prompt
correctivo con diagnóstico y sugerencia. Solo aborta cuando el
modelo demuestra que no puede romper el ciclo tras N intentos.

### 0.10. El estado no vive en memoria

El event log es la fuente de verdad. La memoria del proceso es solo
una caché. Cualquier componente que mantenga estado propio debe
poder reconstruirse desde los eventos.

---

## 1. Contrato público

Esto es lo que la UI (o cualquier cliente) consume. **Es la
superficie API que no puede cambiar sin romper.**

### 1.1. Configuración

    @dataclass(frozen=True)
    class HarnessConfig:
        run_id: str                     # ID único del run
        workspace_root: Path            # dónde escribe el agente
        policy: HarnessPolicy           # autopilot, allowlist, umbrales
        model: ModelSpec                # modelo + parámetros
        agent: AgentSpec                # system prompt, tools permitidas
        storage_dir: Path               # dónde vive el event log

        # Flags de activación por slice (ver §9)
        loop_detection_enabled: bool = True
        health_monitoring_enabled: bool = True
        durable_enabled: bool = False
        schema_compilation_enabled: bool = False
        completion_verification_enabled: bool = False

### 1.2. HarnessSession

    class HarnessSession:
        """Una sesión de agente. Estado + ciclo de ejecución."""

        def __init__(
            self,
            config: HarnessConfig,
            *,
            model_client: ModelClient,
            tool_registry: ToolRegistry,
            event_sink: EventSink | None = None,
        ) -> None: ...

        def step(self, user_message: str) -> Iterator[Event]:
            """Ejecuta un turno completo del agente.

            Emite eventos según ocurren: deltas de texto, tool calls,
            confirmaciones pendientes, warnings de loop, checkpoints.
            Se consume como generador (streaming) o se materializa a
            lista (batch).
            """

        def resume(self) -> Iterator[Event]:
            """Reanuda desde el último checkpoint.

            Si el run terminó, no-op. Si murió a mitad, reconstruye
            estado desde el event log y continúa desde el último step
            incompleto.
            """

        def cancel(self) -> None:
            """Cancela el step en curso. Idempotente."""

        def health(self) -> HealthSnapshot:
            """Métricas del run actual (loop/stuck/thrash/cost)."""

        def events(self, since_seq: int = 0) -> Iterator[Event]:
            """Replay del event log. Sin efectos secundarios."""

        def pending_confirmation(self) -> ConfirmationRequest | None:
            """Si hay una confirmación pendiente, la devuelve."""

        def resolve_confirmation(
            self, response: ConfirmationResponse
        ) -> None:
            """La UI responde a una confirmación pendiente."""

**Eso es todo lo que la UI necesita.** No hay `set_autopilot()`,
`set_verify()`, `on_tool_call()`. Todo se comunica por:

- **Entrada**: `step()` / `resume()` / `cancel()` / `resolve_confirmation()`
- **Salida**: iteración de `Event`

### 1.3. ModelClient

    class ModelClient(Protocol):
        def chat(
            self,
            messages: list[dict],
            *,
            tools: list[ToolSchema] | None = None,
            stream: bool = True,
            cancel_event: threading.Event | None = None,
        ) -> Iterator[ModelDelta]: ...

        def capabilities(self) -> ModelCapabilities: ...

**Mínimo viable.** El harness no necesita más.

### 1.4. ToolRegistry

    class ToolRegistry(Protocol):
        def register(
            self,
            name: str,
            schema: dict,
            *,
            idempotent: bool = False,
            requires_confirmation: bool = False,
        ) -> None: ...

        def get(self, name: str) -> RegisteredTool | None: ...

        def render_for_prompt(self) -> str:
            """Schemas compilados como texto (§5)."""
            ...

        def execute(
            self,
            name: str,
            arguments: dict,
            *,
            cancel_event: threading.Event | None = None,
        ) -> ToolResult: ...

### 1.5. Contrato con la UI

La UI **no accede** a `ToolRegistry`, `ModelClient` ni `EventLog`
directamente. Todo pasa por `HarnessSession`. Esto significa:

- La UI **no sabe** qué tools hay disponibles.
- La UI **no sabe** qué modelo se usa.
- La UI **no sabe** si hay un event log, una SQLite, o un JSON.
- La UI **solo sabe** iterar eventos y responder a confirmaciones.

### 1.6. Lo que el harness NO hace

- **No renderiza nada.** Emite eventos; la UI decide cómo pintarlos.
- **No habla con Ollama directamente.** Recibe un `ModelClient`.
- **No conoce tools concretas.** Recibe un `ToolRegistry`.
- **No gestiona ventanas Qt, señales, ni widgets.**
- **No decide el system prompt del agente.** Eso vive en `AgentSpec`.
- **No persiste el historial de conversación.** Eso vive en
  `HistoryStore`, ajeno al harness.
- **No sabe de MCP, git, shell, ni verificador.** Los ve como tools
  más.

---

*Continúa en §2 Modelo de eventos.*

---

## 2. Modelo de eventos

Los eventos son **dataclasses inmutables**, serializables a JSON,
append-only. Cada uno tiene un `seq` monotónico por sesión y un
`kind` que lo discrimina.

### 2.1. Evento base

    @dataclass(frozen=True)
    class Event:
        seq: int              # monotónico por sesión
        run_id: str
        ts: str               # ISO 8601
        kind: str             # discriminante

Cada subclase fija `kind` en `__post_init__`. La UI hace
`match event.kind:` y renderiza. El event log almacena la
representación JSON plana.

### 2.2. Eventos de ciclo de vida

    @dataclass(frozen=True)
    class RunStarted(Event):
        user_message: str
        agent_name: str
        model_name: str

    @dataclass(frozen=True)
    class RunEnded(Event):
        reason: str           # "completed" | "cancelled" |
                              # "error" | "loop_aborted"
        summary: str

    @dataclass(frozen=True)
    class StepStarted(Event):
        step_index: int

    @dataclass(frozen=True)
    class StepEnded(Event):
        step_index: int
        outcome: str          # "ok" | "failed" | "loop_broken"

### 2.3. Eventos de streaming del modelo

    @dataclass(frozen=True)
    class MessageDelta(Event):
        role: str             # "assistant"
        content: str          # fragmento

    @dataclass(frozen=True)
    class MessageCompleted(Event):
        role: str
        content: str          # completo

### 2.4. Eventos de tool calls

    @dataclass(frozen=True)
    class ToolCallRequested(Event):
        call_id: str
        tool_name: str
        arguments: dict
        auto_approved: bool

    @dataclass(frozen=True)
    class ToolCallCompleted(Event):
        call_id: str
        tool_name: str
        status: str           # "ok" | "error" | "cancelled"
        summary: str
        detail: str
        duration_ms: int

    @dataclass(frozen=True)
    class ConfirmationRequested(Event):
        call_id: str
        tool_name: str
        arguments: dict
        reason: str           # "allowlist" | "destructive" | "policy"

    @dataclass(frozen=True)
    class ConfirmationResolved(Event):
        call_id: str
        approved: bool
        timeout: bool         # True si fue timeout, no click

### 2.5. Eventos de verificación

    @dataclass(frozen=True)
    class VerificationRun(Event):
        call_id: str
        target: str           # path verificado
        issues: list[dict]    # [{code, line, message}]

    @dataclass(frozen=True)
    class VerificationAttached(Event):
        call_id: str
        summary: str          # "OK" | "N issues"

### 2.6. Eventos de loop detection

    @dataclass(frozen=True)
    class LoopWarning(Event):
        detector: str         # "generic_repeat" | "ping_pong" |
                              # "poll" | "post_compact"
        signature: str        # hash del patrón detectado
        count: int

    @dataclass(frozen=True)
    class LoopCorrectivePrompt(Event):
        prompt: str           # prompt inyectado al modelo
        detector: str

    @dataclass(frozen=True)
    class LoopAborted(Event):
        detector: str
        reason: str

### 2.7. Eventos de checkpoint

    @dataclass(frozen=True)
    class CheckpointSaved(Event):
        checkpoint_id: str
        step_index: int
        snapshot: dict        # estado mínimo para resume

### 2.8. Eventos de health

    @dataclass(frozen=True)
    class HealthSnapshot(Event):
        step_index: int
        findings_count: int
        coverage_score: float
        total_tokens: int
        error_count: int
        signals: list[str]    # ["loop"] | ["stuck"] | ["thrash"]

### 2.9. Eventos de error

    @dataclass(frozen=True)
    class HarnessError(Event):
        component: str        # "model" | "tool" | "storage"
        message: str
        recoverable: bool

    @dataclass(frozen=True)
    class HarnessWarning(Event):
        kind: str             # "resume_with_changes" | ...
        details: list[dict]

### 2.10. Ciclo de vida de un step

Un `step(user_message)` pasa por estados bien definidos. Los
eventos marcan las transiciones:

    step("leé gui.py y agregá la sección MODE")
    │
    ├─▶ StepStarted
    │
    ├─▶ [Modelo genera]
    │    ├─▶ MessageDelta xN        ← streaming visible en UI
    │    ├─▶ MessageCompleted
    │    │
    │    ├─▶ Si tool call:
    │    │    ├─▶ ToolCallRequested
    │    │    │
    │    │    ├─▶ ¿Requiere confirmación?
    │    │    │   ├─▶ Sí: ConfirmationRequested
    │    │    │   │      → (UI responde)
    │    │    │   │      → ConfirmationResolved
    │    │    │   └─▶ No: auto-aprobado
    │    │    │
    │    │    ├─▶ [Se ejecuta la tool]
    │    │    ├─▶ ToolCallCompleted
    │    │    │
    │    │    ├─▶ ¿Requiere verificación?
    │    │    │   ├─▶ Sí: VerificationRun + VerificationAttached
    │    │    │   └─▶ No
    │    │    │
    │    │    └─▶ ¿Loop detectado?
    │    │         ├─▶ Warning: LoopWarning
    │    │         ├─▶ Correctivo: LoopCorrectivePrompt
    │    │         │   → vuelve a [Modelo genera]
    │    │         └─▶ Aborto: LoopAborted → RunEnded
    │    │
    │    └─▶ ¿El modelo terminó (no más tool calls)?
    │
    ├─▶ CheckpointSaved             ← tras cada step completo
    │
    └─▶ StepEnded

**El event log es la fuente de verdad.** El estado del
`HarnessSession` en memoria es una caché. Si el proceso muere,
`resume()` reconstruye desde el log.

---

## 3. Loop detection multi-patrón

Esta capa es la que rompe el goteo. Sin ella, cada run es una
lotería. Con ella, el bucle se rompe en el **segundo intento**, no
en el vigésimo.

### 3.1. Principio rector

> **El detector no mata el run. Le dice al modelo qué está
> haciendo mal y le da la oportunidad de corregir. Solo aborta
> cuando el modelo demuestra que no puede romper el ciclo.**

Esto es la diferencia clave con lo que hay hoy. El detector actual
aborta. El modelo nunca aprende qué estaba mal.

### 3.2. Los 4 detectores

#### 3.2.1. genericRepeat

**Qué detecta**: el mismo `(tool, args_normalizados)` aparece N
veces consecutivas en la ventana.

**Ventana**: últimas 30 llamadas (configurable).

**Umbrales**:
- N=3 → `LoopWarning` (warning, no bloquea)
- N=5 → `LoopCorrectivePrompt` (inyecta correctivo)
- N=8 → `LoopAborted` (corta el run)

**Excepción**: no cuenta repeticiones si entre dos llamadas
idénticas hay una llamada con un argumento distinto al mismo tool.
Eso indica exploración legítima. Solo cuenta si las N son
**estrictamente consecutivas**.

#### 3.2.2. pingPong

**Qué detecta**: patrón `(A, B, A, B)` entre dos tool calls, o
entre una tool call y un mensaje.

**Ventana**: últimos 6 elementos del ciclo.

**Umbrales**:
- 2 ciclos completos → `LoopWarning`
- 3 ciclos completos → `LoopCorrectivePrompt`
- 4 ciclos completos → `LoopAborted`

**Ejemplo que caza** (el bucle del run #3):

    insertar_en_archivo(gui.py, "SLATS...", "tras linea 4")
    verificar_codigo(gui.py)             → F821
    insertar_en_archivo(gui.py, "SLATS...", "tras linea 4")
    verificar_codigo(gui.py)             → F821
    insertar_en_archivo(gui.py, "SLATS...", "tras linea 4")
    → Correctivo

**Detección**:

    def detect_ping_pong(
        signatures: list[str],
        min_period: int = 2,
        min_repeats: int = 2,
    ) -> tuple[str, int] | None:
        """Busca el período más pequeño que se repite."""
        for period in range(min_period, 5):
            if len(signatures) < period * min_repeats:
                continue
            window = signatures[-period * min_repeats:]
            pattern = window[:period]
            repeats = 0
            for i in range(0, len(window), period):
                if window[i:i+period] == pattern:
                    repeats += 1
                else:
                    break
            if repeats >= min_repeats:
                return ("|".join(pattern), repeats)
        return None

#### 3.2.3. knownPollNoProgress

**Qué detecta**: patrones de polling conocidos (leer estado,
esperar, volver a leer) que no cambian el resultado.

**Firmas de polling**:
- `leer_archivo(X)` con el mismo X y mismo resultado dos veces
- `git_status()` con mismo output dos veces
- `listar_carpeta(X)` con mismo listado dos veces

**Ventana**: últimas 10 llamadas.

**Umbrales**:
- 2 repeticiones con mismo resultado → `LoopWarning`
- 3 repeticiones con mismo resultado → `LoopCorrectivePrompt`
- 4 repeticiones con mismo resultado → `LoopAborted`

**Diferencia con genericRepeat**: genericRepeat mira
`(tool, args)`. knownPollNoProgress mira
`(tool, args, result_signature)`. Un polling puede tener args
ligeramente distintos pero resultado idéntico. Eso es el verdadero
síntoma de "no hay progreso".

#### 3.2.4. postCompactionGuard

**Qué detecta**: cuando el harness compacta el contexto, el modelo
a veces **repite el mismo ciclo que causó la compactación**.

**Cómo funciona**:
- Al emitirse un evento `ContextCompacted` (nuevo), el guard se arma.
- Durante las siguientes 3 tool calls, si
  `(tool, args, result_signature)` coincide con alguna de las 5
  tool calls **previas a la compactación**, dispara.
- Umbral: 1 match tras compactación → `LoopCorrectivePrompt`.

**Por qué tan agresivo**: si el modelo repite un patrón justo
después de compactar, la compactación no rompió el ciclo mental.

### 3.3. Normalización de firmas

El punto más sutil. Dos llamadas **semánticamente idénticas**
pueden diferir en: orden de keys del JSON, espacios en blanco en
strings largos, comillas, escapes.

**Firma canónica**:

    def canonical_signature(tool: str, args: dict) -> str:
        normalized = json.dumps(
            args,
            sort_keys=True,
            separators=(",", ":"),      # sin espacios
            ensure_ascii=False,
        )
        normalized = _collapse_whitespace(normalized)
        return hashlib.sha256(
            f"{tool}|{normalized}".encode("utf-8")
        ).hexdigest()[:16]

**Para editar_archivo / insertar_en_archivo**: normaliza
`old_string` y `new_string` con:
- Strip de whitespace al inicio/final de cada línea
- Colapso de múltiples espacios
- Preservación de indentación (es semántica)

### 3.4. Escalation ladder

    Cada evento de tool completado:
      1. Normalizar firma
      2. Empujar al ring buffer de cada detector
      3. Cada detector devuelve (matched, count)
      4. Escalera decide:
         ├─ Si algún detector count >= ABORT_THRESHOLD
         │    → LoopAborted
         ├─ Si algún detector count >= CORRECTIVE_THRESHOLD
         │    → LoopCorrectivePrompt
         └─ Si algún detector count >= WARNING_THRESHOLD
              → LoopWarning

**Umbrales consolidados** (por defecto):

| Detector | Warning | Corrective | Abort |
|---|---|---|---|
| genericRepeat | 3 | 5 | 8 |
| pingPong | 2 ciclos | 3 ciclos | 4 ciclos |
| pollNoProgress | 2 | 3 | 4 |
| postCompactionGuard | — | 1 | 3 |

**Configurables en HarnessPolicy**:

    @dataclass(frozen=True)
    class LoopPolicy:
        enabled: bool = True
        window_size: int = 30
        generic_repeat: tuple[int, int, int] = (3, 5, 8)
        ping_pong: tuple[int, int, int] = (2, 3, 4)
        poll_no_progress: tuple[int, int, int] = (2, 3, 4)
        post_compaction: tuple[int, int] = (1, 3)
        max_corrective_attempts: int = 3

### 3.5. Inyección correctiva

Cuando dispara `LoopCorrectivePrompt`, el harness **construye un
mensaje para el modelo** con tres partes:

1. **Diagnóstico**: qué patrón está repitiendo.
2. **Causa probable**: por qué está atascado.
3. **Sugerencia concreta**: qué probar en su lugar.

**Ejemplo para pingPong detectando insertar+verificar**:

    [Harness · Loop detectado]

    Llevas 3 ciclos repitiendo el mismo patrón:
      1. insertar_en_archivo(gui.py, ...)
      2. verificar_codigo(gui.py)

    Diagnóstico: el patrón no está convergiendo. La verificación
    sigue devolviendo el mismo error (F821 en línea 5) tras cada
    intento.

    Causa probable: la variable `left_frame` se usa antes de su
    definición. Insertar el bloque más arriba no lo resuelve.

    Sugerencias:
      · Reescribe el archivo completo con escribir_archivo en vez
        de ediciones parciales.
      · Mueve la definición de `left_frame` ARRIBA del uso.

    No repitas el mismo par de llamadas. Cambia de estrategia o
    explica por qué el error actual no se puede resolver sin
    información adicional del usuario.

**De dónde sale el diagnóstico**: el verificador ya genera
sugerencias accionables por código de error. El
`CorrectivePromptBuilder` reusa esas sugerencias.

**Contador de correctivos**: el harness lleva un contador por run.
Tras `max_corrective_attempts` (por defecto 3), el siguiente
disparo escala a `LoopAborted`.

### 3.6. Integración con el ciclo

    [ToolCallCompleted]
           │
           ▼
    [LoopDetector.observe(tool, args, result)]
           │
           ├─ No match → silencio
           │
           ├─ Warning → LoopWarning (se emite, no interrumpe)
           │
           ├─ Corrective → LoopCorrectivePrompt
           │   │
           │   └─ Se inyecta como mensaje "system" en el
           │      siguiente turno del modelo
           │   └─ Se incrementa correctives_count
           │
           └─ Abort → LoopAborted → RunEnded(reason="loop_aborted")

**El evento `LoopCorrectivePrompt` NO termina el step.** Se emite,
el harness construye el prompt correctivo, y **el mismo step
continúa** con el modelo recibiendo ese prompt en su contexto.

### 3.7. Qué NO es un bucle

El detector tiene que evitar falsos positivos. **No son bucles**:

1. **Iteración legítima sobre N items**: `leer_archivo(f1)`,
   `leer_archivo(f2)`, `leer_archivo(f3)`. Args distintos →
   firmas distintas.
2. **Retry tras error transitorio**: `ejecutar("pytest")` → error
   de red → `ejecutar("pytest")`. Si vuelve a fallar, sí.
3. **Polling con cambio de estado**: `git_status()` → `A` →
   `git_status()` → `B`. Resultado cambió → no es polling.
4. **Exploración legítima**: `grep("foo", f1)`, `grep("bar", f1)`,
   `grep("foo", f2)`. Firmas distintas.

**Regla**: el detector nunca dispara si dos llamadas "repetidas"
tienen **resultados distintos**.

### 3.8. Como activar loop detection (S1-ter)

El codigo de deteccion esta implementado (S1) e integrado en el
worker (S1-bis). Para activarlo en una sesion real:

1. Editar `config.json`:

       "loop_detection_enabled": true

2. Relanzar la app. Al construir el `ChatWorker`, el
   `ChatController` inyecta un `LoopDetector` con la `LoopPolicy`
   por defecto.

3. Cuando el detector dispare, el usuario ve narraciones:

   · "Loop leve (generic_repeat): mismo tool+args 3 veces" — warning
   · "Loop detectado (ping_pong): patron alternante 3 ciclos. El
     agente deberia cambiar de estrategia." — corrective
   · "Loop abortado (generic_repeat): max_corrective_attempts=3
     superado. Sesion cancelada automaticamente." — abort

4. El flag es **OFF por defecto**. En un `config.json` sin la
   clave, `AppConfig.load()` aplica el default `False` y el
   comportamiento es identico al de antes de S1-ter.

### 3.9. Que NO hace todavia el loop detector

En S1-ter el detector **no inyecta el prompt correctivo al modelo**.
Solo emite senales Qt que la UI muestra como narraciones. La
auto-recuperacion (el modelo recibe el correctivo y cambia de
estrategia sin intervencion del usuario) requiere que el harness
controle el ciclo del modelo, lo cual es S4 (session).

Lo que S1-ter ya consigue:

- Si el agente entra en bucle de warning/corrective, el usuario
  **lo ve** en el chat.
- Si el bucle escala a abort (8 repeticiones o 3 correctives
  consecutivos), el worker se **cancela automaticamente**. Ya no
  hay que matar el run a mano.

---

*Continúa en §4 Durable execution.*

---

## 4. Durable execution

Esta capa resuelve el problema más caro: **un crash en F13 tira
1h30m de trabajo**. Con durable execution, al relanzar el harness
lee el event log, ve que F1–F12 están completadas, y solo ejecuta
F13 en adelante. Cero repetición de llamadas al modelo, cero tokens
quemados dos veces.

### 4.1. Principio rector

> **El estado del run no vive en memoria. Vive en el event log. La
> memoria es solo una caché.**

Corolario: si el proceso muere, no perdemos nada. `resume()`
reconstruye el estado desde el log y continúa. La única condición
es que cada operación con side effect esté **registrada antes de
ejecutarse** y sea **idempotente por clave**.

### 4.2. Los 3 cimientos

#### 4.2.1. Event log append-only

**Regla**: todo evento se escribe ANTES de proceder. Si el harness
va a llamar al modelo, primero emite `LLMCallRequested`. Si la
llamada falla, emite `LLMCallFailed`. Si triunfa,
`LLMCallCompleted`. Si el proceso muere entre `Requested` y
`Completed`, al reanudar sabemos que hay una operación **en vuelo**
que necesita ser resuelta.

**Schema**:

    CREATE TABLE events (
        seq         INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id      TEXT NOT NULL,
        ts          TEXT NOT NULL,
        kind        TEXT NOT NULL,
        payload     TEXT NOT NULL
    );
    CREATE INDEX idx_events_run ON events(run_id, seq);

**WAL mode desde el primer connect**:

    self._con.execute("PRAGMA journal_mode=WAL;")
    self._con.execute("PRAGMA synchronous=NORMAL;")
    self._con.execute("PRAGMA busy_timeout=5000;")

**Retención**: por defecto `keep_days: int = 30` y
`keep_last_n: int = 50`. **Nunca borra runs incompletos** (son
candidatos a resume).

#### 4.2.2. Checkpoints por step

Un checkpoint es una **foto del estado mínimo** que permite
reanudar sin re-leer el event log entero.

**Cuándo se guarda**:
- Tras cada `StepEnded(outcome="ok")`.
- Tras cada `LoopCorrectivePrompt` (para no perder el contador).
- Tras cada `ConfirmationResolved` (para no re-preguntar).
- **NO** tras cada `MessageDelta` (sería 100 checkpoints por
  mensaje).

**Qué guarda**:

    @dataclass(frozen=True)
    class CheckpointSnapshot:
        run_id: str
        last_event_seq: int              # hasta dónde cubre
        step_index: int
        messages_summary: list[dict]     # últimos N mensajes
        tools_executed: list[dict]       # últimas M tool calls
        loop_state: dict                 # contadores de detectores
        correctives_count: int
        verification_issues: list[dict]
        agent_spec_hash: str             # detectar cambio de agente
        policy_hash: str                 # detectar cambio de policy

**Lo que NO guarda** (por tamaño):
- El contenido completo de `gui.py` (vive en el workspace).
- La respuesta completa del modelo (vive en el event log).
- Los vectores RAG (viven en su propia DB).

**Schema**:

    CREATE TABLE checkpoints (
        checkpoint_id TEXT PRIMARY KEY,
        run_id        TEXT NOT NULL,
        seq           INTEGER NOT NULL,
        snapshot      TEXT NOT NULL,
        ts            TEXT NOT NULL
    );
    CREATE INDEX idx_checkpoints_run
        ON checkpoints(run_id, seq DESC);

**Retención**: por run, se mantienen los **últimos 3 checkpoints**.
Los anteriores se borran.

#### 4.2.3. Idempotencia por clave

**El problema**: si el harness llamó a `escribir_archivo` y
crasheó **después** de la escritura pero **antes** de emitir
`ToolCallCompleted`, al reanudar podría repetir la escritura. En
un `escribir_archivo` no pasa nada. Pero en un
`ejecutar_comando("pytest")` no queremos ejecutar pytest dos veces.

**La clave de idempotencia**:

    def idempotency_key(
        run_id: str, step_index: int, call_id: str,
    ) -> str:
        return hashlib.sha256(
            f"{run_id}|{step_index}|{call_id}".encode()
        ).hexdigest()[:24]

**Schema**:

    CREATE TABLE executed_ops (
        key       TEXT PRIMARY KEY,
        run_id    TEXT NOT NULL,
        op_type   TEXT NOT NULL,     -- "tool_call" | "model_call"
        call_id   TEXT NOT NULL,
        state     TEXT NOT NULL,     -- "pending" | "completed"
        result    TEXT,              -- JSON, NULL si pending
        ts        TEXT NOT NULL
    );
    CREATE INDEX idx_executed_ops_run
        ON executed_ops(run_id, call_id);

**Flujo de un tool call**:

    1. key = idempotency_key(run_id, step, call_id)
    2. INSERT INTO executed_ops (key, state="pending")
    3. Consultar executed_ops WHERE key = ?
    4. Si state="completed":
         → Ya se ejecutó. Emitir ToolCallCompleted con el result.
         → NO re-ejecutar.
    5. Si state="pending":
         → Murió a mitad. Resolver según idempotencia.
    6. Si no existe (no debería pasar tras el INSERT):
         → Ejecutar la tool.

**Manejo de crash entre pasos**:

- Crash tras INSERT pending pero antes de ejecutar → al reanudar,
  `state="pending"` → reintentar (no hubo side effect).
- Crash tras ejecutar pero antes de UPDATE completed → al
  reanudar, `state="pending"` → reintentar **con cuidado** según
  `idempotent` de la tool.

**Clasificación de tools por idempotencia**:

| Tool | `idempotent` | Razón |
|---|---|---|
| `leer_archivo` | True | Sin side effect |
| `listar_carpeta` | True | Sin side effect |
| `git_status` | True | Sin side effect |
| `buscar_simbolo` | True | Sin side effect |
| `rag_query` | True | Sin side effect |
| `crear_archivo` | False | Segundo intento falla (ya existe) |
| `escribir_archivo` | False | Resultado puede diferir |
| `editar_archivo` | False | Segundo intento falla (old_string ya no está) |
| `insertar_en_archivo` | False | Idem |
| `ejecutar_comando` | False | Side effects externos |

### 4.3. Ciclo de vida de un step con durabilidad

    step(user_message)
    │
    ├─▶ 1. Load state
    │    ├─ Si hay checkpoint reciente → cargar snapshot
    │    └─ Si no → fold del event log hasta el final
    │
    ├─▶ 2. Emit StepStarted
    │    └─ Append a events
    │
    ├─▶ 3. Model call
    │    ├─ Emit LLMCallRequested
    │    ├─ key = idempotency_key(run_id, step, "model_0")
    │    ├─ INSERT executed_ops (state="pending")
    │    ├─ Llamar al modelo
    │    ├─ UPDATE executed_ops (state="completed", result)
    │    └─ Emit LLMCallCompleted
    │
    ├─▶ 4. Por cada tool call que pida el modelo:
    │    ├─ Emit ToolCallRequested
    │    ├─ Verificar executed_ops
    │    ├─ Si pending → skip (crash recovery)
    │    ├─ Ejecutar la tool
    │    ├─ Guardar resultado
    │    └─ Emit ToolCallCompleted
    │
    ├─▶ 5. Loop detection + verificación
    │
    ├─▶ 6. CheckpointManager.save()
    │    └─ Emit CheckpointSaved
    │
    └─▶ 7. Emit StepEnded

**Cada paso emite eventos antes de proceder**. Esto es el "event
sourcing antes que side effect" del paper IEEE.

### 4.4. Resume: qué pasa al relanzar

    def resume(self) -> Iterator[Event]:
        """Reanuda un run interrumpido."""
        # 1. Cargar estado desde el último checkpoint
        state = self._load_last_state()

        # 2. Detectar inconsistencias
        inconsistencies = self._detect_inconsistencies(state)
        if inconsistencies:
            yield HarnessWarning(
                kind="resume_with_changes",
                details=inconsistencies,
            )
            if any(i.severity == "critical" for i in inconsistencies):
                yield RunEnded(reason="resume_incompatible")
                return

        # 3. Resolver operaciones pending
        pending_ops = self._load_pending_ops(state.run_id)
        for op in pending_ops:
            if op.op_type == "model_call":
                yield from self._replay_model_call(op)
            elif op.op_type == "tool_call":
                if op.tool_idempotent:
                    yield from self._replay_tool_call(op)
                else:
                    yield HarnessError(
                        component="tool",
                        message=(
                            f"Operación {op.call_id} murió a mitad "
                            "y no es idempotente. Marcada como fallida."
                        ),
                        recoverable=True,
                    )

        # 4. Continuar desde el último step incompleto
        yield from self._continue_from(state)

**Detección de inconsistencias**:

| Cambio detectado | Severidad | Acción |
|---|---|---|
| `policy_hash` cambió | critical | Abortar |
| `agent_spec_hash` cambió | critical | Abortar |
| `model_name` cambió | warning | Continuar, anotar |
| `workspace_root` no existe | critical | Abortar |
| `workspace_root` cambió externamente | warning | Continuar |
| `checkpoint` de hace > 7 días | warning | Continuar con aviso |

### 4.5. Qué NO es durable

El harness **no intenta** ser durable en:

1. **La respuesta del modelo en streaming**: si el modelo emite 500
   tokens y crashea en el 501, se reintenta desde cero.
2. **El estado interno de la UI**: si la UI crashea pero el harness
   no, la UI re-renderiza desde el event log.
3. **Los side effects externos**: si `ejecutar_comando("curl ...")`
   ya hizo la petición HTTP y crasheó después, la petición no se
   deshace. Solo se garantiza que **no se repite**.
4. **Los ficheros del workspace**: el workspace se persiste en
   disco. Si el usuario edita `gui.py` a mano mientras el harness
   está pausado, el harness no lo sabe hasta que lo lea.

### 4.6. Configuración

    @dataclass(frozen=True)
    class DurablePolicy:
        enabled: bool = True
        checkpoint_after_every_step: bool = True
        checkpoint_after_corrective: bool = True
        checkpoint_after_confirmation: bool = True
        keep_last_checkpoints: int = 3
        events_retention_days: int = 30
        events_retention_min_runs: int = 50
        resume_on_startup: bool = True
        max_resume_age_days: int = 7

**`resume_on_startup`**: si la app crashea y el usuario la relanza,
el harness busca runs incompletos y ofrece reanudarlos. La UI
muestra un diálogo: *"Encontré un run incompleto (F13 de 25).
¿Reanudar?"*.

### 4.7. Integración con la UI

La UI no sabe de checkpoints ni de resume. Solo ve:

1. **Al arrancar**: si hay runs huérfanos, un diálogo opcional.
2. **Durante el run**: eventos normales, más un indicador visual si
   `CheckpointSaved` (barra de estado: "Guardado").
3. **Si el harness detecta resume incompatible**: `HarnessWarning`
   con detalles, la UI lo muestra como notificación.

**El usuario puede**:
- Reanudar el run (botón).
- Descartar el run huérfano (botón, borra el event log).
- Ignorar (el run queda huérfano, no se reanuda).

### 4.8. Aceptación

Esta capa está terminada cuando:

1. **Test de crash simulado**: un `FakeModelClient` que crashea en
   el step 5. Al reanudar, los steps 1–4 no se repiten.
   Verificación: contador de llamadas al modelo = 5 (no 10).
2. **Test de operación pending**: simular crash entre
   `ToolCallRequested` y `ToolCallCompleted`. Al reanudar, si la
   tool es idempotente, se reintenta. Si no, se marca como failed.
3. **Test de inconsistencia**: cambiar `policy_hash` entre
   checkpoint y resume. El harness debe abortar con
   `resume_incompatible`.
4. **Test de retención**: 100 checkpoints creados → solo los
   últimos 3 sobreviven.
5. **Test de WAL**: dos conexiones simultáneas al event log
   (harness + tracker) → ninguna bloquea a la otra.

**Métrica de impacto**: un run OVERPAPER que crashea en F15 debe
reanudar y completar F16–F25 sin repetir F1–F14. Tiempo total de
reanudación < 5 segundos.

---

*Continúa en §5 Tool schema compilation.*

---

## 5. Tool schema compilation

Esta capa resuelve el problema del tool calling poco fiable con
modelos pequeños. El paper TSCG documenta 0% → 90% de accuracy
**solo cambiando el formato de presentación** de los schemas.

### 5.1. El problema cuantificado

El paper TSCG (Sakizli, abril 2026) documenta con precisión: **el
formato JSON no es el problema de capacidad del modelo, es el
problema de representación**.

| Métrica | JSON (baseline) | TSCG | Mejora |
|---|---|---|---|
| Phi-4 14B con 20+ tools | 0% acc | 90% acc | **+90pp** |
| Ahorro de tokens | — | 52-57% | — |
| Schemas por invocación | 3,000-25,000 tok | 1,400-11,000 tok | -51% |
| BFCL (3 modelos frontera) | baseline | 108-181% ARR | — |

**Consecuencias del desajuste de protocolo**:
1. **Coste de tokens**: redundancia estructural transmitida en
   cada llamada.
2. **Coste de capacidad**: los modelos pequeños no parsean JSON
   fiablemente a escala, bloqueando capacidades agénticas.
3. **Coste de escalado**: overhead crece linealmente con el catálogo.

**Aplicado a ChatPerezoso**: el agente "Programador" tiene 10+
tools. Con gpt-oss:20b y qwen3:30b-a3b, el tool calling está en
la zona de colapso (0-49% a >15 tools).

### 5.2. Los 8 operadores TSCG

TSCG transforma JSON schema en texto estructurado token-efficiente
mediante 8 operadores deterministas, en 3 clases:

| Operador | Nombre | Qué hace | Clase |
|---|---|---|---|
| SDM | Semantic Density Maximization | Elimina relleno | Compresión |
| TAS | Tokenizer-Aligned Syntax | Corrige splits BPE | Compresión |
| DRO | Delimiter-Role Optimization | Delimitadores compactos | Compresión |
| CFL | Constraint-First Layout | Constraints al inicio | Estructural |
| CFO | Causal-Forward Ordering | Campos en orden causal | Estructural |
| CAS | Causal Access Score | Reordena por importancia | Fragilidad |
| SAD-F | Selective Anchor Duplication | Duplica anchors al final | Fragilidad |
| CCP | Causal Closure Principle | Bloques de cierre | Fragilidad |

### 5.3. Perfiles por modelo

El paper mide empíricamente 3 perfiles:

| Perfil | Modelo ejemplo | Comportamiento |
|---|---|---|
| operator-hungry | Claude Opus 4.7 | Todos contribuyen |
| operator-sensitive | GPT-5.2 | Algunos dañan |
| operator-robust | Claude Sonnet 4 | Insensible |

**Para gpt-oss:20b y qwen3:30b-a3b**: no hay datos publicados. Hay
que medir empíricamente con el 180-call sweep del paper.

**Configuración conservadora inicial**:
- Activar SDM + TAS + DRO (compresión pura, siempre segura).
- Medir accuracy con y sin CFL/CFO/CAS/SAD-F/CCP.
- Decidir por modelo.

### 5.4. Implementación

**Módulo**: `core/harness/schemas.py`

    @dataclass(frozen=True)
    class CompiledToolSchema:
        tool_name: str
        description: str
        parameters: list[CompiledParam]
        required: list[str]
        examples: list[dict]      # input_examples

        def render(self) -> str:
            """Renderiza como texto token-efficiente."""
            lines = [
                f"## {self.tool_name}",
                f"{self.description}",
                "",
                "Params:",
            ]
            for p in self.parameters:
                req = " (required)" if p.name in self.required else ""
                lines.append(f"  - {p.name}: {p.type}{req}")
                if p.description:
                    lines.append(f"      {p.description}")
                if p.enum_values:
                    vals = ", ".join(p.enum_values)
                    lines.append(f"      Valores: {vals}")
                if p.default is not None:
                    lines.append(f"      Default: {p.default}")
            return "\n".join(lines)

**Compilador**:

    class ToolSchemaCompiler:
        def __init__(self, profile: CompilerProfile):
            self.profile = profile
            self._operators = self._load_operators(profile)

        def compile(self, tool: dict) -> CompiledToolSchema:
            for op in self._operators:
                tool = op.apply(tool)
            return self._to_compiled(tool)

**Perfiles**:

    @dataclass(frozen=True)
    class CompilerProfile:
        name: str
        operators: tuple[str, ...]

    CONSERVATIVE = CompilerProfile(
        name="conservative",
        operators=("sdm", "tas", "dro"),
    )
    BALANCED = CompilerProfile(
        name="balanced",
        operators=("sdm", "tas", "dro", "cfl", "cfo", "cas"),
    )
    AGGRESSIVE = CompilerProfile(
        name="aggressive",
        operators=(
            "sdm", "tas", "dro", "cfl", "cfo", "cas", "sadf", "ccp",
        ),
    )

**Configuración por modelo** en `HarnessConfig`:

    model_schema_profiles: dict[str, str] = {
        "gpt-oss:20b": "conservative",
        "qwen3:30b-a3b": "balanced",
        "*": "conservative",
    }

**Medición empírica** (obligatoria antes de fijar perfiles):
- 180-call sweep por modelo, ~$1 en la API de Ollama.
- Métricas: tool selection accuracy, param F1, value recall.
- Se corre **una vez por modelo**, se guarda en
  `~/.cache/chatperezoso/schema_profiles.json`.

**Integración con ToolRegistry**:

    class ToolRegistry:
        def __init__(self, compiler: ToolSchemaCompiler):
            self.compiler = compiler
            self._compiled: dict[str, CompiledToolSchema] = {}

        def register(self, name: str, schema: dict) -> None:
            self._compiled[name] = self.compiler.compile(schema)

        def render_for_prompt(self) -> str:
            return "\n\n".join(
                s.render() for s in self._compiled.values()
            )

**El harness ya no envía JSON al modelo.** Envía el texto
compilado. El modelo responde con un tool call en formato Ollama
normal; el harness lo valida contra el schema original.

### 5.5. Los 3 mecanismos de mejora

El paper identifica 3 mecanismos por los que TSCG mejora la
accuracy:

**M1: Format Translation** — convierte JSON a texto estructurado.
Dominante para modelos donde el parsing de JSON es el cuello de
botella.

**M2: Structural Reorganization** — CAS reordering + CFL + CFO.
Mejora accuracy, pero CFL y CFO se vuelven contraproducentes a ≥43
tools.

**M3: Combination** — efectos sinérgicos. CFL+CFO juntos dan
+17.5pp (super-aditivo).

**Lo crítico**: no es solo compresión. Es **reorganización
estructural que alinea el schema con cómo el modelo procesa la
información** (atención causal, sesgo de recencia, densidad
semántica).

### 5.6. Validación empírica por modelo

**Protocolo**:
1. Por cada modelo del catálogo, correr el 180-call sweep.
2. Guardar en cache: `{model: profile_name, accuracy: float}`.
3. Fijar el perfil en `HarnessConfig`.

**Si la accuracy cae con un perfil**: degradar al conservador.

**Si la accuracy sube**: subir un nivel (conservative → balanced →
aggressive).

**Re-medir** cuando:
- Se añade un modelo nuevo al catálogo.
- Se cambia la versión del modelo (Ollama actualiza pesos).
- El catálogo de tools supera 15 entradas.

### 5.7. Aceptación

| Métrica | Objetivo |
|---|---|
| Tool selection accuracy (gpt-oss:20b) | >85% con 10+ tools |
| Ahorro de tokens | >50% vs JSON baseline |
| Sin regresión de accuracy al compilar | Δ < 2% |
| Perfiles cacheados por modelo | Presentes para los 3 modelos activos |

---

*Continúa en §6 Verificación integrada + §7 Health monitoring.*

---

## 6. Verificación integrada

Esta capa resuelve el bucle verificar-reparar: cuando el modelo
escribe código, el verificador lo rechaza, el modelo "repara",
introduce más errores, y entra en ciclo. El paper VRR-Stop
documenta el fenómeno y da el criterio de parada.

### 6.1. El problema del bucle verificar-reparar

VRR-Stop (Wu et al., julio 2026):

> "Cuando tanto el verificador como el reparador son ruidosos, la
> reparación puede dañar planes ya correctos, y la aceptación
> reportada sigue subiendo mientras la validez real cae."

Los dos supuestos que fallan:
1. La reparación multi-ronda tiende a mejorar la calidad real.
2. La salida del verificador refleja la validez real.

**Cuando ambos fallan simultáneamente**: un plan inicialmente
válido puede ser falsamente rechazado por el verificador,
convertido en inválido por una reparación dañina, y finalmente
falsamente aceptado.

**Ejemplo del run #3**: el modelo escribió `gui.py` con F821
(usar `left_frame` antes de definirla). El verificador lo rechazó.
El modelo "reparó" reescribiendo. La reparación movió `left_frame`
a otro sitio pero introdujo 5 F821 nuevos. El verificador los
detectó. El modelo volvió a "reparar". Bucle.

### 6.2. VRR-Stop: criterio de parada robusto

**La solución**: en lugar de "reparar hasta que pase el
verificador" (bucle abierto), **estimar el signo de la ganancia
marginal verdadera** en cada ronda.

    @dataclass(frozen=True)
    class VRRConfig:
        max_rounds: int = 5
        min_verification_margin: float = 0.3   # VRR-Guard
        calibration_calls: int = 3

    class VRRStopCriterion:
        def __init__(self, config: VRRConfig):
            self.config = config
            self._history: list[VRRRound] = []

        def should_repair(
            self, current_round: VRRRound,
        ) -> RepairDecision:
            """Decide: COMMIT | REPAIR | STOP."""
            self._history.append(current_round)

            # Sin suficiente evidencia → REPAIR (conservador)
            if len(self._history) < 2:
                return RepairDecision.REPAIR

            # Estimar ganancia marginal
            gain = self._estimate_marginal_gain()

            # VRR-Guard: fallback cuando la calibración falla
            if self._verifier_discrimination_too_low():
                if (current_round.verification_margin
                        >= self.config.min_verification_margin):
                    return RepairDecision.COMMIT
                return RepairDecision.STOP

            # Decisión por signo de ganancia marginal
            if gain > 0:
                return RepairDecision.REPAIR
            return RepairDecision.COMMIT

**Resultado empírico** (GSM8K stress): VRR-Stop mejora la validez
verdadera final en 60.6 puntos porcentuales sobre reparación fija
de 5 rondas a un coste medio de 0.72 rondas.

**En cristiano**: en lugar de reparar 5 rondas siempre, VRR-Stop
para cuando la reparación ya no ayuda. Mejora la calidad final Y
gasta menos.

### 6.3. Implementación en ChatPerezoso

**Módulo**: `core/harness/vrr.py`

    @dataclass(frozen=True)
    class VRRRound:
        step_index: int
        issues_count: int
        issues_codes: list[str]     # ["F821", "F811"]
        repair_applied: bool
        verification_margin: float

    class RepairDecision(Enum):
        COMMIT = "commit"           # aceptar tal cual
        REPAIR = "repair"           # inyectar correctivo
        STOP = "stop"               # abortar el bucle de reparación

**VRR-Guard** (heurística simple primero):

    def _verifier_discrimination_too_low(self) -> bool:
        """Si el verificador no discrimina, usar VRR-Guard."""
        if len(self._history) < self.config.calibration_calls:
            return False
        # Si los issues_count oscilan sin converger, el verificador
        # no está discriminando bien.
        counts = [r.issues_count for r in self._history[-3:]]
        return max(counts) - min(counts) > 0

**Ganancia marginal**:

    def _estimate_marginal_gain(self) -> float:
        """Estima si reparar va a mejorar o empeorar."""
        prev = self._history[-2]
        curr = self._history[-1]
        # Si los issues aumentaron, reparar empeoró → gain < 0.
        return float(prev.issues_count - curr.issues_count)

### 6.4. Integración con el loop detector

Esta capa es complementaria a §3 (Loop Detection):

| Detector | Qué detecta | Cuándo actúa |
|---|---|---|
| Loop Detection | Repetición de patrones | Antes de la reparación |
| VRR-Stop | Bucle verificar-reparar sin convergencia | Durante la reparación |
| Verificación integrada | Issues de código | Después de cada escritura |

**Flujo integrado**:

    [ToolCallCompleted: escribir_archivo]
           │
           ▼
    [Verificador: analiza el archivo]
           │
           ├─ Sin issues → COMMIT → siguiente tool
           │
           └─ Con issues → VRRStopCriterion.should_repair()
                │
                ├─ REPAIR → inyecta prompt correctivo
                │           vuelve al modelo
                │
                ├─ COMMIT → acepta con issues, avisa al usuario
                │
                └─ STOP → aborta el bucle de reparación
                           emite HarnessWarning con diagnóstico

### 6.5. Qué NO es bucle de reparación

Igual que el loop detector, hay que evitar falsos positivos:

1. **Reparación con progreso**: ronda 1 con 5 F821 → ronda 2 con
   2 F821 → ronda 3 con 0. Cada ronda reduce issues → REPAIR.
2. **Reparación de errores distintos**: ronda 1 con F821 → ronda 2
   con F811. Los códigos cambian → REPAIR (son issues nuevos).
3. **Error transitorio del verificador**: si el verificador falla
   (no el código), no cuenta como ronda.

**Regla**: cada `VRRRound` tiene que tener el mismo o mayor
`issues_count` que la ronda anterior **con los mismos códigos**
para contar como bucle.

### 6.6. Aceptación

| Métrica | Objetivo |
|---|---|
| Rondas de reparación por issue | <2 (vs 5+ sin criterio) |
| Falsos positivos (commit de código roto) | 0 |
| Detección de bucle verificar-reparar | 100% de los casos del run #3 |
| OVERPAPER run #5 F6 | No entra en bucle |

---

## 7. Health monitoring

El loop detection de §3 caza repeticiones. Pero hay **tres modos de
fallo adicionales** que no son repeticiones y que el loop detector
no ve. Agent Vitals los documenta y los resuelve con 4 métricas
por step.

### 7.1. Los 4 modos de fallo

| Señal | Qué detecta | Métrica | Umbral |
|---|---|---|---|
| Loop | Agente repitiendo sin progreso | findings_count plateau | 3 steps sin cambio |
| Stuck | Cobertura estancada | coverage_score baja + baja varianza | 5 steps ratio < 0.05 |
| Thrash | Errores en cascada | error_count por step | > 3 errores/step |
| Runaway Cost | Tokens sin output | tokens sube, findings plano | > 5000 tokens sin finding |

**Diferencia crítica con loop detector**:

- **Loop**: "El agente hace lo mismo una y otra vez."
- **Stuck**: "El agente hace cosas distintas pero no avanza."
- **Thrash**: "El agente comete errores en cascada."
- **Runaway Cost**: "El agente consume tokens sin producir nada."

**Un agente puede estar stuck sin estar en loop.** Hace
`leer_archivo(f1)`, `leer_archivo(f2)`, `grep(x, f1)`,
`grep(y, f2)` — todas distintas, todas legítimas, pero ninguna
acerca al objetivo.

### 7.2. Implementación

**Módulo**: `core/harness/health.py`

    @dataclass(frozen=True)
    class HealthPolicy:
        enabled: bool = True
        loop_window: int = 3
        stuck_window: int = 5
        stuck_coverage_threshold: float = 0.05
        thrash_error_threshold: int = 3
        runaway_token_threshold: int = 5000
        on_loop: str = "corrective"
        on_stuck: str = "corrective"
        on_thrash: str = "confirm"
        on_runaway: str = "warn"

    class HealthMonitor:
        def __init__(self, policy: HealthPolicy):
            self.policy = policy
            self._history: list[VitalsSnapshot] = []

        def step(
            self,
            *,
            findings_count: int,
            coverage_score: float,
            total_tokens: int,
            error_count: int,
        ) -> HealthSnapshot:
            snapshot = VitalsSnapshot(
                step_index=len(self._history),
                findings_count=findings_count,
                coverage_score=coverage_score,
                total_tokens=total_tokens,
                error_count=error_count,
            )
            self._history.append(snapshot)

            signals: list[str] = []
            if self._detect_loop(snapshot):
                signals.append("loop")
            if self._detect_stuck(snapshot):
                signals.append("stuck")
            if self._detect_thrash(snapshot):
                signals.append("thrash")
            if self._detect_runaway(snapshot):
                signals.append("runaway_cost")

            return HealthSnapshot(
                step_index=snapshot.step_index,
                signals=signals,
                metrics={...},
            )

### 7.3. Los 4 detectores

**Loop**: findings_count en plateau

    def _detect_loop(self, current) -> bool:
        if len(self._history) < self.policy.loop_window:
            return False
        window = self._history[-self.policy.loop_window:]
        return len({s.findings_count for s in window}) == 1

**Stuck**: cobertura sin subir

    def _detect_stuck(self, current) -> bool:
        if len(self._history) < self.policy.stuck_window:
            return False
        window = self._history[-self.policy.stuck_window:]
        peak = max(s.coverage_score for s in window)
        return peak < self.policy.stuck_coverage_threshold

**Thrash**: errores excesivos

    def _detect_thrash(self, current) -> bool:
        return current.error_count >= self.policy.thrash_error_threshold

**Runaway Cost**: tokens sin output

    def _detect_runaway(self, current) -> bool:
        if len(self._history) < 2:
            return False
        prev = self._history[-2]
        tokens_delta = current.total_tokens - prev.total_tokens
        findings_delta = current.findings_count - prev.findings_count
        return (
            tokens_delta > self.policy.runaway_token_threshold
            and findings_delta == 0
        )

### 7.4. Integración con el harness

Tras cada `StepEnded`, el harness llama `health_monitor.step()` con
las 4 métricas. Si hay señales, decide:

| Señal | Acción |
|---|---|
| loop | Ya cubierto por §3. Health solo confirma. |
| stuck | Inyecta prompt de "cambio de estrategia" |
| thrash | Pausa el run. ConfirmationRequested genérico. |
| runaway_cost | Warning al usuario. No aborta. |

**De dónde salen las 4 métricas**:

- **findings_count**: issues **distintos** que el verificador
  detecta (si el mismo F821 reaparece, no cuenta).
- **coverage_score**: el harness parsea el output del modelo
  buscando `"FASE N VERIFICADA"`. Ratio = fases / total.
- **total_tokens**: `ModelClient` reporta tokens de cada llamada.
- **error_count**: tool calls con `status == "error"` en el step.

**Persistencia**: cada `HealthSnapshot` se emite como evento y se
guarda en el event log. Al reanudar, el monitor reconstruye su
historial desde los eventos.

### 7.5. Content-based loop detection

Agent Vitals v1.5 añade detección por similitud de contenido:

> "When you pass output_text to monitor.step(), Agent Vitals
> computes content-level similarity to distinguish loops from
> stuck states. High similarity (≥0.85): Confirms loop. Low
> similarity with stagnant coverage: Confirms stuck."

**Útil para ChatPerezoso**: si el modelo repite la misma
explicación en prosa (sin tool calls), el loop detector de firmas
no lo ve (no hay tool call). Pero la similitud de texto sí.

**Implementación**: Jaccard similarity entre outputs consecutivos
del modelo.

    def _jaccard(self, a: str, b: str) -> float:
        sa = set(a.lower().split())
        sb = set(b.lower().split())
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / len(sa | sb)

**Umbral**: > 0.85 → confirma loop. < 0.3 con coverage estancada →
confirma stuck.

### 7.6. Aceptación

| Métrica | Objetivo |
|---|---|
| Detección de stuck sin loop | 100% en test sintético |
| Detección de thrash | 100% con > 3 errores/step |
| Detección de runaway | 100% con > 5000 tokens sin finding |
| Falsos positivos | < 5% en runs normales |
| Overhead por step | < 5 ms |

---

*Continúa en §8 Completion verification + §9 Arquitectura de migración.*

---

## 8. Completion verification

El problema documentado por Anthropic: **el modelo dice "FASE
VERIFICADA" sin haber verificado nada**. El completion verification
resuelve esto.

### 8.1. El problema

`agent-execution-harness` lo documenta:

> "Do not trust 'done' unless the agent gives evidence from the
> harness artifact. A strong final answer should include: run_id,
> artifact path, final status, evidence, tests or gates executed,
> verified claims, rollback notes when relevant."

**Traducción para ChatPerezoso**: cuando el modelo dice "FASE 5
VERIFICADA", el harness **no lo acepta por fe**. Verifica:

1. ¿Ejecutó las tools requeridas para esa fase?
2. ¿La verificación real pasó?
3. ¿El workspace está en el estado esperado?

### 8.2. El patrón: evidencia verificable

    [Modelo dice "FASE 5 VERIFICADA"]
           │
           ▼
    [CompletionVerifier.verify(phase=5, ...)]
           │
           ├─ verified → StepEnded(outcome="ok")
           │
           ├─ incomplete → LoopCorrectivePrompt:
           │    "Dijiste FASE 5 VERIFICADA pero no ejecutaste
           │     `python -m py_compile gui.py`. Ejecútala."
           │
           ├─ unverified → LoopCorrectivePrompt:
           │    "Dijiste FASE 5 VERIFICADA pero no hay
           │     verificación. Ejecuta la verificación real."
           │
           └─ verification_failed → LoopCorrectivePrompt con
                issues exactos

### 8.3. Implementación

**Módulo**: `core/harness/completion.py`

    @dataclass(frozen=True)
    class CompletionPolicy:
        require_tool_execution: bool = True
        require_verification_pass: bool = True
        require_evidence: bool = True
        max_auto_continuations: int = 3

    @dataclass(frozen=True)
    class PhaseSpec:
        """Especificación de una fase (parseada del prompt)."""
        index: int
        name: str
        required_tools: list[str]      # ej. ["escribir_archivo",
                                        #      "ejecutar_comando"]
        expected_files: list[str]      # ej. ["gui.py"]
        verification_command: str      # ej. "python -m py_compile gui.py"

    class CompletionVerifier:
        def __init__(self, policy: CompletionPolicy):
            self.policy = policy

        def verify(
            self,
            phase: PhaseSpec,
            tool_calls: list[ToolCall],
            tool_results: list[ToolResult],
            workspace_state: WorkspaceState,
        ) -> CompletionResult:
            if self.policy.require_tool_execution:
                executed = {t.name for t in tool_calls}
                missing = set(phase.required_tools) - executed
                if missing:
                    return CompletionResult(
                        status="incomplete",
                        missing_tools=list(missing),
                        message=f"No ejecutó: {missing}",
                    )

            if self.policy.require_verification_pass:
                verification = self._find_verification(tool_results)
                if verification is None:
                    return CompletionResult(
                        status="unverified",
                        message="No hay verificación ejecutada.",
                    )
                if verification.issues:
                    return CompletionResult(
                        status="verification_failed",
                        issues=verification.issues,
                    )

            if self.policy.require_evidence:
                for f in phase.expected_files:
                    if not workspace_state.has_file(f):
                        return CompletionResult(
                            status="missing_artifact",
                            message=f"Falta {f} en el workspace.",
                        )

            return CompletionResult(status="verified")

### 8.4. Parseo del prompt

**El `PhaseSpec` se parsea del prompt** (no se configura a mano).
El formato esperado:

    ━━━ VERIFICACIÓN FASE N ━━━
    python -m py_compile gui.py

**Parser**:

    _PHASE_HEADER = re.compile(
        r"FASE (\d+) — (.+?)\n", re.MULTILINE,
    )
    _VERIFICATION = re.compile(
        r"━━━ VERIFICACIÓN FASE \d+ ━━━\n(.+?)\n", re.MULTILINE,
    )

    def parse_phases(prompt: str) -> list[PhaseSpec]:
        """Extrae fases del prompt OVERPAPER."""
        phases = []
        for match in _PHASE_HEADER.finditer(prompt):
            index = int(match.group(1))
            name = match.group(2).strip()
            # Verificación de esta fase
            verification = self._extract_verification(prompt, index)
            phases.append(PhaseSpec(
                index=index,
                name=name,
                required_tools=self._infer_required_tools(match),
                expected_files=self._infer_expected_files(match),
                verification_command=verification,
            ))
        return phases

**Fallback**: si el prompt no sigue el formato, `PhaseSpec` se
construye con valores por defecto conservadores.

### 8.5. Auto-continuation

Anthropic documenta el patrón complementario:

> "Si al final del turno la checklist tiene ítems pendientes y no
> hay bloqueo, enviar mensaje automático para continuar. Máximo
> 2-3 auto-continuations."

**Implementación**:

    class AutoContinuation:
        def __init__(self, policy: CompletionPolicy):
            self.policy = policy
            self._count = 0

        def should_continue(
            self,
            phase: PhaseSpec,
            last_result: CompletionResult,
        ) -> bool:
            if last_result.status == "verified":
                self._count = 0
                return False
            if self._count >= self.policy.max_auto_continuations:
                return False  # forzar revisión humana
            self._count += 1
            return True

        def build_continuation_prompt(
            self,
            phase: PhaseSpec,
            result: CompletionResult,
        ) -> str:
            return (
                f"[Harness · Auto-continuación]\n\n"
                f"Fase {phase.index} incompleta:\n"
                f"{result.message}\n\n"
                f"Completa lo que falta y declara la fase como "
                f"VERIFICADA con evidencia real."
            )

### 8.6. Aceptación

| Métrica | Objetivo |
|---|---|
| Detección de "FASE VERIFICADA" sin verificar | 100% |
| Detección de tools requeridas faltantes | 100% |
| Auto-continuations antes de humano | ≤ 3 |
| Falsos positivos (rechazar fase válida) | 0% |

---

## 9. Arquitectura de migración

### 9.1. Principio: strangler pattern

No es big-bang. El harness **envuelve** el código existente, y se
migra por slices.

> "The safest migration strategy is to wrap the existing code
> behind a harness layer, then gradually replace components from
> behind that layer. This approach: never requires a full
> production freeze, allows rollback at any point, lets you
> validate each change against your evaluation suite, keeps the
> system working throughout."

**Aplicado a ChatPerezoso**:

    Fase 1: Harness existe, UI habla con workers.py como siempre.
             workers.py INTERNAMENTE usa el harness para loop/
             durable/health.

    Fase 2: Harness reemplaza el ciclo de workers.py.
             workers.py se convierte en adaptador fino.

    Fase 3: UI habla directamente con el harness.
             workers.py se borra.

**Cada fase es deployable. Cada fase tiene rollback.**

### 9.2. Orden de migración

| Slice | Qué se migra | Rollback | Tests que migran |
|---|---|---|---|
| S0 | `core/harness/` creado, vacío, tests propios | Eliminar carpeta | Ninguno |
| S1 | `loop.py` + `health.py` en `workers.py` | Revertir import + llamada | Tests de loop/health |
| S2 | `durable.py` (event log + checkpoints) | `durable_enabled=False` | Tests de durable |
| S3 | `schemas.py` (tool compilation) | `schema_compilation_enabled=False` | Tests de schemas |
| S4 | `session.py` (ciclo completo) | `workers.py` vuelve a su ciclo | Tests de session |
| S5 | `completion.py` (completitud) | `completion_verification_enabled=False` | Tests de completion |
| S6 | `ui/controllers/` habla con harness | Revertir a `workers.py` | Tests de UI |
| S7 | Borrar `workers.py` y tests huérfanos | Restaurar desde git | — |

**Lo crítico**: cada slice es **opt-in por flag**.

    @dataclass(frozen=True)
    class HarnessConfig:
        loop_detection_enabled: bool = True
        health_monitoring_enabled: bool = True
        durable_enabled: bool = False
        schema_compilation_enabled: bool = False
        completion_verification_enabled: bool = False

**Al principio, todo OFF menos loop y health.**

### 9.3. Qué se borra (y cuándo)

| Pieza | Cuándo se borra | Reemplazada por |
|---|---|---|
| Loop detection en `workers.py` | S1 | `core/harness/loop.py` |
| `_VERIFY_AFTER` y `_maybe_verify` | S1 | `core/harness/policy.py` |
| `_request_confirmation` (lógica) | S4 | `core/harness/session.py` |
| Ciclo de agente en `_call_tool` | S4 | `core/harness/session.py` |
| `workers.py` (fichero) | S7 | `core/harness/` |
| `test_workers_allowlist.py` | S7 | `test_harness_loop.py` |
| `test_auto_approve_shell.py` | S7 | `test_harness_policy.py` |
| `test_workers_verificador_insert.py` | S7 | `test_harness_verification.py` |

**Los tests actuales se quedan hasta S7.** Son la red de seguridad.
En S7, se reescriben contra el harness.

### 9.4. Qué NO se toca

- `ui/views/`, `ui/theme*` — intactos.
- `plugins/` — intactos (el harness los ve como tools).
- `core/config.py`, `agents.py`, `history.py` — intactos.
- `core/workspace.py` — intacto.
- `core/ast_index.py`, `rag_index.py` — intactos (WAL añadido).
- `core/ollama.py` — intacto (log a stderr añadido).
- `core/tools.py` — refactor menor para schema compilation (S3).
- `scripts/` — intactos.

### 9.5. Mapa de ficheros final

    core/harness/
    ├── __init__.py
    ├── session.py          # Orquestador principal
    ├── loop.py             # Detección multi-patrón
    ├── durable.py          # Checkpoints + resume
    ├── schemas.py          # Tool schema compilation
    ├── health.py           # 4 métricas
    ├── events.py           # Event sourcing
    ├── policy.py           # Autopilot, allowlist, confirmaciones
    ├── completion.py       # Verificación de completitud
    └── vrr.py              # VRR-Stop (verificar-reparar)

    tests/harness/
    ├── test_loop.py
    ├── test_durable.py
    ├── test_schemas.py
    ├── test_health.py
    ├── test_events.py
    ├── test_policy.py
    ├── test_completion.py
    ├── test_vrr.py
    └── test_session.py

### 9.6. Ficheros que se borran en S7

    ui/workers.py                   ← reemplazado por harness/session.py
    ui/autopilot_prompt.py          ← movido a harness/policy.py
    tests/test_workers_allowlist.py
    tests/test_auto_approve_shell.py
    tests/test_workers_verificador_insert.py
    tests/test_chat_worker_signals.py
    tests/test_chat_worker_summary.py

**Tests que se quedan** (~1400 de 1645):
- Todo lo que testea `plugins/`, `ui/views/`, `core/` (fuera de
  workers), `scripts/`.

### 9.7. Aceptación

| Métrica | Objetivo |
|---|---|
| Cero regresiones en cada slice | 100% de tests pasando |
| Rollback por flag funciona | Sí para S2, S3, S5 |
| Migración completa sin romper `main` | Sí |
| Tests huérfanos borrados en S7 | 5 ficheros |

---

*Continúa en §10 Plan de implementación + §11 Criterios de aceptación.*

---

## 10. Plan de implementación

7 slices. Cada una es deployable, reversible, y con tests propios.

### 10.1. S0 — Estructura vacía (30 min)

**Qué**: crear `core/harness/` con `__init__.py` y los stubs de
todos los módulos. Sin lógica.

**Entregable**:
- `core/harness/__init__.py`
- `core/harness/events.py` (solo dataclasses)
- `core/harness/policy.py` (solo dataclasses de config)
- `tests/harness/__init__.py`

**Tests**: import limpio.

**Rollback**: `rm -rf core/harness/ tests/harness/`.

**Criterio**: `pytest -q tests/harness/` pasa (0 tests).

### 10.2. S1 — Loop detection + health (1-2 sesiones)

**Qué**: implementar `loop.py` + `health.py`. Integrarlos en
`workers.py` con flag `loop_detection_enabled=True`.

**Entregable**:
- `core/harness/loop.py` (4 detectores + escalation)
- `core/harness/health.py` (4 métricas)
- `tests/harness/test_loop.py` (30+ casos)
- `tests/harness/test_health.py` (15+ casos)
- Integración en `ui/workers.py` (llamada tras cada tool call)

**Impacto**: run OVERPAPER #4 debe romper bucles en 2-3 intentos.

**Rollback**: `loop_detection_enabled=False` en config.

**Criterio**: 45+ tests nuevos pasan. Suite completa 1700+.

### 10.3. S2 — Durable execution (2 sesiones)

**Qué**: event log SQLite + checkpoints + resume.

**Entregable**:
- `core/harness/events.py` (log + fold)
- `core/harness/durable.py` (checkpoints + idempotencia)
- `tests/harness/test_durable.py` (20+ casos)
- Flag `durable_enabled=False` por defecto

**Impacto**: crash en step 5 → resume recupera steps 1–4.

**Rollback**: `durable_enabled=False`.

**Criterio**: test de crash simulado pasa. WAL no bloquea.

### 10.4. S3 — Tool schema compilation (1 sesión)

**Qué**: compilador TSCG-style con 3 perfiles.

**Entregable**:
- `core/harness/schemas.py`
- `tests/harness/test_schemas.py` (15+ casos)
- Flag `schema_compilation_enabled=False`
- Script de sweep: `scripts/schema_sweep.py`

**Impacto**: tool selection accuracy >85% con 10+ tools.

**Rollback**: `schema_compilation_enabled=False`.

**Criterio**: sweep ejecutado para gpt-oss:20b y qwen3:30b-a3b.
Perfiles cacheados.

### 10.5. S4 — Session (2 sesiones)

**Qué**: `session.py` completo. `workers.py` pasa a adaptador.

**Entregable**:
- `core/harness/session.py` (orquestador)
- `core/harness/policy.py` (autopilot + allowlist)
- `tests/harness/test_session.py` (25+ casos)
- `ui/workers.py` refactorizado

**Impacto**: la UI consume eventos, no señales Qt dispersas.

**Rollback**: revertir `workers.py`. Harness sigue accesible.

**Criterio**: run OVERPAPER #5 llega a F20+.

### 10.6. S5 — VRR-Stop + completion (1 sesión)

**Qué**: VRR-Stop + verificación de completitud.

**Entregable**:
- `core/harness/vrr.py`
- `core/harness/completion.py`
- `tests/harness/test_vrr.py` (10+ casos)
- `tests/harness/test_completion.py` (15+ casos)

**Impacto**: F6 del run #3 ya no entra en bucle.

**Rollback**: `completion_verification_enabled=False`.

**Criterio**: test de "FASE VERIFICADA" sin verificar detectado.

### 10.7. S6 — Migración de UI (1 sesión)

**Qué**: `ui/controllers/` habla con `HarnessSession` en vez de
`ChatWorker`.

**Entregable**:
- `ui/controllers/chat_controller.py` refactorizado
- `ui/harness_bridge.py` (adaptador Qt ← HarnessSession)
- Tests de UI actualizados

**Impacto**: la UI ya no conoce `workers.py`.

**Rollback**: revertir a `workers.py`.

**Criterio**: toda la suite pasa. Run OVERPAPER #6 completo.

### 10.8. S7 — Limpieza (1 sesión)

**Qué**: borrar `workers.py` + tests huérfanos.

**Entregable**:
- `git rm ui/workers.py ui/autopilot_prompt.py`
- `git rm tests/test_workers_allowlist.py` + 4 más
- Tests reescritos contra harness donde falten

**Impacto**: código limpio, un solo ciclo de agente.

**Rollback**: `git revert`.

**Criterio**: suite 1500+ sin workers.

---

## 11. Criterios de aceptación del harness v3

El harness está terminado cuando **todos** los siguientes son
ciertos:

### 11.1. Funcionales

| # | Criterio | Verificación |
|---|---|---|
| F1 | run OVERPAPER completa 25/25 fases sin intervención | Run #6 |
| F2 | Loop detection rompe bucles en ≤ 3 intentos | Test sintético + Run #6 |
| F3 | Crash en F13 recupera F1–F12 al reanudar | Test de crash simulado |
| F4 | Tool selection accuracy >85% con 10+ tools | Schema sweep |
| F5 | VRR-Stop evita bucle verificar-reparar | Run #6 F6 |
| F6 | Completion verification detecta "FASE VERIFICADA" falsa | Test + Run #6 |
| F7 | Health monitoring detecta stuck sin loop | Test sintético |
| F8 | Resume tras crash < 5 segundos | Test de timing |

### 11.2. No-funcionales

| # | Criterio | Verificación |
|---|---|---|
| N1 | Overhead del harness < 50 ms por step | Benchmark |
| N2 | SQLite WAL soporta 2 conexiones concurrentes | Test de concurrencia |
| N3 | Retención limita checkpoints a 3 por run | Test |
| N4 | Event log nunca pierde un evento (WAL + fsync) | Test de crash |
| N5 | Cero regresiones en tests existentes | Suite 100% |

### 11.3. De migración

| # | Criterio | Verificación |
|---|---|---|
| M1 | Cada slice tiene rollback por flag | Tests de flag |
| M2 | `main` sigue funcionando tras cada slice | Smoke test |
| M3 | Tests huérfanos borrados en S7 | `git rm` verificado |
| M4 | Documentación actualizada | `docs/harness-v3.md` |

### 11.4. El criterio maestro

> **Un run OVERPAPER de 25 fases, lanzado con qwen3:30b-a3b y
> agente "Programador", sobre workspace limpio, debe completarse
> sin intervención manual.**

Si ese run termina con `"PROYECTO COMPLETADO"` sin que el usuario
haya tenido que matar bucles, aprobar confirmaciones de más, o
reanudar tras un crash, el harness está listo.

---

## Anexos

### A. Referencias técnicas

| Tema | Fuente |
|---|---|
| Loop detection multi-patrón | OpenClaw `tools.loopDetection` |
| Inyección correctiva | HuggingFace smolagents `doom_loop.py` |
| Normalización de firmas | smolagents (canonical JSON) |
| Health monitoring 4 señales | agent-vitals |
| Circuit breaker per-tool | agent-guard-mcp |
| Durable execution event sourcing | IEEE "Durable Execution for AI Agents" (2026) |
| Checkpoints por super-step | LangGraph checkpointers |
| Idempotencia por clave | durable-agents (PyPI) |
| Progress markers vs checkpoints | Azure Durable Task |
| Tool schema compilation | TSCG (Sakizli, abril 2026) |
| VRR-Stop | Wu et al., julio 2026 |
| Completion verification | Anthropic Claude Opus 5.5 (2026) |
| Strangler pattern | Martin Fowler (2004) |
| Agent architecture patterns | Google Cloud AI Agent Bake-Off (2026) |

### B. Papers citados

- **TSCG: Deterministic Tool-Schema Compilation for Agentic LLM
  Deployments** — Sakizli, abril 2026. arXiv/Zenodo.
- **VRR-Stop: Verifier-Repair-Repeat with Stopping Criterion** —
  Wu et al., julio 2026. arXiv.
- **Durable Execution for AI Agents: A Design Pattern for
  Fault-Tolerant Agent Loops** — IEEE Xplore, agosto 2026.
- **When Agents Do Not Stop: Uncovering Infinite Agentic Loops in
  LLM Agents** — arXiv:2607.01641, julio 2026.
- **PALADIN: Failure Recovery for Tool-Using Agents** —
  septiembre 2025.
- **VIGIL: Runtime Reflexive Agent Framework** — diciembre 2025.

### C. Glosario

- **Event sourcing**: patrón donde el estado se reconstruye desde
  un log de eventos inmutables.
- **Strangler pattern**: migración incremental donde el nuevo
  código envuelve al viejo y lo reemplaza por partes.
- **Checkpoint**: foto del estado mínimo que permite reanudar sin
  replay completo.
- **Idempotencia**: propiedad de una operación que se puede
  ejecutar N veces sin cambiar el resultado.
- **Loop detection**: detección de patrones repetitivos en la
  trayectoria del agente.
- **Health monitoring**: detección de modos de fallo que no son
  bucles (stuck, thrash, runaway).
- **Schema compilation**: transformación de JSON schema a texto
  token-efficiente para modelos pequeños.
- **VRR-Stop**: criterio de parada para el bucle
  verificar-reparar.
- **Completion verification**: verificar que el modelo realmente
  completó lo que dice haber completado.

### D. Decisiones abiertas para la implementación

Estas decisiones se tomarán **durante** la construcción de cada
slice, no antes:

1. **S1**: ¿los umbrales del loop detector son exactamente los del
   paper o ajustamos tras ver datos del run #4?
2. **S2**: ¿checkpoint cada step o cada 2 steps? (medir overhead)
3. **S3**: ¿qué perfil por modelo, medido con sweep o empírico?
4. **S4**: ¿`HarnessSession` expone eventos en batch o streaming?
5. **S6**: ¿la UI consume eventos directamente o vía un
   `HarnessBridge` con señales Qt?

### E. Métricas de seguimiento

Durante la implementación, se registran:

- Tests totales por slice.
- Líneas de código por módulo del harness.
- Tiempo de overhead por step.
- Runs OVERPAPER completados con éxito.
- Rondas de reparación por issue (VVR-Stop).
- Correctivos inyectados por run (loop detection).

Estas métricas viven en `~/.cache/chatperezoso/harness_metrics.json`.

---

## Fin del diseño

El documento cierra aquí. **Próximo paso**: empezar por S0.

