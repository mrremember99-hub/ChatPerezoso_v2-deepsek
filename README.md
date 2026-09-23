# ChatPerezoso v2

Cliente de chat con Ollama, herramientas y plugins. Núcleo pequeño,
plugins todo lo demás.

## Arranque rápido

### macOS / Linux

```bash
git clone <repo> chatperezoso
cd chatperezoso
./setup.sh --mcp
source .venv/bin/activate
python bootstrap.py

## Instalación para desarrollo

```bash
pip install -e ".[dev]"
```

El extra `dev` instala `ruff`, `mypy`, `pytest`, `pytest-qt` y `pytest-timeout`. Sin `ruff` ni `mypy`, el plugin verificador degrada a los niveles 1, 3 y 4 (sintaxis, secretos, conflictos).
