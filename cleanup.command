#!/bin/zsh
# Análisis y limpieza del proyecto ChatPerezoso.
# Doble clic desde Finder o ./cleanup.command desde Terminal.

cd "$(dirname "$0")" || exit 1

if [ ! -d .venv ]; then
  echo "No hay .venv. Ejecuta primero ./setup.command --all"
  echo ""
  echo "Pulsa Enter para cerrar..."
  read -r _
  exit 1
fi

source .venv/bin/activate

# Si se lanzó sin argumentos, hacemos el flujo guiado.
if [ $# -eq 0 ]; then
  echo "\033[1mChatPerezoso · Limpieza\033[0m"
  echo ""
  echo "  1) Solo analizar (recomendado)"
  echo "  2) Analizar y borrar cachés"
  echo "  3) Analizar y borrar cachés + legacy"
  echo ""
  printf "  Elige [1-3]: "
  read -r choice
  case "$choice" in
    2) exec python scripts/cleanup.py --apply ;;
    3) exec python scripts/cleanup.py --aggressive ;;
    *) exec python scripts/cleanup.py ;;
  esac
fi

# Con argumentos, se los pasamos directamente al script.
exec python scripts/cleanup.py "$@"