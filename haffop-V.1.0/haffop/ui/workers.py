"""Background threads: one-shot jobs and the live sensor poller."""

from __future__ import annotations

import traceback
from typing import Any, Callable

from PyQt6.QtCore import QThread, pyqtSignal


class Job(QThread):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn: Callable[[], Any], parent=None) -> None:
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self._fn())
        except Exception:
            self.failed.emit(traceback.format_exc())


class SensorPoller(QThread):
    readings = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, backend, interval_ms: int, parent=None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._interval = interval_ms
        self._stop = False

    def run(self) -> None:
        while not self._stop:
            try:
                self.readings.emit(self._backend.read_sensors())
            except Exception as exc:
                self.readings.emit([])
                self.failed.emit(f"Sensor polling failed: {exc}")
            for _ in range(self._interval // 100):
                if self._stop:
                    return
                self.msleep(100)

    def stop(self, timeout_ms: int = 5000) -> bool:
        self._stop = True
        return self.wait(timeout_ms)
