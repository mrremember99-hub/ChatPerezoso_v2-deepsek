#!/usr/bin/env bash
# Instalación de ChatPerezoso en un venv aislado.
# Uso: ./setup.sh [--dev] [--mcp]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

WITH_DEV=0
WITH_MCP=0
for arg in "$@"; do
  case "$arg" in
    --dev) WITH_DEV=1 ;;
    --mcp) WITH_MCP=1 ;;
    *) echo "Opción desconocida: $arg" >&2; exit 1 ;;
  esac
done

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "✗ Python no encontrado. Instala Python 3.11 o superior." >&2
  exit 1
fi

echo "→ Creando entorno virtual en .venv"
"$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

echo "→ Actualizando pip"
python -m pip install --upgrade pip >/dev/null

echo "→ Instalando dependencias base"
pip install -r requirements.txt

if [ "$WITH_MCP" -eq 1 ]; then
  echo "→ Instalando dependencias MCP"
  pip install -r plugins/mcp/requirements.txt
fi

if [ "$WITH_DEV" -eq 1 ]; then
  echo "→ Instalando dependencias de desarrollo"
  pip install -r requirements-dev.txt
fi

echo "→ Instalando el proyecto en modo editable (entry points de plugins)"
pip install -e .

cat <<EOF

✓ Instalación completada.

Para arrancar:
    source .venv/bin/activate
    python bootstrap.py

Para diagnóstico sin arrancar:
    python bootstrap.py --check
EOF