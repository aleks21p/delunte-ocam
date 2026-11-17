"""Read-only health overview; missing telemetry is never reported as healthy."""

from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QGridLayout, QLabel, QScrollArea, QVBoxLayout, QWidget
import psutil

from haffop.hardware.model import HardwareSnapshot, SensorReading
from haffop.ui.widgets import Card, StatGrid, muted
from haffop.ui.workers import Job
from haffop.util import human_bytes


class HealthTab(QScrollArea):
    def __init__(self, snap: HardwareSnapshot, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)
        title = QLabel("Your system at a glance")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        layout.addWidget(muted(snap.system_name + "  |  " + snap.cpu.name))
        grid = QGridLayout()
        grid.setSpacing(16)
        self.stats = {}
        for i, (key, title, subtitle) in enumerate((
                ("cpu", "Processor", f"{snap.cpu.cores} cores / {snap.cpu.threads} threads"),
                ("ram", "Memory", human_bytes(snap.ram_total)),
                ("thermal", "Thermal telemetry", "Actual device sensors only; no estimated CPU or GPU temperatures."),
                ("power", "Battery & power", "Charge state is not a battery health/capacity rating."))):
            card = Card(title, subtitle)
            stats = StatGrid()
            card.add(stats)
            self.stats[key] = stats
            grid.addWidget(card, i // 2, i % 2)
        layout.addLayout(grid)
        storage = Card("Filesystem & network checks",
                       "Free space, interface state and driver counters are diagnostics, not SSD SMART or Wi-Fi speed ratings.")
        self.checks = StatGrid()
        storage.add(self.checks)
        layout.addWidget(storage)
        notes = Card("Coverage & next steps")
        notes.add(muted("Benchmarks measure repeatable workloads. Testing runs component checks or a simultaneous "
                        "stress workload. A completed test is not a guarantee of hardware health. "
                        "Use vendor SMART tools and a bootable memory tester for comprehensive drive/RAM diagnostics."))
        for warning in snap.warnings:
            notes.add(muted(warning))
        layout.addWidget(notes)
        layout.addStretch()
        self.setWidget(body)
        self._job: Job | None = None
        self.update_sensors([])
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def refresh(self) -> None:
        if self._job:
            return
        self._job = Job(self._collect, self)
        self._job.done.connect(self._refreshed)
        self._job.failed.connect(lambda message: self.checks.set("error", message, "Health query failed"))
        self._job.finished.connect(self._finished)
        self._job.start()

    @staticmethod
    def _collect() -> dict:
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        checks = []
        for partition in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(partition.mountpoint)
                value = f"{human_bytes(usage.free)} free; {usage.percent:.0f}% used"
            except OSError as exc:
                value = f"Unavailable: {exc}"
            checks.append(("disk:" + partition.mountpoint, value, partition.mountpoint))
        counters = psutil.net_io_counters(pernic=True)
        for name, state in psutil.net_if_stats().items():
            io = counters.get(name)
            detail = f"{state.speed} Mbit/s reported link" if state.speed > 0 else "link speed unavailable"
            if io:
                detail += f"; errors {io.errin + io.errout}; drops {io.dropin + io.dropout} (since boot)"
            checks.append(("net:" + name, f"{'Up' if state.isup else 'Down'}; {detail}", name))
        return {"memory": memory, "swap": swap, "battery": psutil.sensors_battery(), "checks": checks}

    def _refreshed(self, data: dict) -> None:
        memory, swap, battery = data["memory"], data["swap"], data["battery"]
        self.stats["ram"].set("used", f"{memory.percent:.0f}% ({human_bytes(memory.available)} available)", "Usage")
        self.stats["ram"].set("swap", f"{swap.percent:.0f}%", "Swap usage")
        self.checks.clear_values()
        for key, value, label in data["checks"]:
            self.checks.set(key, value, label)
        self.stats["power"].set("state", "No battery reported" if battery is None else
                                ("Plugged in" if battery.power_plugged else "On battery"), "Power")
        self.stats["power"].set("charge", f"{battery.percent:.0f}%" if battery else "n/a", "Charge")

    def _finished(self) -> None:
        self._job.deleteLater()
        self._job = None

    def update_sensors(self, readings: list[SensorReading]) -> None:
        keyed = {r.key: r for r in readings if r.key}
        cpu = keyed.get("cpu")
        self.stats["cpu"].set_temp("temperature", cpu.value if cpu else None, "Die/package")
        self.stats["cpu"].set("load", f"{keyed['cpu_load'].value:.0f}%" if "cpu_load" in keyed else "n/a", "Load")
        self.stats["thermal"].set("source", f"{cpu.group}: {cpu.label}" if cpu else
                                   "CPU temperature unavailable; ACPI zones are not CPU measurements.", "CPU source")
        temps = [r.value for r in readings if r.unit == "°C"]
        self.stats["thermal"].set("coverage", f"{len(temps)} temperature readings", "Coverage")
        self.stats["thermal"].set_temp("peak", max(temps) if temps else None, "Hottest reported sensor")
