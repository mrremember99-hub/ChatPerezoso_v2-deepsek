"""Configuración manual por modelo: overrides del modo de tool calling.

Permite forzar el modo cuando la detección automática vía /api/show da
un resultado incorrecto (Ollama puede mentir sobre las capacidades, o
un modelo puede tener un bug en el template de tools).

Formato de models.json:
    {
      "overrides": {
        "deepseek-r1:latest": {
          "mode": "xml",
          "note": "no emite tool_calls nativos"
        },
        "llama3.1:latest": {
          "mode": "native"
        }
      }
    }

Valores válidos de "mode":
  · "native" — fuerza tool calling nativo (parámetro tools + tool_calls)
  · "xml"    — fuerza prompt-guided XML (bloque <tool_call>)
  · "auto"   — delega en /api/show (comportamiento por defecto)

Si un modelo no aparece en models.json, se usa "auto".
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path


logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_FILE = BASE_DIR / "models.json"

VALID_MODES = frozenset({"native", "xml", "auto"})


@dataclass(frozen=True)
class ModelOverride:
    mode: str  # "native" | "xml" | "auto"
    note: str = ""

    def is_forced(self) -> bool:
        return self.mode in ("native", "xml")


class ModelsConfig:
    def __init__(self, path: Path = MODELS_FILE):
        self.path = path
        self._overrides: dict[str, ModelOverride] = {}
        self._loaded = False

    # -- API pública ---------------------------------------------------------

    def get(self, model: str) -> ModelOverride:
        self._ensure_loaded()
        return self._overrides.get(model, ModelOverride(mode="auto"))

    def set(self, model: str, mode: str, note: str = "") -> None:
        if mode not in VALID_MODES:
            raise ValueError(
                f"Modo inválido: {mode!r}. Debe ser uno de {sorted(VALID_MODES)}"
            )
        self._ensure_loaded()
        if mode == "auto" and not note:
            # "auto" sin nota es el default: no lo guardamos, así el
            # archivo solo contiene lo que difiere de la detección.
            self._overrides.pop(model, None)
        else:
            self._overrides[model] = ModelOverride(mode=mode, note=note)
        self._save()

    def remove(self, model: str) -> None:
        self._ensure_loaded()
        if model in self._overrides:
            del self._overrides[model]
            self._save()

    def all(self) -> dict[str, ModelOverride]:
        self._ensure_loaded()
        return dict(self._overrides)

    # -- persistencia --------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._overrides = self._load()

    def _load(self) -> dict[str, ModelOverride]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("models.json ilegible: %s", exc)
            return {}
        if not isinstance(data, dict):
            return {}
        raw = data.get("overrides")
        if not isinstance(raw, dict):
            return {}

        result: dict[str, ModelOverride] = {}
        for name, spec in raw.items():
            if not isinstance(name, str) or not isinstance(spec, dict):
                continue
            mode = str(spec.get("mode", "auto")).lower()
            if mode not in VALID_MODES:
                logger.warning(
                    "Modo inválido %r para %s, se ignora", mode, name
                )
                continue
            note = str(spec.get("note", ""))
            result[name] = ModelOverride(mode=mode, note=note)
        return result

    def _save(self) -> None:
        payload: dict = {"overrides": {}}
        for name, override in sorted(self._overrides.items()):
            entry: dict = {"mode": override.mode}
            if override.note:
                entry["note"] = override.note
            payload["overrides"][name] = entry

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("No se pudo escribir %s: %s", self.path, exc)


# Instancia global (singleton ligero).
_default = ModelsConfig()


def get_override(model: str) -> ModelOverride:
    return _default.get(model)


def set_override(model: str, mode: str, note: str = "") -> None:
    _default.set(model, mode, note)


def reload() -> None:
    """Fuerza recargar el archivo desde disco. Útil tras editarlo a mano."""
    _default._loaded = False
    _default._ensure_loaded()
