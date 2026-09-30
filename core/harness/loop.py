"""Loop detection multi-patron.

Spec: docs/harness-v3.md §3.

Detectores:
  · generic_repeat    — mismo (tool, args) consecutivo
  · ping_pong         — patron alternante A->B->A->B
  · poll_no_progress  — mismo (tool, args, resultado)
  · post_compaction   — repite patron previo a compactacion

Escalation ladder:
  warning  -> corrective  -> abort

El corrective NO mata el run. Inyecta un prompt al modelo.
Tras max_corrective_attempts, escala a abort.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from dataclasses import dataclass
from typing import Any, ClassVar

from core.harness.policy import LoopPolicy

# ── Normalizacion de firmas ────────────────────────────────


def _normalize_string(s: str) -> str:
    """Preserva indentacion, colapsa whitespace interno."""
    out: list[str] = []
    for ln in s.split("\n"):
        ln = ln.rstrip()
        m = re.match(r"^(\s*)(.*)$", ln)
        if not m:
            out.append(ln)
            continue
        leading, rest = m.groups()
        rest = re.sub(r"  +", " ", rest)
        out.append(leading + rest)
    return "\n".join(out)


def _normalize_value(v: Any) -> Any:
    if isinstance(v, str):
        return _normalize_string(v)
    if isinstance(v, dict):
        return {k: _normalize_value(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_normalize_value(item) for item in v]
    return v


def canonical_signature(tool: str, args: dict[str, Any]) -> str:
    """Hash canonico de (tool, args) para deteccion de repeticiones."""
    normalized = json.dumps(
        _normalize_value(args),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(
        f"{tool}|{normalized}".encode()
    ).hexdigest()[:16]


def result_signature(result: Any) -> str:
    """Hash normalizado del resultado de una tool."""
    if result is None:
        return ""
    s = re.sub(r"\s+", " ", str(result)).strip()
    return hashlib.sha256(s.encode()).hexdigest()[:12]


def detect_ping_pong(
    signatures: list[str],
    *,
    min_period: int = 2,
    max_period: int = 4,
    min_repeats: int = 2,
) -> tuple[str, int] | None:
    """Busca el periodo mas pequeno que se repite min_repeats veces.

    Devuelve (patron, ciclos) o None si no hay patron.
    """
    for period in range(min_period, max_period + 1):
        if len(signatures) < period * min_repeats:
            continue
        window = signatures[-period * min_repeats:]
        pattern = window[:period]
        repeats = 0
        for i in range(0, len(window), period):
            if window[i:i + period] == pattern:
                repeats += 1
            else:
                break
        if repeats >= min_repeats:
            return ("|".join(pattern), repeats)
    return None


# ── Dataclasses publicas ───────────────────────────────────


@dataclass(frozen=True)
class LoopObservation:
    tool: str
    args: dict[str, Any]
    signature: str
    result_signature: str
    result_summary: str


@dataclass(frozen=True)
class LoopDecision:
    action: str          # "silent" | "warning" | "corrective" | "abort"
    detector: str = ""
    signature: str = ""
    count: int = 0
    reason: str = ""


# ── Detector principal ─────────────────────────────────────


class LoopDetector:
    """Detector multi-patron de bucles."""

    def __init__(self, policy: LoopPolicy) -> None:
        self.policy = policy
        self.recent: deque[LoopObservation] = deque(
            maxlen=policy.window_size
        )
        self._correctives_count = 0
        self._pre_compaction_sigs: list[str] = []
        self._armed_post_compaction = False
        self._post_compaction_calls = 0
        self._post_compaction_matches = 0

    def reset(self) -> None:
        self.recent.clear()
        self._correctives_count = 0
        self._pre_compaction_sigs = []
        self._armed_post_compaction = False
        self._post_compaction_calls = 0
        self._post_compaction_matches = 0

    @property
    def correctives_count(self) -> int:
        return self._correctives_count

    def observe_compaction(
        self, *, tokens_before: int, tokens_after: int,
    ) -> None:
        """Marca el inicio de la ventana post-compactacion."""
        self._pre_compaction_sigs = [
            obs.signature for obs in list(self.recent)[-5:]
        ]
        self._armed_post_compaction = True
        self._post_compaction_calls = 0
        self._post_compaction_matches = 0

    def observe(
        self,
        tool: str,
        args: dict[str, Any],
        result: Any = None,
        *,
        result_summary: str = "",
    ) -> LoopDecision:
        sig = canonical_signature(tool, args)
        res_sig = result_signature(result)
        obs = LoopObservation(
            tool=tool,
            args=args,
            signature=sig,
            result_signature=res_sig,
            result_summary=result_summary,
        )
        self.recent.append(obs)

        if not self.policy.enabled:
            return LoopDecision(action="silent")

        decisions: list[tuple[str, str, str, int, str]] = []

        self._check_generic_repeat(decisions)
        self._check_ping_pong(decisions)
        self._check_poll_no_progress(decisions)
        self._check_post_compaction(sig, decisions)

        if not decisions:
            return LoopDecision(action="silent")

        priority = {"abort": 0, "corrective": 1, "warning": 2}
        decisions.sort(key=lambda d: priority[d[0]])
        action, detector, sig_out, count, reason = decisions[0]

        if action == "corrective":
            self._correctives_count += 1
            if (
                self._correctives_count
                > self.policy.max_corrective_attempts
            ):
                action = "abort"
                reason = (
                    "max_corrective_attempts="
                    f"{self.policy.max_corrective_attempts} superado"
                )

        return LoopDecision(
            action=action,
            detector=detector,
            signature=sig_out,
            count=count,
            reason=reason,
        )

    # ── Detectores internos ─────────────────────────────

    def _check_generic_repeat(
        self, decisions: list[tuple[str, str, str, int, str]],
    ) -> None:
        if not self.recent:
            return
        last = self.recent[-1].signature
        count = 0
        for obs in reversed(self.recent):
            if obs.signature == last:
                count += 1
            else:
                break
        warn, correct, abort = self.policy.generic_repeat
        reason = f"mismo tool+args {count} veces consecutivas"
        if count >= abort:
            decisions.append(("abort", "generic_repeat", last, count, reason))
        elif count >= correct:
            decisions.append(
                ("corrective", "generic_repeat", last, count, reason)
            )
        elif count >= warn:
            decisions.append(
                ("warning", "generic_repeat", last, count, reason)
            )

    def _check_ping_pong(
        self, decisions: list[tuple[str, str, str, int, str]],
    ) -> None:
        sigs = [obs.signature for obs in self.recent]
        res = detect_ping_pong(sigs, min_repeats=2)
        if not res:
            return
        pattern, cycles = res
        warn, correct, abort = self.policy.ping_pong
        reason = f"patron alternante {cycles} ciclos"
        if cycles >= abort:
            decisions.append(("abort", "ping_pong", pattern, cycles, reason))
        elif cycles >= correct:
            decisions.append(
                ("corrective", "ping_pong", pattern, cycles, reason)
            )
        elif cycles >= warn:
            decisions.append(
                ("warning", "ping_pong", pattern, cycles, reason)
            )

    def _check_poll_no_progress(
        self, decisions: list[tuple[str, str, str, int, str]],
    ) -> None:
        if not self.recent:
            return
        last = self.recent[-1]
        compound = f"{last.signature}|{last.result_signature}"
        count = 0
        for obs in reversed(self.recent):
            other = f"{obs.signature}|{obs.result_signature}"
            if other == compound:
                count += 1
            else:
                break
        warn, correct, abort = self.policy.poll_no_progress
        reason = f"mismo tool+args+resultado {count} veces"
        if count >= abort:
            decisions.append(
                ("abort", "poll_no_progress", compound, count, reason)
            )
        elif count >= correct:
            decisions.append(
                ("corrective", "poll_no_progress", compound, count, reason)
            )
        elif count >= warn:
            decisions.append(
                ("warning", "poll_no_progress", compound, count, reason)
            )

    def _check_post_compaction(
        self,
        sig: str,
        decisions: list[tuple[str, str, str, int, str]],
    ) -> None:
        if not self._armed_post_compaction:
            return
        self._post_compaction_calls += 1
        if self._post_compaction_calls > 3:
            self._armed_post_compaction = False
            return
        if sig not in self._pre_compaction_sigs:
            return
        self._post_compaction_matches += 1
        matches = self._post_compaction_matches
        correct_thresh, abort_thresh = self.policy.post_compaction
        reason = f"{matches} matches tras compactacion"
        if matches >= abort_thresh:
            decisions.append(
                ("abort", "post_compaction", sig, matches, reason)
            )
        elif matches >= correct_thresh:
            decisions.append(
                ("corrective", "post_compaction", sig, matches, reason)
            )


# ── Constructor de prompt correctivo ───────────────────────


class CorrectivePromptBuilder:
    """Construye el prompt inyectado al modelo cuando se detecta bucle."""

    _SUGGESTIONS: ClassVar[dict[str, list[str]]] = {
        "generic_repeat": [
            "Usa buscar_simbolo o rag_query en vez de repetir.",
            "Reescribe el archivo completo con escribir_archivo.",
            "Si estas atascado, para y explica al usuario por que.",
        ],
        "ping_pong": [
            "El patron no converge. Cambia de estrategia.",
            "Reescribe el archivo completo con escribir_archivo.",
            "Explica al usuario que error persiste y por que.",
        ],
        "poll_no_progress": [
            "El estado no cambia entre llamadas.",
            "Verifica si la operacion previa realmente tuvo efecto.",
            "Prueba una herramienta distinta.",
        ],
        "post_compaction": [
            "El contexto se compacto y volviste al mismo patron.",
            "Cambia de estrategia por completo.",
            "Pide informacion adicional al usuario.",
        ],
    }

    def build(
        self,
        decision: LoopDecision,
        last_obs: LoopObservation | None = None,
    ) -> str:
        lines = ["[Harness · Loop detectado]", ""]
        lines.append(
            f"Patron: {decision.detector} "
            f"(repeticiones: {decision.count})"
        )
        lines.append(f"Motivo: {decision.reason}")
        lines.append("")
        if last_obs is not None:
            lines.append(f"Ultima llamada: {last_obs.tool}")
            if last_obs.result_summary:
                lines.append(f"Resultado: {last_obs.result_summary}")
            lines.append("")
        suggestions = self._SUGGESTIONS.get(
            decision.detector,
            ["Cambia de estrategia."],
        )
        lines.append("Sugerencias:")
        for s in suggestions:
            lines.append(f"  \u00b7 {s}")
        lines.append("")
        lines.append(
            "No repitas la misma llamada. Cambia de estrategia o "
            "explica por que estas atascado."
        )
        return "\n".join(lines)
