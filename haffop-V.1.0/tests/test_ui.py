import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time

import pytest
from PyQt6.QtWidgets import QApplication

from haffop.hardware.model import GPUInfo, HardwareSnapshot, SensorReading
from haffop.ui.benchmarks_tab import BenchmarksTab, ReferencesTab
from haffop.ui.hardware_tab import ExpandedView, SimpleView


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def test_iGPU_is_not_cpu_temperature_and_stale_values_clear(app):
    view = SimpleView(HardwareSnapshot(gpus=[GPUInfo("igpu", "Integrated GPU", "intel", True)]))
    view.update_sensors([SensorReading("CPU", "Package", 65, "°C", "cpu")])
    assert view.stats["gpu:igpu"]._values["temp"].text() == "n/a"
    assert view.stats["cpu"]._values["temp"].text() == "65 °C"
    view.update_sensors([])
    assert view.stats["cpu"]._values["temp"].text() == "n/a"


def test_expanded_temperatures_update_and_clear(app):
    view = ExpandedView(HardwareSnapshot())
    view.update_sensors([SensorReading("CPU", "Package", 65, "°C", "cpu")])
    assert view._sensor_items["CPU/Package/°C"].text(1) == "65.0 °C"
    view.update_sensors([])
    assert view._sensor_items["CPU/Package/°C"].text(1) == "n/a (not reported)"


def test_guard_stops_on_heat_sensor_loss_and_staleness(app):
    class Worker:
        cancel = threading.Event()

        def isRunning(self):
            return True

    view = BenchmarksTab(HardwareSnapshot())
    view.worker = Worker()
    view.last_sensors = time.monotonic()
    view.update_sensors([SensorReading("CPU", "Package", 91, "°C", "cpu")])
    assert view.worker.cancel.is_set()
    assert "limit 90" in view.stop_reason
    view.worker.cancel.clear()
    view.required_sensors = {"cpu"}
    view.update_sensors([SensorReading("CPU", "Load", 10, "%", "cpu_load")])
    assert view.worker.cancel.is_set()
    assert "lost" in view.stop_reason
    view.worker.cancel.clear()
    view.required_sensors.clear()
    view.last_sensors = time.monotonic() - 26
    view._guard()
    assert view.worker.cancel.is_set()
    assert "25 seconds" in view.stop_reason
    view.worker = None


def test_all_offline_profiles_are_packaged_and_searchable(app):
    references = ReferencesTab()
    for index in range(references.profile.count()):
        references.profile.setCurrentIndex(index)
        assert references.tree.topLevelItemCount() > 0
    references.search.setText("not-a-real-hardware-model")
    assert references.tree.topLevelItemCount() == 0


def test_results_save_atomically_and_can_be_read_back(app, tmp_path):
    import json
    view = BenchmarksTab(HardwareSnapshot())
    view.report = {"schema": 1, "results": [], "cancelled": True}
    path = tmp_path / "results.json"
    view._write(path)
    assert json.loads(path.read_text()) == view.report
    assert list(tmp_path.iterdir()) == [path]


def test_main_window_streams_sensors_and_waits_for_cancelled_worker(app, monkeypatch):
    from PyQt6.QtCore import QThread
    from PyQt6.QtWidgets import QMessageBox
    from haffop.platform_info import FEDORA, PlatformInfo
    from haffop.ui import main_window

    class Hardware:
        def detect(self):
            return HardwareSnapshot()

        def read_sensors(self):
            return [SensorReading("CPU", "Package", 45, "°C", "cpu")]

    class Tuner:
        def components(self):
            return []

    class SlowWorker(QThread):
        def __init__(self, parent):
            super().__init__(parent)
            self.cancel = threading.Event()

        def run(self):
            self.cancel.wait(3)
            self.msleep(350)

    def wait_until(predicate):
        deadline = time.monotonic() + 4
        while not predicate() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert predicate()

    monkeypatch.setattr(main_window, "hardware_backend", Hardware)
    monkeypatch.setattr(main_window, "tuning_backend", lambda *_: Tuner())
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window = main_window.MainWindow(PlatformInfo(FEDORA, "Test"))
    window.show()
    try:
        wait_until(lambda: window.tabs.count() == 6 and
                   window.hardware.simple.stats["cpu"]._values["temp"].text() == "45 °C")
        page = window.testing
        page.worker = SlowWorker(page)
        page.worker.finished.connect(page._finished)
        page.worker.start()
        window.close()
        assert window.isVisible()
        wait_until(lambda: not window.isVisible())
        assert page.worker is None
        assert not window.poller.isRunning()
    finally:
        if hasattr(window, "health"):
            window.health.timer.stop()
            if window.health._job:
                window.health._job.wait()
        if window.poller:
            window.poller.stop()
        app.processEvents()
