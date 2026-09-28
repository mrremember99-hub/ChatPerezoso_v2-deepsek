"""RightPanel v2: MCP, COLA y ARCHIVOS envueltos en cards.

Hereda de RightPanel. Las secciones las sigue construyendo la
clase padre (via los _build_*_section extraidos en el refactor),
aqui solo las envolvemos en QFrame#Card con CardTitle.

Diferencia visual con v1: tres cards con borde ambar y titulo
ambar claro, en vez de QLabels de seccion planos sobre el
fondo del panel.

Diferencia funcional minima: la card de COLA se oculta entera
cuando no hay prompts. En v1 solo se ocultaba el titulo, dejando
el resto invisible pero presente; con cards es mas limpio
ocultar la card entera. Se overridea set_queue_list.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from .. import theme_v2
from .right_panel import RightPanel


class RightPanelV2(RightPanel):
    """RightPanel con las tres secciones en cards ambar.

    Reutiliza toda la API publica y las senales de RightPanel.
    Solo cambia la construccion del arbol de widgets: en vez de
    QLabels de seccion planos, tres QFrame#Card con CardTitle.
    """

    # Se asigna en _build(). Declarado a nivel de clase para que
    # set_queue_list pueda consultarlo sin riesgo incluso si algo
    # lo llamara antes de tiempo.
    _queue_card: QFrame | None = None

    # -- construccion ---------------------------------------------------

    def _build(self) -> None:
        # v1 fija el ancho a design.SIDEBAR_WIDTH_PX (260). El
        # boceto v2 usa 310 (theme_v2.RIGHT_PANEL_WIDTH) y el
        # splitter ya pide 310 al repartir. Sin este ajuste, v2
        # se queda en 260.
        self.setFixedWidth(theme_v2.RIGHT_PANEL_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        layout.addWidget(
            self._make_card("MCP", self._build_mcp_section)
        )

        self._queue_card = self._make_card(
            "COLA DE PROMPTS", self._build_queue_section
        )
        # Igual que v1: la cola arranca oculta hasta que haya
        # prompts.
        self._queue_card.setVisible(False)
        layout.addWidget(self._queue_card)

        layout.addWidget(
            self._make_card("ARCHIVOS", self._build_files_section),
            1,
        )

    def _make_card(
        self,
        title: str,
        build_fn: Callable[[QVBoxLayout], None],
    ) -> QFrame:
        """Envuelve una seccion v1 en QFrame#Card + CardTitle.

        `build_fn` recibe el QVBoxLayout interno de la card y
        construye alli los widgets de la seccion (los _build_*_
        section de RightPanel aceptan cualquier QVBoxLayout).
        """
        card = QFrame()
        card.setObjectName("Card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 6, 10, 10)
        card_layout.setSpacing(6)

        title_label = QLabel(title)
        title_label.setObjectName("CardTitle")
        card_layout.addWidget(title_label)

        build_fn(card_layout)
        return card

    # -- override de set_queue_list -------------------------------------

    def set_queue_list(self, prompts: list[str]) -> None:
        super().set_queue_list(prompts)
        # El queue_title interno duplica el CardTitle. Se mantiene
        # siempre oculto; la visibilidad efectiva la controla la
        # card.
        self.queue_title.setVisible(False)
        if self._queue_card is not None:
            self._queue_card.setVisible(bool(prompts))
