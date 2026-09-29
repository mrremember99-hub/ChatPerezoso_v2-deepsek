#!/usr/bin/env bash
# Instalación de ChatPerezoso sobre el Python global (Homebrew 3.12).
#
# Sin venv: es una decisión de diseño del proyecto. El código se
# instala en modo editable en el Python del sistema, lo que registra
# los entry points de los plugins y permite `python3 main.py` desde
# cualquier directorio.
#
# Uso: ./setup.sh [--dev] [--mcp] [--all]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

WITH_DEV=0
WITH_MCP=0
for arg in "$@"; do
  case "$arg" in
    --dev) WITH_DEV=1 ;;
    --mcp) WITH_MCP=1 ;;
    --all) WITH_DEV=1; WITH_MCP=1 ;;
    *) echo "Opción desconocida: $arg" >&2; exit 1 ;;
  esac
done

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "✗ Python no encontrado. Instala Python 3.12:" >&2
  echo "    brew install python@3.12" >&2
  exit 1
fi

PY_VER=$("$PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "→ Python $PY_VER en $(command -v "$PY")"

EXTRAS=""
if [ "$WITH_DEV" -eq 1 ] && [ "$WITH_MCP" -eq 1 ]; then
  EXTRAS="[dev,mcp]"
elif [ "$WITH_DEV" -eq 1 ]; then
  EXTRAS="[dev]"
elif [ "$WITH_MCP" -eq 1 ]; then
  EXTRAS="[mcp]"
fi

echo "→ Instalando ChatPerezoso en modo editable${EXTRAS:+ con extras $EXTRAS}"

# --break-system-packages: el Python de Homebrew está marcado como
# "externally managed" (PEP 668). En este proyecto personal no usamos
# venv por decisión de diseño (ver handoff).
"$PY" -m pip install --break-system-packages --upgrade pip >/dev/null
"$PY" -m pip install --break-system-packages -e ".${EXTRAS}"

cat <<MSG

✓ Instalación completada.

Para arrancar:
    python3 main.py

Diagnóstico sin arrancar:
    python3 bootstrap.py --check

Tests:
    pytest -q tests/
MSG
