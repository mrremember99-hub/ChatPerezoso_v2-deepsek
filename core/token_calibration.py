"""Calibración empírica del ratio chars/token por modelo.

Cada modelo usa un tokenizer distinto. Los ratios fijos (2.8/4.2) son
una aproximación, pero Ollama nos da el número real de tokens del
prompt en `prompt_eval_count`. Con eso, cada modelo puede aprender su
propio ratio.

Diseño:
  · Un `_ModelCalibration` por modelo.
  · `observe(chars, actual_tokens)` actualiza el ratio con EWMA.
  · `chars_per_token(model)` devuelve el ratio aprendido, o el default
    si no hay datos suficientes.

El estado es global al proceso y vive en memoria. No se persiste a
disco: es una optimización de runtime que se reconstruye en segundos.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

# H4 (2026-09-27): el default solo aplica si has_calibration() es
# True (rama no usada en produccion) o si una futura ruta llama
# directamente. Alineado con _PROSE_CHARS_PER_TOKEN de context_window
# para coherencia. Medicion empirica con gpt-oss:20b: prosa ES 4.90,
# prosa EN 5.69, codigo Python 3.40 chars/token. Los 4.2/2.8 de
# context_window son conservadores por diseno; la calibracion por
# modelo (EWMA) converge al ratio real tras pocas observaciones.
_DEFAULT_CHARS_PER_TOKEN = 4.2
_ALPHA = 0.20
# H4: 2 en vez de 3. Con 2 observaciones el ratio ya esta mezclado
# y es razonablemente estable. Acorta la ventana en que se aplican
# las constantes conservadoras por defecto.
_MIN_OBSERVATIONS = 2
_MIN_RATIO = 2.0
_MAX_RATIO = 6.0


@dataclass
class _ModelCalibration:
    chars_per_token: float = _DEFAULT_CHARS_PER_TOKEN
    observations: int = 0


class TokenCalibrationStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._models: dict[str, _ModelCalibration] = {}

    def observe(
        self,
        model: str,
        *,
        chars: int,
        actual_tokens: int,
        alpha: float = _ALPHA,
    ) -> None:
        if not model or chars <= 0 or actual_tokens <= 0:
            return
        observed = chars / actual_tokens
        if not (_MIN_RATIO <= observed <= _MAX_RATIO):
            return

        with self._lock:
            cal = self._models.get(model)
            if cal is None:
                self._models[model] = _ModelCalibration(
                    chars_per_token=observed,
                    observations=1,
                )
                return
            cal.chars_per_token = (
                (1.0 - alpha) * cal.chars_per_token + alpha * observed
            )
            cal.observations += 1

    def has_calibration(self, model: str) -> bool:
        if not model:
            return False
        with self._lock:
            cal = self._models.get(model)
        return cal is not None and cal.observations >= _MIN_OBSERVATIONS

    def chars_per_token(self, model: str) -> float:
        if not self.has_calibration(model):
            return _DEFAULT_CHARS_PER_TOKEN
        with self._lock:
            cal = self._models.get(model)
        assert cal is not None
        return cal.chars_per_token

    def stats(self) -> dict[str, dict[str, float]]:
        with self._lock:
            return {
                model: {
                    "chars_per_token": cal.chars_per_token,
                    "observations": cal.observations,
                }
                for model, cal in self._models.items()
            }

    def reset(self) -> None:
        with self._lock:
            self._models.clear()


_default = TokenCalibrationStore()


def observe(model: str, *, chars: int, actual_tokens: int) -> None:
    _default.observe(model, chars=chars, actual_tokens=actual_tokens)


def has_calibration(model: str) -> bool:
    return _default.has_calibration(model)


def chars_per_token(model: str) -> float:
    return _default.chars_per_token(model)


def stats() -> dict[str, dict[str, float]]:
    return _default.stats()


def reset() -> None:
    _default.reset()
