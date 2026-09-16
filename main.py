from PySide6.QtWidgets import QApplication

from ui.controllers.app_controller import AppController
from ui.theme import DARK_STYLE
from ui.views.main_window import MainWindow


def main() -> int:
    app = QApplication([])
    app.setStyleSheet(DARK_STYLE)

    view = MainWindow()
    controller = AppController(view)
    view.show()

    exit_code = app.exec()
    controller.shutdown()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
