"""Benchmark/test controls, cancellable workers, reports and offline references."""

from __future__ import annotations

import csv
from dataclasses import asdict
import json
from pathlib import Path
import threading
import time
from typing import Callable

import psutil
from PyQt6.QtCore import QStandardPaths, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QMessageBox, QProgressBar, QPushButton, QScrollArea, QSpinBox,
                             QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from haffop.diagnostics import COMPONENTS, DURATIONS, TestRunner, compare
from haffop.hardware.model import HardwareSnapshot, SensorReading
from haffop.ui.widgets import Card, muted
from haffop.util import resource_path

LABELS = {"cpu": "CPU - verified SHA-256 throughput", "ram": "RAM - copy and verify",
          "storage": "SSD / storage - temporary file read-back", "gpu": "GPU - OpenCL integer compute",
          "network": "Wi-Fi / Ethernet - iperf3 TCP throughput"}
DESCRIPTIONS = {
    "cpu": "Measures CPU crypto throughput using independent worker processes. Not a gaming or general CPU rating.",
    "ram": "Copies and compares 32 MiB of buffers. A limited integrity check, not a full-memory test or rated MT/s measurement.",
    "storage": "Writes 64 MiB once, fsyncs, then reads/verifies until time expires. OS caching affects the score. "
               "Choose a folder on the drive you intend to test. No raw device writes.",
    "gpu": "Requires the GPU vendor's OpenCL driver. Measures integer compute and verifies output samples. "
           "Does not substitute CPU rendering if unsupported. OpenCL indices can differ from OS GPU indices; "
           "the actual device name appears in results.",
    "network": "Requires iperf3 installed locally and 'iperf3 -s' on a trusted LAN peer. "
               "Enter that peer below. Uses OS routing: disable Ethernet/select a Wi-Fi route to test Wi-Fi. "
               "Generates network traffic for the entire duration; not an internet speed test.",
    "all": "Runs CPU, RAM, storage, GPU and network sequentially, each for the selected duration. "
           "Unsupported components are explicitly marked unavailable.",
    "stress": "Runs CPU, RAM, storage and GPU simultaneously. Storage writes only 64 MiB, then repeats cached reads; "
              "this is a bounded mixed workload, not maximum load on every component. Network traffic is excluded.",
}


class BenchmarkWorker(QThread):
    progress = pyqtSignal(int, str)
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, config: dict, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.cancel = threading.Event()

    def run(self) -> None:
        try:
            result = TestRunner(self.cancel, self.progress.emit).run(**self.config)
            self.done.emit(result)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class ReferencesTab(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        title = QLabel("Offline public references")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        layout.addWidget(muted(
            "Historical community results, September 2021. These are not factory standards. "
            "Exact workload settings/units are missing from the source tables, so these numbers "
            "cannot rate Haffop's different workloads. Newer hardware may not appear."))
        row = QHBoxLayout()
        self.profile = QComboBox()
        for label, profile in (("CPU - OpenSSL", "pts-openssl"), ("CPU - Sysbench", "pts-sysbench"),
                               ("RAM - RAMspeed", "pts-ramspeed"), ("GPU - glmark2", "pts-glmark2"),
                               ("Storage - fio", "pts-fio")):
            self.profile.addItem(label, profile)
        row.addWidget(self.profile)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search hardware model (clear to browse all)")
        self.search.setClearButtonEnabled(True)
        row.addWidget(self.search, 1)
        layout.addLayout(row)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Hardware / configuration", "Published value (source units unspecified)", "Source count"])
        self.tree.setColumnWidth(0, 430)
        self.tree.setColumnWidth(1, 320)
        self.tree.setAlternatingRowColors(True)
        layout.addWidget(self.tree, 1)
        self.credit = muted("Source: X; Michael Larabel (Phoronix), Zenodo 5535465 (2021). CC BY 4.0. "
                            "Unchanged table extracts; no endorsement. All browsing works offline.")
        layout.addWidget(self.credit)
        notice = QPushButton("Source, license & limitations")
        notice.clicked.connect(lambda: QMessageBox.information(
            self, "Offline dataset attribution",
            resource_path("resources", "references", "NOTICE.txt").read_text(encoding="utf-8")))
        layout.addWidget(notice)
        self.profile.currentIndexChanged.connect(self.refresh)
        self.search.textChanged.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        self.tree.clear()
        path = resource_path("resources", "references", self.profile.currentData() + ".csv")
        try:
            with path.open(encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    if self.search.text().lower().strip() in row["descproc"].lower():
                        self.tree.addTopLevelItem(QTreeWidgetItem([row["descproc"], row["perf"], row["nbproc"]]))
        except (OSError, KeyError, csv.Error) as exc:
            QMessageBox.warning(self, "Reference data unavailable", str(exc))


class BenchmarksTab(QScrollArea):
    busy_changed = pyqtSignal(bool)

    def __init__(self, snap: HardwareSnapshot, testing: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.snap = snap
        self.worker: BenchmarkWorker | None = None
        self.report: dict | None = None
        self.baseline: dict | None = None
        self.last_sensors = 0.0
        self.readings: list[SensorReading] = []
        self.required_sensors: set[str] = set()
        self.peak: dict[str, float] = {}
        self.stop_reason = ""
        self.start_check: Callable[[], bool] = lambda: True
        self.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)
        title = QLabel("Component testing & stress" if testing else "Component benchmarks")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        layout.addWidget(muted(
            "Repeatable, local measurements. Close background apps and keep the same power/thermal settings "
            "for meaningful comparisons. No tuning settings are changed and no results are uploaded."))
        self.controls = Card("Configure a run")
        form = QFormLayout()
        self.component = QComboBox()
        for key in COMPONENTS:
            self.component.addItem(LABELS[key], key)
        self.component.addItem("All component benchmarks (sequential)", "all")
        if testing:
            self.component.addItem("Overall stress test (simultaneous)", "stress")
        form.addRow("Workload", self.component)
        self.description = muted("")
        form.addRow(self.description)
        self.duration = QComboBox()
        for value, label in zip(DURATIONS, ("30 seconds", "1 minute", "5 minutes", "10 minutes", "30 minutes")):
            self.duration.addItem(label, value)
        form.addRow("Duration per component", self.duration)
        self.workers = QSpinBox()
        self.workers.setRange(1, min(32, psutil.cpu_count() or 1))
        self.workers.setValue(min(4, self.workers.maximum()))
        form.addRow("CPU workers (advanced)", self.workers)
        self.gpu_index = QSpinBox()
        self.gpu_index.setRange(0, 31)
        form.addRow("OpenCL GPU index (advanced)", self.gpu_index)
        folder_row = QHBoxLayout()
        self.folder = QLineEdit(str(Path.home()))
        folder_row.addWidget(self.folder, 1)
        browse = QPushButton("Choose drive folder...")
        browse.clicked.connect(self._folder)
        folder_row.addWidget(browse)
        form.addRow("Storage target", folder_row)
        self.host = QLineEdit()
        self.host.setPlaceholderText("Trusted LAN iperf3 server (optional except for network test)")
        form.addRow("Network peer", self.host)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(5201)
        form.addRow("iperf3 TCP port", self.port)
        self.controls.layout_.addLayout(form)
        layout.addWidget(self.controls)
        buttons = QHBoxLayout()
        self.start = QPushButton("Start test" if testing else "Run benchmark")
        self.start.setObjectName("Primary")
        self.stop = QPushButton("Stop")
        self.stop.setObjectName("Danger")
        self.stop.setEnabled(False)
        buttons.addWidget(self.start)
        buttons.addWidget(self.stop)
        buttons.addStretch()
        self.save = QPushButton("Export results...")
        self.save.setEnabled(False)
        self.load = QPushButton("Load saved baseline...")
        buttons.addWidget(self.load)
        buttons.addWidget(self.save)
        layout.addLayout(buttons)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.status = muted("Ready. All tests are opt-in. Unsupported drivers/tools are reported, not faked.")
        layout.addWidget(self.status)
        self.results = QTreeWidget()
        self.results.setHeaderLabels(["Component / worker", "Status", "Measured score", "Details / baseline comparison"])
        self.results.setColumnWidth(0, 160)
        self.results.setColumnWidth(1, 110)
        self.results.setColumnWidth(2, 200)
        self.results.setMinimumHeight(230)
        self.results.setWordWrap(True)
        self.results.setAlternatingRowColors(True)
        layout.addWidget(self.results)
        layout.addWidget(muted(
            "Safety guard: stops on reported CPU >=90 C, GPU >=85 C or drive >=70 C, lost known "
            "temperature sensors, or telemetry older than 25 s. Missing sensors cannot protect the device. "
            "Stop immediately if the system becomes unstable. Long tests increase heat and power use. "
            "Results measure only this workload; a pass does not certify component health or factory performance."))
        layout.addStretch()
        self.setWidget(body)
        self.component.currentIndexChanged.connect(self._selection)
        self.start.clicked.connect(self._run)
        self.stop.clicked.connect(self.cancel)
        self.save.clicked.connect(self._save)
        self.load.clicked.connect(self._load)
        self.watchdog = QTimer(self)
        self.watchdog.setInterval(1000)
        self.watchdog.timeout.connect(self._guard)
        self._selection()

    def is_running(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def _selection(self) -> None:
        key = self.component.currentData()
        self.description.setText(DESCRIPTIONS[key])
        self.host.setEnabled(key in ("network", "all"))
        self.port.setEnabled(key in ("network", "all"))
        self.gpu_index.setEnabled(key in ("gpu", "all", "stress"))
        self.workers.setEnabled(key in ("cpu", "all", "stress"))

    def _folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder on the drive to test", self.folder.text())
        if folder:
            self.folder.setText(folder)

    def _run(self) -> None:
        if not self.start_check():
            QMessageBox.warning(self, "Tuning in progress", "Wait for the current tuning change to finish before testing.")
            return
        key = self.component.currentData()
        components = list(COMPONENTS) if key == "all" else (list(COMPONENTS[:-1]) if key == "stress" else [key])
        directory = Path(self.folder.text()).expanduser()
        if "storage" in components and not directory.is_dir():
            QMessageBox.warning(self, "Storage target", "Choose an existing folder on the drive to test.")
            return
        if key == "network" and not self.host.text().strip():
            QMessageBox.warning(self, "Network target", "Enter your trusted LAN iperf3 server.")
            return
        if time.monotonic() - self.last_sensors > 25:
            QMessageBox.warning(self, "Telemetry unavailable", "Wait for a successful live sensor update before starting.")
            return
        hot = self._hot_sensor()
        if hot:
            QMessageBox.warning(self, "Temperature too high", hot)
            return
        warning = (
            f"Run {self.component.currentText()} for {self.duration.currentText()} per component?\n\n"
            "This creates load, heat and power use. Save your work and supervise the test. "
            "No power, voltage or clock limits will be changed.\n\n"
            "CPU/GPU temperature sensors may be unavailable; the safety guard cannot detect unreported heat. "
            "Storage writes a 64 MiB temporary file only. Network tests send traffic to the configured peer.\n\n"
            "Continue?")
        if QMessageBox.question(self, "Confirm diagnostic workload", warning,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        identity = {"cpu": asdict(self.snap.cpu), "ram_total": self.snap.ram_total,
                    "ram_modules": [asdict(m) for m in self.snap.ram_modules],
                    "gpus": [asdict(g) for g in self.snap.gpus], "disks": [asdict(d) for d in self.snap.disks]}
        config = {"components": components, "seconds": self.duration.currentData(), "workers": self.workers.value(),
                  "directory": str(directory), "gpu_index": self.gpu_index.value(), "host": self.host.text().strip(),
                  "port": self.port.value(), "combined": key == "stress", "identity": identity}
        self.required_sensors = {r.key for r in self.readings if r.unit == "°C" and r.key
                                 and (r.key == "cpu" or r.key.startswith(("gpu:", "disk:")))}
        self.peak = {}
        self.stop_reason = ""
        self.results.clear()
        self.report = None
        self.progress.setValue(0)
        self.status.setText("Starting isolated workers...")
        self.worker = BenchmarkWorker(config, self)
        self.worker.progress.connect(self._progress)
        self.worker.done.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._finished)
        self.controls.setEnabled(False)
        self.start.setEnabled(False)
        self.stop.setEnabled(True)
        self.save.setEnabled(False)
        self.load.setEnabled(False)
        self.busy_changed.emit(True)
        self.watchdog.start()
        self.worker.start()

    def _progress(self, value: int, message: str) -> None:
        self.progress.setValue(value)
        if not self.stop_reason:
            self.status.setText(message)

    def cancel(self, reason: str | bool = "Stopped by user.") -> None:
        if self.is_running():
            self.stop_reason = reason if isinstance(reason, str) else "Stopped by user."
            self.status.setText(self.stop_reason + " Cleaning up workers and temporary files...")
            self.worker.cancel.set()
            self.stop.setEnabled(False)

    def _hot_sensor(self) -> str:
        for reading in self.readings:
            if reading.unit != "°C" or not reading.key:
                continue
            limit = 90 if reading.key == "cpu" else 85 if reading.key.startswith("gpu:") else 70 if reading.key.startswith("disk:") else None
            if limit is not None and reading.value >= limit:
                return f"Safety stop: {reading.group} / {reading.label}: {reading.value:.1f} C (limit {limit} C)."
        return ""

    def _guard(self) -> None:
        if not self.is_running():
            return
        keys = {r.key for r in self.readings if r.unit == "°C"}
        reason = self._hot_sensor()
        if self.required_sensors - keys:
            reason = "Safety stop: a previously available temperature sensor was lost."
        if time.monotonic() - self.last_sensors > 25:
            reason = "Safety stop: live telemetry is more than 25 seconds old."
        if reason:
            self.cancel(reason)

    def update_sensors(self, readings: list[SensorReading]) -> None:
        self.readings = readings
        if readings:
            self.last_sensors = time.monotonic()
        elif self.is_running():
            self.cancel("Safety stop: sensor polling failed.")
        if self.is_running():
            for r in readings:
                if r.unit == "°C":
                    key = f"{r.group}: {r.label}"
                    self.peak[key] = max(self.peak.get(key, r.value), r.value)
            self._guard()

    def _done(self, report: dict) -> None:
        report["stop_reason"] = self.stop_reason
        report["peak_temperatures_c"] = self.peak
        self.report = report
        self._render()
        complete = not report["cancelled"]
        statuses = {r["status"] for r in report["results"]}
        self.progress.setValue(100 if complete else self.progress.value())
        self.status.setText(self.stop_reason or
                            ("Run completed with failed/unavailable components. See details." if statuses != {"completed"}
                             else "Workloads completed and output verified. This is not a full hardware health certification."))
        try:
            root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
            root.mkdir(parents=True, exist_ok=True)
            path = root / "latest-diagnostic.json"
            self._write(path)
            self.status.setText(self.status.text() + f" Report saved to {path}")
        except OSError as exc:
            QMessageBox.warning(self, "Report not saved", str(exc))

    def _failed(self, message: str) -> None:
        self.status.setText("Diagnostic run failed: " + message)
        QMessageBox.warning(self, "Diagnostic run failed", message)

    def _finished(self) -> None:
        self.watchdog.stop()
        self.controls.setEnabled(True)
        self.start.setEnabled(True)
        self.stop.setEnabled(False)
        self.save.setEnabled(self.report is not None)
        self.load.setEnabled(True)
        self.busy_changed.emit(False)
        self.worker.deleteLater()
        self.worker = None

    def _render(self) -> None:
        self.results.clear()
        if not self.report:
            return
        for row in self.report["results"]:
            score = f"{row['score']:.2f} {row['unit']}" if "score" in row else "n/a"
            detail = row.get("detail", "")
            if row.get("device"):
                detail = row["device"] + ": " + detail
            if self.baseline is not None:
                _, comparison = compare(self.report, self.baseline, row["component"])
                detail += " | " + comparison
            item = QTreeWidgetItem([row["component"].upper(), row["status"], score, detail])
            item.setToolTip(3, detail)
            self.results.addTopLevelItem(item)

    def _write(self, path: Path) -> None:
        from PyQt6.QtCore import QIODevice, QSaveFile
        output = QSaveFile(str(path))
        if not output.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(output.errorString())
        data = json.dumps(self.report, indent=2).encode("utf-8")
        if output.write(data) != len(data) or not output.commit():
            raise OSError(output.errorString())

    def _save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export diagnostic results", "haffop-results.json", "JSON (*.json)")
        if path:
            try:
                self._write(Path(path))
            except OSError as exc:
                QMessageBox.warning(self, "Export failed", str(exc))

    def _load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load a Haffop baseline", "", "JSON (*.json)")
        if not path:
            return
        try:
            if Path(path).stat().st_size > 4 * 1024**2:
                raise ValueError("Baseline file exceeds the 4 MiB limit.")
            baseline = json.loads(Path(path).read_text(encoding="utf-8"))
            if (not isinstance(baseline, dict) or baseline.get("schema") != 1
                    or not isinstance(baseline.get("results"), list)
                    or any(not isinstance(row, dict) for row in baseline["results"])):
                raise ValueError("Not a Haffop diagnostic report.")
            self.baseline = baseline
            self.status.setText(f"Baseline loaded: {path}. Only matching workloads/settings will be compared.")
            self._render()
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Invalid baseline", str(exc))
