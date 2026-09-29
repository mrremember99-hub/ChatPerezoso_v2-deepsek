SHELL := /bin/zsh
PY    := python3
PIP   := $(PY) -m pip
BREAK := --break-system-packages

.PHONY: help install install-dev install-all run check test lint health clean

help:
	@echo "ChatPerezoso — comandos disponibles:"
	@echo ""
	@echo "  make install       Instala dependencias base (Python global)"
	@echo "  make install-dev   Instala también las de desarrollo"
	@echo "  make install-all   Instala todo (incluye MCP)"
	@echo "  make run           Arranca la app"
	@echo "  make check         Diagnóstico sin arrancar"
	@echo "  make test          Corre los tests con pytest"
	@echo "  make lint          ruff + mypy"
	@echo "  make health        Informe detallado del proyecto"
	@echo "  make clean         Limpia caches (no toca el Python global)"

install:
	@$(PIP) install $(BREAK) -e .
	@echo "✓ Dependencias base instaladas."

install-dev: install
	@$(PIP) install $(BREAK) -e ".[dev]"
	@echo "✓ Dependencias de desarrollo instaladas."

install-all: install
	@$(PIP) install $(BREAK) -e ".[dev,mcp]"
	@echo "✓ Todo instalado."

run:
	@$(PY) main.py

check:
	@$(PY) bootstrap.py --check

test:
	@pytest -q tests/

lint:
	@ruff check core ui plugins scripts tests
	@mypy core ui plugins

health:
	@$(PY) scripts/health_check.py || true

clean:
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	@echo "✓ Caches limpiados."
