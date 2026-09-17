# ChatPerezoso v2

Aplicación de escritorio local para conversar con Ollama y operar sobre una
carpeta de trabajo mediante herramientas. El núcleo es pequeño; lo demás vive
en plugins que pueden añadirse o quitarse sin tocarlo.

## Principios

- El núcleo hace solo: UI, transporte con Ollama, conversación, streaming,
  herramientas básicas, workspace y agentes.
- Las funciones adicionales viven en `plugins/`.
- Las operaciones de archivos están confinadas al workspace elegido.
- Las operaciones destructivas requieren confirmación explícita.
- No hay agentes autónomos, memoria de largo plazo ni benchmarks en el núcleo.

## Arranque

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 main.py
