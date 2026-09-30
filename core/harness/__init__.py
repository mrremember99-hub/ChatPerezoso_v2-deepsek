"""Harness v3 — ciclo de agente con loop detection, durable
execution, tool schema compilation, health monitoring y completion
verification.

Spec completa: docs/harness-v3.md.

Estado de implementacion:
  S0  (esta)  — estructura vacia + dataclasses
  S1          — loop detection + health monitoring
  S2          — durable execution
  S3          — tool schema compilation
  S4          — session (ciclo completo)
  S5          — VRR-Stop + completion verification
  S6          — migracion de UI
  S7          — limpieza (borrar ui/workers.py)

Importar cualquier simbolo de este paquete en S0 es seguro. Los
modulos aun no implementados lanzan NotImplementedError al llamar
sus funciones, no al importarlos.
"""
from __future__ import annotations

__all__: list[str] = []
