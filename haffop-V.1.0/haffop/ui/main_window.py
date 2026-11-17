"""Shared health, hardware, benchmarking and tuning workspace."""

from __future__ import annotations

import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QTabWidget,
                             QVBoxLayout, QWidget)

from haffop import APP_NAME, __version__
from haffop.hardware import get_backend as hardware_backend
from haffop.platform_info import WINDOWS, PlatformInfo
from haffop.tuning import get_backend as tuning_backend
from haffop.ui.hardware_tab import HardwareTab
from haffop.ui.health_tab import HealthTab
from haffop.ui.benchmarks_tab import BenchmarksTab, ReferencesTab
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
        self._closing = False
        self._last_sensor_update = 0.0

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
        h.addWidget(muted(f"  {__version__}  ·  health & performance  ·  {self.plat.pretty_name}"), 1)
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
        self.health = HealthTab(snap)
        self.benchmarks = BenchmarksTab(snap)
        self.testing = BenchmarksTab(snap, testing=True)
        self.tabs.addTab(self.health, "Health")
        self.tabs.addTab(self.hardware, "Hardware")
        self.tabs.addTab(self.benchmarks, "Benchmarks")
        self.tabs.addTab(self.testing, "Testing")
        self.tabs.addTab(ReferencesTab(), "Offline references")
        self.tabs.addTab(self.tuning, "Tuning")
        for page, other in ((self.benchmarks, self.testing), (self.testing, self.benchmarks)):
            page.busy_changed.connect(other.setDisabled)
            page.busy_changed.connect(self.tuning.setDisabled)
            page.start_check = lambda: self.tuning._job is None or not self.tuning._job.isRunning()
        self.tabs.show()
        self.poller = SensorPoller(self.hw, 3000 if self.plat.edition == WINDOWS else 2000, self)
        self.poller.readings.connect(self._sensors)
        self.poller.failed.connect(lambda message: self.statusBar().showMessage(message))
        self.poller.start()
        self._stale_timer = QTimer(self)
        self._stale_timer.setInterval(1000)
        self._stale_timer.timeout.connect(self._expire_sensors)
        self._stale_timer.start()

    def _sensors(self, readings) -> None:
        import time
        self._last_sensor_update = time.monotonic() if readings else 0
        for page in (self.health, self.tuning, self.hardware, self.benchmarks, self.testing):
            page.update_sensors(readings)
        if readings:
            self.statusBar().showMessage(f"{self.plat.pretty_name}  ·  Live telemetry updated")

    def _expire_sensors(self) -> None:
        import time
        if self._last_sensor_update and time.monotonic() - self._last_sensor_update > 25:
            self._sensors([])
            self.statusBar().showMessage("Telemetry stale: readings cleared. Check sensor access.")

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
        for name in ("benchmarks", "testing"):
            page = getattr(self, name, None)
            if page and page.is_running():
                event.ignore()
                if self._closing:
                    QTimer.singleShot(200, self.close)
                    return
                if QMessageBox.question(self, "Stop diagnostics?",
                                        "Stop the active workload and close after cleanup?",
                                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                        QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
                    self._closing = True
                    page.cancel()
                    QTimer.singleShot(200, self.close)
                return
        self._closing = True
        self.tabs.setEnabled(False)
        if hasattr(self, "health"):
            self.health.timer.stop()
        jobs = [self._job]
        if hasattr(self, "health") and self.health._job:
            jobs.append(self.health._job)
        if hasattr(self, "tuning") and self.tuning._job:
            jobs.append(self.tuning._job)
        for job in jobs:
            if job.isRunning():
                event.ignore()
                QTimer.singleShot(200, self.close)
                self.statusBar().showMessage("Waiting for the current hardware operation before closing...")
                return
        if self.poller:
            if not self.poller.stop(0):
                event.ignore()
                QTimer.singleShot(200, self.close)
                return
        super().closeEvent(event)
