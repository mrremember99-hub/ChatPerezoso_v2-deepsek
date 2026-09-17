"""Consulta /api/show para todos los modelos instalados y genera
un models.json con el modo detectado para cada uno.

Uso:
    python scripts/probe_models.py              # solo imprime
    python scripts/probe_models.py --write      # escribe models.json
    python scripts/probe_models.py --write --keep  # respeta overrides manuales

Los overrides manuales existentes (mode != "auto") no se sobreescriben
a menos que se use --force.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true",
                        help="escribe models.json (por defecto solo imprime)")
    parser.add_argument("--force", action="store_true",
                        help="sobrescribe también overrides manuales")
    parser.add_argument("--host", default="http://localhost:11434")
    args = parser.parse_args()

    from core.model_capabilities import clear_cache, get_capabilities
    from core.models_config import ModelsConfig
    from core.ollama import OllamaClient

    client = OllamaClient(args.host)
    models = client.list_models()
    print(f"Consultando /api/show para {len(models)} modelos...")
    print()

    # Limpiar caché para forzar consultas reales
    clear_cache()

    store = ModelsConfig()
    existing = store.all()

    results = []
    for name in sorted(models):
        caps = get_capabilities(args.host, name, force_refresh=True)
        results.append((name, caps))
        icon = {
            "native": "●",
            "xml": "◇",
            "unknown": "?",
        }.get(caps.tool_mode, "·")
        detail = f"{icon} {name:45} {caps.tool_mode:8} (source={caps.source})"
        if caps.probed:
            if caps.native_tools:
                detail += "  [tools nativo]"
            else:
                detail += "  [sin tools: XML]"
        else:
            detail += "  [/api/show no respondió]"
        print(detail)

    if not args.write:
        print()
        print("Modo solo lectura. Usa --write para generar models.json.")
        return 0

    for name, caps in results:
        # `existing.get(name)` devuelve None si no hay override, pero
        # nuestro dataclass ModelOverride siempre tiene un modo. Usamos
        # `in` para evitar la ambigüedad de tipos.
        if name in existing and not args.force:
            existing_override = existing[name]
            if existing_override.is_forced():
                print(f"· {name}: override manual preservado ({existing_override.mode})")
                continue
        # Si el modo es "native" y source es "probe", no hace falta escribir
        # nada (es el default). Solo escribimos overrides útiles.
        if caps.tool_mode == "xml":
            store.set(name, "xml", note="detectado por /api/show")
        elif caps.tool_mode == "native" and caps.source == "override":
            # Ya estaba forzado, no tocar
            pass

    print()
    print(f"OK: models.json actualizado en {store.path}")
    print(f"   {len(store.all())} override(s) guardado(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
