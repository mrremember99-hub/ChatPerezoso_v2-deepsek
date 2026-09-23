"""Persistencia de la conversación actual.

Guarda el historial de mensajes en un JSON junto a ``config.json``. El
formato es deliberadamente simple: una lista de mensajes con el mismo
esquema que usa ``OllamaClient`` (role, content) más metadatos ligeros
(modelo y workspace en el momento de guardar).

No hay múltiples conversaciones ni nombres. La idea es recordar dónde se
quedó el usuario la última vez. Cuando quiera empezar de cero, "Nueva
conversación" borra el archivo.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
HISTORY_FILE = BASE_DIR / "history.json"

# Límite defensivo: si un archivo corrupto contiene más mensajes de la
# cuenta, no lo cargamos entero.
_MAX_MESSAGES = 200


@dataclass
class History:
    messages: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    workspace: str = ""
    saved_at: str = ""


class HistoryStore:
    def __init__(self, path: Path = HISTORY_FILE):
        self.path = path

    # -- API pública ---------------------------------------------------------

    def load(self) -> History | None:
        """Devuelve la conversación guardada o None si no hay o está corrupta."""
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None

        raw_messages = data.get("messages")
        if not isinstance(raw_messages, list):
            return None

        messages: list[dict[str, Any]] = []
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if role not in {"user", "assistant"}:
                continue
            if not isinstance(content, str):
                continue
            messages.append({"role": role, "content": content})
            if len(messages) >= _MAX_MESSAGES:
                break

        if not messages:
            return None

        return History(
            messages=messages,
            model=str(data.get("model", "")),
            workspace=str(data.get("workspace", "")),
            saved_at=str(data.get("saved_at", "")),
        )

    def save(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str = "",
        workspace: str = "",
    ) -> None:
        """Escribe la conversación actual. Silencioso si falla el disco."""
        # Solo guardamos user/assistant. Los mensajes tool y los system
        # prompts internos no forman parte de la conversación visible.
        serializable = [
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if m.get("role") in {"user", "assistant"}
            and isinstance(m.get("content"), str)
        ]
        # Mismo limite que en load(). Sin esto, una sesion larga
        # guardaba todos los mensajes en disco, y al recargar solo
        # se veian los ultimos 200. El resto se perdia en silencio.
        serializable = serializable[-_MAX_MESSAGES:]
        payload = {
            "messages": serializable,
            "model": model,
            "workspace": workspace,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Escritura atómica: escribir a .tmp y renombrar. Si el
            # proceso muere a mitad, history.json queda intacto (el
            # .tmp se ignora y el replace() es atómico en POSIX).
            tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(self.path)
        except OSError:
            # La persistencia es una comodidad, no un requisito. Si el
            # disco falla, la app sigue funcionando sin recordar nada.
            pass

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


# ── Escritura asíncrona ──────────────────────────────────────────────

from concurrent.futures import ThreadPoolExecutor


class AsyncHistoryWriter:
    """Escribe el historial a disco fuera del hilo de UI.

    `HistoryStore.save()` hace json.dumps + write_text + replace.
    Para historiales de varios MB eso son decenas o cientos de ms
    de trabajo síncrono en el hilo llamante. Aquí lo mandamos a un
    ThreadPoolExecutor de 1 worker:

      · Un solo worker → las escrituras se serializan en el orden
        en que llegan. Nunca pueden quedar fuera de orden.
      · Snapshot inmutable → el thread no ve self.messages mutando.
      · shutdown() → drain del executor al cerrar la app.

    No notifica errores al llamante: la persistencia es una
    comodidad, no un requisito (ver HistoryStore.save).
    """

    def __init__(self, store: HistoryStore) -> None:
        self._store = store
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="HistoryWriter",
        )

    def submit(
        self,
        messages: list[dict],
        *,
        model: str = "",
        workspace: str = "",
    ) -> None:
        """Encola la escritura. No bloquea el hilo que llama."""
        # Snapshot inmutable, cap a _MAX_MESSAGES, solo roles válidos.
        # El thread de persistencia nunca debe leer `messages`
        # original, que puede estar mutando en el hilo UI.
        snapshot = [
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if m.get("role") in {"user", "assistant"}
            and isinstance(m.get("content"), str)
        ][-_MAX_MESSAGES:]
        self._executor.submit(
            self._store.save,
            snapshot,
            model=model,
            workspace=workspace,
        )

    def flush(self, timeout: float = 3.0) -> None:
        """Espera a que las escrituras encoladas terminen.

        No cierra el executor: se puede seguir usando después. Se
        implementa encolando una tarea noop que, al ejecutarse,
        garantiza que todo lo anterior se completó (FIFO, un solo
        worker).
        """
        self._executor.submit(lambda: None).result(timeout=timeout)

    def shutdown(self, timeout: float = 3.0) -> bool:
        """Espera a que terminen las escrituras pendientes, con timeout.

        Devuelve True si todo termino dentro del timeout, False si
        quedo alguna escritura en vuelo. En el segundo caso no
        matamos el hilo (Python no lo permite): el executor se
        cierra con wait=False y el proceso lo terminara al salir.

        Antes este metodo aceptaba `timeout` pero lo ignoraba:
        `executor.shutdown(wait=True)` bloqueaba indefinidamente.
        """
        try:
            self.flush(timeout=timeout)
        except Exception:
            # Timeout al drenar la cola. Cerrar sin esperar.
            self._executor.shutdown(wait=False, cancel_futures=False)
            return False
        # Cola vacia: cerrar es inmediato.
        self._executor.shutdown(wait=False, cancel_futures=False)
        return True