# -- 3. Python ---------------------------------------------------------------
step "Comprobando Python"

# Buscamos explícitamente un Python 3.12 o 3.13. PySide6 todavía no
# publica wheels para 3.14, así que no sirve.
PYTHON_BIN=""
for candidate in \
    /opt/homebrew/bin/python3.13 \
    /opt/homebrew/bin/python3.12 \
    python3.13 python3.12; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PYTHON_BIN="$candidate"
    break
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  fail "Python 3.12 o 3.13 no encontrado"
  echo "    Instálalo con:"
  echo "    ${DIM}brew install python@3.12${RESET}"
  exit 1
fi

PY_VER=$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
ok "Python $PY_VER en $PYTHON_BIN"

# -- 4. venv -----------------------------------------------------------------
step "Preparando entorno virtual"

# Si existe un .venv de un intento anterior con Python equivocado, lo
# recreamos. Si el .venv está bien, lo reutilizamos.
if [ -d .venv ]; then
  VENV_PY=$(cat .venv/pyvenv.cfg 2>/dev/null | grep -i '^version' | head -1 || true)
  VENV_PY_MAJOR=$(echo "$VENV_PY" | grep -oE '[0-9]+\.[0-9]+' | head -1 || true)
  if [ "$VENV_PY_MAJOR" != "$PY_VER" ]; then
    warn ".venv existente usa Python $VENV_PY_MAJOR; recreando con $PY_VER"
    rm -rf .venv
  fi
fi

if [ -d .venv ]; then
  ok ".venv ya existe"
else
  "$PYTHON_BIN" -m venv .venv
  ok ".venv creado con Python $PY_VER"
fi
source .venv/bin/activate

# -- 5. pip ------------------------------------------------------------------
step "Actualizando pip"

# Nos aseguramos de que pip no intente --user dentro del venv.
unset PIP_USER
export PIP_USER=0

python -m pip install --upgrade pip >/dev/null
ok "pip $(pip --version | cut -d' ' -f2)"