"""Main window: header, Tuning tab (main) and Hardware tab."""

from __future__ import annotations

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QTabWidget,
                             QVBoxLayout, QWidget)

from haffop import APP_NAME, __version__
from haffop.hardware import get_backend as hardware_backend
from haffop.platform_info import WINDOWS, PlatformInfo
from haffop.tuning import get_backend as tuning_backend
from haffop.ui.hardware_tab import HardwareTab
from haffop.ui.theme import ACCENT, WARN
from haffop.ui.tuning_tab import TuningTab
from haffop.ui.widgets import muted
from haffop.ui.workers import Job, SensorPoller
from haffop.util import is_admin, resource_path


class MainWindow(QMainWindow):
    def __init__(self, plat: PlatformInfo) -> None:
        super().__init__()
        self.plat = plat
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.setWindowIcon(QIcon(str(resource_path("resources", "haffop.svg"))))
        self.resize(1200, 820)
        self.poller: SensorPoller | None = None

        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._header())
        self.loading = QLabel("Detecting hardware…")
        self.loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading.setStyleSheet("font-size: 14pt; color: #9aa0a6;")
        lay.addWidget(self.loading, 1)
        self.tabs = QTabWidget()
        self.tabs.hide()
        lay.addWidget(self.tabs, 1)
        self.setCentralWidget(root)
        self.statusBar().showMessage(f"{plat.pretty_name}")

        self.hw = hardware_backend()
        self._job = Job(self._detect, self)
        self._job.done.connect(self._detected)
        self._job.failed.connect(self._detect_failed)
        self._job.start()

    def _header(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("Header")
        h = QHBoxLayout(bar)
        h.setContentsMargins(18, 10, 18, 10)
        icon = QLabel()
        icon.setPixmap(QIcon(str(resource_path("resources", "haffop.svg"))).pixmap(34, 34))
        h.addWidget(icon)
        title = QLabel(APP_NAME)
        title.setObjectName("AppTitle")
        h.addWidget(title)
        h.addWidget(muted(f"  hardware viewer & performance unlocker  ·  {self.plat.pretty_name}"), 1)
        self.system_label = muted("")
        h.addWidget(self.system_label)
        if self.plat.edition == WINDOWS and not is_admin():
            lbl = QLabel("Not running as administrator")
            lbl.setStyleSheet(f"color: {WARN};")
            h.addWidget(lbl)
            btn = QPushButton("Restart as administrator")
            btn.setObjectName("Primary")
            btn.clicked.connect(self._relaunch_admin)
            h.addWidget(btn)
        elif self.plat.edition != WINDOWS and not is_admin():
            note = muted("  ·  changes ask for your password")
            note.setWordWrap(False)
            h.addWidget(note)
        else:
            ok = QLabel("  Administrator")
            ok.setStyleSheet(f"color: {ACCENT};")
            h.addWidget(ok)
        return bar

    def _detect(self):
        snap = self.hw.detect()
        tuner = tuning_backend(snap, self.plat)
        return snap, tuner, tuner.components()

    def _detected(self, res) -> None:
        snap, tuner, comps = res
        self.loading.hide()
        self.system_label.setText(snap.system_name + ("  ·  laptop" if snap.is_laptop else "  ·  desktop"))
        self.system_label.setWordWrap(False)
        self.tuning = TuningTab(snap, tuner, comps)
        self.hardware = HardwareTab(snap)
        self.tabs.addTab(self.tuning, "Tuning")
        self.tabs.addTab(self.hardware, "Hardware")
        self.tabs.show()
        self.poller = SensorPoller(self.hw, 3000 if self.plat.edition == WINDOWS else 2000, self)
        self.poller.readings.connect(self.tuning.update_sensors)
        self.poller.readings.connect(self.hardware.update_sensors)
        self.poller.start()

    def _detect_failed(self, tb: str) -> None:
        self.loading.setText("Hardware detection failed.")
        QMessageBox.critical(self, "Detection failed", tb)

    def _relaunch_admin(self) -> None:
        import ctypes
        if getattr(sys, "frozen", False):
            exe, params = sys.executable, " ".join(sys.argv[1:])
        else:
            exe, params = sys.executable, "-m haffop"
        if ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1) > 32:
            self.close()

    def closeEvent(self, event) -> None:
        if self.poller:
            self.poller.stop()
        super().closeEvent(event)
