"""P3#22: editar/insertar invalidan la cache de resultados."""
from __future__ import annotations

from pathlib import Path


# -- contrato de configuracion ---------------------------------------

def test_editar_insertar_en_invalidating_tools():
    from ui.controllers.app_controller import _INVALIDATING_TOOLS
    assert "editar_archivo" in _INVALIDATING_TOOLS
    assert "insertar_en_archivo" in _INVALIDATING_TOOLS


def test_delete_from_tree_invalida_cache():
    """Contrato estatico: el borrado desde el arbol llama a
    self.composite.cache.invalidate_all()."""
    src = Path("ui/controllers/app_controller.py").read_text()
    i = src.index("def _on_delete_requested")
    j = src.index("\n    def ", i + 20)
    body = src[i:j]
    assert "invalidate_all" in body


# -- comportamiento real del CachedToolProvider ----------------------

def test_editar_invalida_cache_de_lectura():
    """Patron editar+releer: antes devolvia stale; ahora va al source."""
    from core.composite_tools import CachedToolProvider

    class _Source:
        def __init__(self):
            self.calls = 0

        def definitions(self):
            return []

        def intent_rules(self):
            return {}

        def requires_confirmation(self, name):
            return False

        def call(self, name, arguments, **kwargs):
            self.calls += 1
            return f"result-{name}-{self.calls}"

    src = _Source()
    cached = CachedToolProvider(
        src,
        cacheable={"leer_archivo"},
        invalidating={"editar_archivo", "insertar_en_archivo"},
    )

    # Poblar cache con dos lecturas identicas.
    r1 = cached.call("leer_archivo", {"path": "a.py"})
    r2 = cached.call("leer_archivo", {"path": "a.py"})
    assert r1 == r2, "segunda lectura deberia venir de cache"

    # Editar invalida.
    cached.call("editar_archivo", {"path": "a.py"})

    # Releer va al source: resultado distinto.
    r3 = cached.call("leer_archivo", {"path": "a.py"})
    assert r3 != r1, "tras editar, la lectura debe ir al source"


def test_insertar_invalida_cache_de_lectura():
    from core.composite_tools import CachedToolProvider

    class _Source:
        def __init__(self):
            self.calls = 0

        def definitions(self):
            return []

        def intent_rules(self):
            return {}

        def requires_confirmation(self, name):
            return False

        def call(self, name, arguments, **kwargs):
            self.calls += 1
            return f"r-{self.calls}"

    src = _Source()
    cached = CachedToolProvider(
        src,
        cacheable={"leer_archivo"},
        invalidating={"insertar_en_archivo"},
    )
    r1 = cached.call("leer_archivo", {"path": "a.py"})
    r2 = cached.call("leer_archivo", {"path": "a.py"})
    assert r1 == r2
    cached.call("insertar_en_archivo", {"path": "a.py", "line": 1})
    r3 = cached.call("leer_archivo", {"path": "a.py"})
    assert r3 != r1
