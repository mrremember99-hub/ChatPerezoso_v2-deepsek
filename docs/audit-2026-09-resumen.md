# Auditoria ChatPerezoso v2 - Resumen ejecutivo

**Fecha:** 2026-09
**Rama:** main (commit 3e1e541)
**Estado:** cerrada
**Tests:** 641 passed, 4 skipped

Documento completo: [audit-2026-09.md](./audit-2026-09.md)

## Que se audito

Nucleo (concurrencia, streaming, contexto, tool calling) y subsistemas de UI (workers, renderers, controladores). Se apoyo en lectura de codigo, benchmarks sinteticos sin red, y auditorias externas que aportaron hallazgos adicionales.

## Hallazgos y decisiones

| ID | Hallazgo | Severidad | Decision | Evidencia |
|----|----------|-----------|----------|-----------|
| H1 | Race AsyncRunner.submit/close | Critico | Aplicado | test_async_runner.py |
| H2 | _inject_system_prompts muta el dict del llamante | Alto | Aplicado | test en test_ollama.py |
| H3 | Senal muerta ChatWorker.text | Medio | Eliminada | grep sin resultados |
| H4 | Coste de render en streaming | Alto | Aplicado (2.2) | code final 26.6 -> 0.1 ms |
| H5 | ToolIntentGate._RULES_REGISTRY global mutable | Medio | Aplicado | 641 tests verdes |
| H12 | AsyncRunner.__del__ sin close_callback | Medio | Aplicado | - |
| H14 | Cambio de agente durante streaming | Bajo | Aplicado | sidebar deshabilita los 4 controles |
| H15 | Guard en _render_markdown_block | Bajo | Aplicado | reset si posiciones invalidas |

## Evaluado y descartado (con datos)

- Fase 2.1 - setLayoutEnabled(False) + beginEditBlock. Medida: prose accum 145 -> 154 ms, code accum 12 -> 21 ms. El patron real es 1 mutacion por drain. Revertido, no reintentar sin cambiar el streaming.

- Fase 5 - Benchmark MikeVeerman. El repo no existe con ese nombre. Sustituido por scripts/benchmark_intent_gate.py.

- Timeouts adaptativos. Medido con qwen3:14b thinking ON: peor TTFT 42 s. El timeout actual (read=300 s) es por lectura, no total.

## Pendiente real

- Evaluar granite4.1:3b como modelo por defecto para equipos con poca RAM. gemma4:12b da TTFT 85 s con presion de memoria.

## Hallazgos empiricos de modelos

- qwen3-coder:30b: pierde tool calling en contexto largo. El stall guard detecta la anomalia y aborta la fase limpiamente, pero no la recupera.
- gpt-oss:20b: completo las 9 fases del prompt de prueba sin incidencias.
- Implicacion: para tareas multi-fase de codigo, preferir modelos con mayor adherencia a tool calling en contexto largo.

## Referencias

- [Documento completo](./audit-2026-09.md)
- [Estado del proyecto](../TODO.md)
- [Plan V2](../PLAN_V2.md)
