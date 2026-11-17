"""Application entry point."""

from __future__ import annotations

import sys


def main() -> int:
    if sys.argv[1:2] == ["--haffop-test-worker"]:
        from haffop.diagnostics import worker_main
        return worker_main(sys.argv[2:])
    # Bundled (PyInstaller) builds have no separate Python, so the binary doubles as its own pkexec helper.
    if sys.argv[1:2] == ["--haffop-helper"]:
        from haffop.tuning import helper
        return helper.main()
    try:
        from PyQt6.QtGui import QIcon
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        print("PyQt6 is required. Install it with your package manager (python3-pyqt6) or 'pip install PyQt6'.",
              file=sys.stderr)
        return 1

    from haffop import APP_NAME
    from haffop.platform_info import detect
    from haffop.ui.main_window import MainWindow
    from haffop.ui.theme import STYLESHEET
    from haffop.util import resource_path

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName("haffop")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    app.setWindowIcon(QIcon(str(resource_path("resources", "haffop.svg"))))
    win = MainWindow(detect())
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
