SHELL := /bin/zsh
VENV  := .venv
PY    := $(VENV)/bin/python
PIP   := $(VENV)/bin/pip

.PHONY: help install install-dev install-all run check reset health clean

help:
	@echo "ChatPerezoso — comandos disponibles:"
	@echo ""
	@echo "  make install       Instala dependencias base en .venv"
	@echo "  make install-dev   Instala también las de desarrollo"
	@echo "  make install-all   Instala todo (incluye MCP)"
	@echo "  make run           Prepara y arranca la app"
	@echo "  make check         Diagnóstico sin arrancar"
	@echo "  make health        Informe detallado del proyecto"
	@echo "  make reset         Borra el estado local (config, agents, etc.)"
	@echo "  make clean         Elimina .venv y caches"

$(VENV)/bin/activate:
	@python3 -m venv $(VENV)
	@$(PIP) install --upgrade pip >/dev/null

install: $(VENV)/bin/activate
	@$(PIP) install -r requirements.txt
	@$(PIP) install -e .
	@echo "✓ Dependencias base instaladas."

install-dev: install
	@$(PIP) install -r requirements-dev.txt
	@echo "✓ Dependencias de desarrollo instaladas."

install-all: install
	@$(PIP) install -r plugins/mcp/requirements.txt
	@$(PIP) install -r requirements-dev.txt
	@echo "✓ Todo instalado."

run: $(VENV)/bin/activate
	@$(PY) bootstrap.py

check: $(VENV)/bin/activate
	@$(PY) bootstrap.py --check

health: $(VENV)/bin/activate
	@$(PY) scripts/health_check.py

reset: $(VENV)/bin/activate
	@$(PY) bootstrap.py --reset --yes

clean:
	@rm -rf $(VENV) .pytest_cache **/__pycache__ 2>/dev/null || true
	@echo "✓ Limpieza completada."