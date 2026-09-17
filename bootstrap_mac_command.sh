#!/usr/bin/env bash
# bootstrap_mac_command.sh — Genera ChatPerezoso.command para macOS.
# Doble clic desde Finder abre Terminal y ejecuta la aplicación.
set -euo pipefail

if [[ ! -f main.py ]]; then
    echo "Ejecútalo desde la raíz del proyecto (donde está main.py)." >&2
    exit 1
fi

cat > ChatPerezoso.command <<'EOF'
#!/bin/bash
# Lanzador de ChatPerezoso para macOS.
#
# Doble clic desde Finder (o ./ChatPerezoso.command desde terminal).
# Abre Terminal, activa el venv si existe y ejecuta la aplicación.
# Si algo falla, se queda abierto para que puedas leer el error.

set -u

# Directorio del proyecto: donde vive este script.
cd "$(dirname "$0")" || exit 1
PROJECT_DIR="$(pwd)"

echo "▶ ChatPerezoso"
echo "  Proyecto: $PROJECT_DIR"

# Activa el entorno virtual si existe. Prueba .venv, venv y env.
for env_name in .venv venv env; do
    if [ -f "$env_name/bin/activate" ]; then
        # shellcheck source=/dev/null
        source "$env_name/bin/activate"
        echo "  Entorno:  $env_name"
        break
    fi
done

# Elige el intérprete. Si hay venv activo, 'python' apunta al venv.
PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo
    echo "✗ No se ha encontrado python3 en el PATH."
    echo "  Instálalo con: brew install python"
    echo
    read -r -p "Pulsa Enter para cerrar…"
    exit 1
fi

echo "  Python:   $($PYTHON_BIN --version 2>&1)"
echo

# Ejecuta la app.
"$PYTHON_BIN" main.py
EXIT_CODE=$?

if [ "$EXIT_CODE" -ne 0 ]; then
    echo
    echo "✗ La aplicación terminó con código $EXIT_CODE."
    echo
    read -r -p "Pulsa Enter para cerrar…"
fi

exit "$EXIT_CODE"
EOF

chmod +x ChatPerezoso.command

echo
echo "✓ ChatPerezoso.command creado en:"
echo "  $(pwd)/ChatPerezoso.command"
echo
echo "Uso:"
echo "  · Doble clic desde Finder."
echo "  · O desde terminal: ./ChatPerezoso.command"
echo
echo "La primera vez que lo ejecutes desde Finder, macOS puede avisar de"
echo "que es un archivo de un desarrollador no identificado. Solución:"
echo "  · Clic derecho → Abrir → Abrir."
echo "  O desde terminal, una sola vez:"
echo "  · xattr -cr ChatPerezoso.command"