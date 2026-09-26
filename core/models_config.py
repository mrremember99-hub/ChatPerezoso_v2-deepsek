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
        },
        "qwen3:14b": {
          "mode": "native",
          "thinking": false,
          "note": "thinking off para TTFT bajo"
        }
      }
    }

Valores válidos de "mode":
  · "native" — fuerza tool calling nativo (parámetro tools + tool_calls)
  · "xml"    — fuerza prompt-guided XML (bloque <tool_call>)
  · "auto"   — delega en /api/show (comportamiento por defecto)

Valores válidos de "thinking":
  · true     — fuerza thinking (razonamiento interno)
  · false    — desactiva thinking (TTFT bajo, respuestas directas)
  · ausente  — Ollama decide según el modelo (comportamiento por defecto)

Si un modelo no aparece en models.json, se usa "auto" sin override de
thinking.
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

# Niveles válidos de thinking para modelos que los exigen
# (gpt-oss: "Passing true/false is ignored for that model").
# Los modelos que aceptan bool se siguen controlando con true/false.
THINKING_LEVELS: frozenset[str] = frozenset({"low", "medium", "high"})

# Modelos verificados con tool calling nativo en la app.
# Verificados manualmente con el prompt OVERPAPER de 9 fases.
VERIFIED_TOOL_MODELS: frozenset[str] = frozenset({
    "gpt-oss:20b",
    "ministral-3",
})

def is_verified_tool_model(name: str) -> bool:
    """True si el modelo esta en la whitelist.

    Compara sin tag: Ollama reporta ``ministral-3:latest``
    pero el usuario escribe ``ministral-3``. Sin normalizar,
    un modelo verificado pasaria por no verificado.
    """
    base = name.split(":", 1)[0].strip()
    if not base:
        return False
    return any(m.split(":", 1)[0].strip() == base
               for m in VERIFIED_TOOL_MODELS)


@dataclass(frozen=True)
class ModelOverride:
    mode: str  # "native" | "xml" | "auto"
    note: str = ""
    # None = auto (Ollama decide). bool fuerza el parámetro `think`
    # en el payload de /api/chat (True/False). str debe ser uno de
    # THINKING_LEVELS = ("low", "medium", "high") y se envía
    # literal: lo exige gpt-oss (Ollama ignora bool para ese modelo).
    thinking: bool | str | None = None
    # Texto corto que se muestra en la sidebar debajo del badge de
    # capabilities. Si está vacío, se genera uno automáticamente a
    # partir de las capabilities del modelo.
    recommendation: str = ""

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

    def set(
        self,
        model: str,
        mode: str,
        note: str = "",
        thinking: bool | str | None = None,
        recommendation: str = "",
    ) -> None:
        if mode not in VALID_MODES:
            raise ValueError(
                f"Modo inválido: {mode!r}. Debe ser uno de {sorted(VALID_MODES)}"
            )
        if thinking is not None and not isinstance(thinking, bool):
            if not (
                isinstance(thinking, str)
                and thinking.lower() in THINKING_LEVELS
            ):
                raise ValueError(
                    f"thinking inválido: {thinking!r}. Debe ser bool, "
                    f"None, o uno de {sorted(THINKING_LEVELS)}"
                )
            thinking = thinking.lower()
        self._ensure_loaded()
        # "auto" sin ningún extra es el default puro: no guardamos nada,
        # así el archivo solo contiene lo que difiere de la detección.
        if (
            mode == "auto"
            and not note
            and thinking is None
            and not recommendation
        ):
            self._overrides.pop(model, None)
        else:
            self._overrides[model] = ModelOverride(
                mode=mode,
                note=note,
                thinking=thinking,
                recommendation=recommendation,
            )
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
            raw_thinking = spec.get("thinking")
            thinking: bool | str | None
            if isinstance(raw_thinking, bool):
                thinking = raw_thinking
            elif (
                isinstance(raw_thinking, str)
                and raw_thinking.lower() in THINKING_LEVELS
            ):
                thinking = raw_thinking.lower()
            else:
                thinking = None
            raw_rec = spec.get("recommendation", "")
            recommendation = (
                str(raw_rec).strip() if isinstance(raw_rec, str) else ""
            )
            result[name] = ModelOverride(
                mode=mode,
                note=note,
                thinking=thinking,
                recommendation=recommendation,
            )
        return result

    def _save(self) -> None:
        payload: dict = {"overrides": {}}
        for name, override in sorted(self._overrides.items()):
            entry: dict = {"mode": override.mode}
            if override.note:
                entry["note"] = override.note
            if override.thinking is not None:
                entry["thinking"] = override.thinking
            if override.recommendation:
                entry["recommendation"] = override.recommendation
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
