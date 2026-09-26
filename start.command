#!/bin/zsh
# Arranca ChatPerezoso v2.
# Doble clic desde Finder o ./start.command desde Terminal.

cd "$(dirname "$0")" || exit 1

if [ ! -d .venv ]; then
  echo "No hay .venv. Ejecuta primero ./setup.command"
  echo ""
  echo "Pulsa Enter para cerrar..."
  read -r _
  exit 1
fi

source .venv/bin/activate

# Argumentos opcionales:
#   --debug   activa CHATPEREZOSO_DEBUG=1 (log de snapshots a consola)
if [ "$1" = "--debug" ]; then
  export CHATPEREZOSO_DEBUG=1
fi

exec python bootstrap.py
