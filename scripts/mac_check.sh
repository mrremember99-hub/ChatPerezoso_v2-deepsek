#!/bin/zsh
# Verifica el entorno macOS para ChatPerezoso.
# No modifica nada. Solo informa.

set -u

if [ -t 1 ]; then
  BOLD=$'\033[1m'; GREEN=$'\033[32m'; RED=$'\033[31m'
  YELLOW=$'\033[33m'; DIM=$'\033[2m'; RESET=$'\033[0m'
else
  BOLD=""; GREEN=""; RED=""; YELLOW=""; DIM=""; RESET=""
fi

ok()   { echo "  ${GREEN}✓${RESET} $1"; }
warn() { echo "  ${YELLOW}⚠${RESET} $1"; }
fail() { echo "  ${RED}✗${RESET} $1"; }
line() { printf "  ${DIM}%-28s${RESET} %s\n" "$1" "$2"; }

echo "${BOLD}ChatPerezoso · Verificación del entorno macOS${RESET}"

echo "\n${BOLD}Sistema${RESET}"
line "Versión" "$(sw_vers -productVersion)"
line "Build" "$(sw_vers -buildVersion)"
line "Arquitectura" "$(uname -m)"
line "Kernel" "$(uname -r)"
line "Hostname" "$(hostname -s 2>/dev/null || echo '—')"

echo "\n${BOLD}Rutas Homebrew${RESET}"
if [ "$(uname -m)" = "arm64" ]; then
  line "Prefijo esperado" "/opt/homebrew"
  [ -d /opt/homebrew ] && ok "existe" || warn "no existe"
else
  line "Prefijo esperado" "/usr/local"
  [ -d /usr/local/Homebrew ] && ok "existe" || warn "no existe"
fi

echo "\n${BOLD}Herramientas${RESET}"
for tool in brew python3 node npx git ollama; do
  if command -v "$tool" >/dev/null 2>&1; then
    ok "$tool → $(command -v "$tool")"
  else
    warn "$tool no encontrado"
  fi
done

echo "\n${BOLD}Xcode Command Line Tools${RESET}"
if xcode-select -p >/dev/null 2>&1; then
  ok "instaladas en $(xcode-select -p)"
  if [ -d "$(xcode-select -p)" ]; then
    ok "ruta válida"
  else
    fail "ruta inválida · reinstala con: xcode-select --install"
  fi
else
  fail "no instaladas · xcode-select --install"
fi

echo "\n${BOLD}Python${RESET}"
if command -v python3 >/dev/null 2>&1; then
  line "Intérprete" "$(command -v python3)"
  line "Versión" "$(python3 --version)"
  line "venv disponible" "$(python3 -c 'import venv; print("sí")' 2>/dev/null || echo 'no')"
  line "ssl" "$(python3 -c 'import ssl; print(ssl.OPENSSL_VERSION)' 2>/dev/null || echo '—')"
fi

echo "\n${BOLD}Ollama${RESET}"
if command -v ollama >/dev/null 2>&1; then
  ok "binario presente"
  if curl -s --max-time 2 http://localhost:11434/api/tags >/dev/null 2>&1; then
    ok "servidor respondiendo"
    N=$(curl -s --max-time 2 http://localhost:11434/api/tags | \
        python3 -c 'import sys,json; print(len(json.load(sys.stdin).get("models",[])))' 2>/dev/null || echo '?')
    line "modelos" "$N"
  else
    warn "servidor no responde · ollama serve"
  fi
else
  warn "no instalado · brew install ollama"
fi

echo "\n${BOLD}Espacio en disco${RESET}"
df -h . | tail -1 | awk '{printf "  %-28s %s usados de %s\n", "Directorio actual", $3, $2}'

echo "\n${BOLD}Puertos en uso (por si Ollama no arranca)${RESET}"
if lsof -nP -iTCP:11434 -sTCP:LISTEN >/dev/null 2>&1; then
  ok "11434 (Ollama) escuchando"
  lsof -nP -iTCP:11434 -sTCP:LISTEN | tail -n +2 | while read -r line; do
    echo "    $line"
  done
else
  warn "11434 libre"
fi

echo ""