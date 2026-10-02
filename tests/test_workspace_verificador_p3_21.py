"""P3#21: verificar que _choose_workspace reaplica _apply_verificador."""
from __future__ import annotations

from pathlib import Path


def test_choose_workspace_reaplica_verificador():
    """Contrato estatico: tras _rebuild_composite en _choose_workspace
    debe aparecer _apply_verificador(...).
    """
    src = Path("ui/controllers/app_controller.py").read_text()
    i = src.index("def _choose_workspace")
    j = src.index("def ", i + 20)
    body = src[i:j]

    # Debe contener _rebuild_composite() y _apply_verificador(...)
    # en ese orden.
    idx_rebuild = body.index("self._rebuild_composite()")
    idx_apply = body.index("self._apply_verificador")
    assert idx_apply > idx_rebuild, (
        "_apply_verificador debe ir despues de _rebuild_composite"
    )
