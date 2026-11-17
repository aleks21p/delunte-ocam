"""Hardware tab: Simple (cards) and Expanded (everything detected) views."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QGuiApplication
from PyQt6.QtWidgets import (QButtonGroup, QFileDialog, QGridLayout, QHBoxLayout, QLineEdit, QProgressBar,
                             QPushButton, QScrollArea, QStackedWidget, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from haffop.hardware.model import CATEGORY_ORDER, HardwareSnapshot, SensorReading
from haffop.ui.theme import MUTED, temp_color
from haffop.ui.widgets import Card, StatGrid, muted
from haffop.util import human_bytes


def _mhz(v: float | None) -> str:
    if not v:
        return "n/a"
    return f"{v / 1000:.2f} GHz" if v >= 1000 else f"{v:.0f} MHz"


class SimpleView(QScrollArea):
    def __init__(self, snap: HardwareSnapshot, parent=None) -> None:
        super().__init__(parent)
        self.snap = snap
        self.setWidgetResizable(True)
        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(18, 18, 18, 18)
        grid.setSpacing(14)
        self.cards: list[Card] = []
        self.stats: dict[str, StatGrid] = {}

        # CPU
        c = snap.cpu
        card, st = self._card("cpu", "Processor (CPU)", c.name)
        st.set("cores", f"{c.cores} cores / {c.threads} threads", "Cores")
        st.set("max", _mhz(c.max_mhz), "Max boost")
        st.set("clock", "...", "Current clock")
        st.set_temp("temp", None, "Temperature")
        self.cpu_load = QProgressBar()
        self.cpu_load.setRange(0, 100)
        st.set("load", "...", "Load")
        card.add(self.cpu_load)

        # RAM
        mods = snap.ram_modules
        types = ", ".join(sorted({m.mem_type for m in mods if m.mem_type})) or "RAM"
        speed = max((m.configured_mts or m.rated_mts or 0 for m in mods), default=0)
        rated = max((m.rated_mts or 0 for m in mods), default=0)
        card, st = self._card("ram", "Memory (RAM)", f"{human_bytes(snap.ram_total)} {types}")
        st.set("cap", f"{snap.ram_total / 1024**3:.1f} GiB", "Capacity")
        st.set("speed", f"{speed} MT/s" + (f" (rated {rated})" if rated and rated != speed else "") if speed else "n/a", "Speed")
        st.set("mods", f"{len(mods)} module(s)" + (f" in {snap.ram_slots_total} slots" if snap.ram_slots_total else ""), "Modules")
        st.set("used", "...", "In use")

        # GPUs
        for g in snap.gpus or []:
            card, st = self._card(f"gpu:{g.key}", "Graphics (GPU)", g.name)
            st.set("type", "Integrated graphics" if g.integrated else "Dedicated graphics card", "Type")
            st.set("vram", human_bytes(g.vram) if g.vram else ("Shared with system RAM" if g.integrated else "n/a"), "Video memory")
            st.set("clock", "...", "Clock")
            st.set_temp("temp", None, "Temperature")
        if not snap.gpus:
            self._card("gpu", "Graphics (GPU)", "No GPU detected")

        # Storage
        card, st = self._card("storage", "Storage", f"{len(snap.disks)} drive(s)")
        for d in snap.disks:
            st.set(f"disk:{d.key}", f"{human_bytes(d.size, si=True)} {d.kind}", d.model)
            st.set(f"diskt:{d.key}", "n/a", "   temperature")

        # Wi-Fi
        if snap.wifi:
            for w in snap.wifi:
                card, st = self._card(f"wifi:{w.key}", "Wi-Fi", w.name)
                st.set("std", w.max_standard or "Unknown", "Supports")
                st.set("conn", (f"{w.link_speed} - {w.standard}" if w.connected else "Not connected"), "Link speed")
                if w.signal:
                    st.set("sig", w.signal, "Signal")
                st.set_temp("temp", None, "Temperature")
        else:
            self._card("wifi", "Wi-Fi", "No Wi-Fi card detected")

        # All temperatures
        self.temp_card, self.temp_stats = self._card("temps", "Temperatures", "Every temperature sensor, live")

        cols = 3
        for i, card in enumerate(self.cards):
            grid.addWidget(card, i // cols, i % cols, Qt.AlignmentFlag.AlignTop)
        for col in range(cols):
            grid.setColumnStretch(col, 1)
        grid.setRowStretch(grid.rowCount(), 1)
        self.setWidget(body)

    def _card(self, key: str, title: str, subtitle: str) -> tuple[Card, StatGrid]:
        card = Card(title, subtitle)
        st = StatGrid()
        card.add(st)
        self.cards.append(card)
        self.stats[key] = st
        return card, st

    def update_sensors(self, readings: list[SensorReading]) -> None:
        by_key = {r.key: r for r in readings if r.key}
        if (r := by_key.get("cpu")):
            self.stats["cpu"].set_temp("temp", r.value, "Temperature")
        if (r := by_key.get("cpu_clock")):
            self.stats["cpu"].set("clock", _mhz(r.value), "Current clock")
        if (r := by_key.get("cpu_load")):
            self.stats["cpu"].set("load", f"{r.value:.0f} %", "Load")
            self.cpu_load.setValue(int(r.value))
        if (r := by_key.get("ram_used")):
            self.stats["ram"].set("used", f"{r.value:.0f} %", "In use")
        if (r := by_key.get("ram")):
            self.stats["ram"].set_temp("temp", r.value, "Temperature")
        cpu_temp = by_key.get("cpu")
        for g in self.snap.gpus:
            st = self.stats.get(f"gpu:{g.key}")
            if not st:
                continue
            if (r := by_key.get(f"gpu:{g.key}")):
                st.set_temp("temp", r.value, "Temperature")
            elif g.integrated and cpu_temp:
                st.set("temp", f"{cpu_temp.value:.0f} °C (shares CPU die)", "Temperature", temp_color(cpu_temp.value))
            if (r := by_key.get(f"gpu_clock:{g.key}")):
                st.set("clock", _mhz(r.value), "Clock")
        for d in self.snap.disks:
            if (r := by_key.get(f"disk:{d.key}")):
                self.stats["storage"].set_temp(f"diskt:{d.key}", r.value, "   temperature")
        for w in self.snap.wifi:
            if (r := by_key.get(f"wifi:{w.key}")):
                self.stats[f"wifi:{w.key}"].set_temp("temp", r.value, "Temperature")
        for r in readings:
            if r.unit == "°C":
                self.temp_stats.set_temp(f"{r.group}/{r.label}", r.value, f"{r.group}: {r.label}")


class ExpandedView(QWidget):
    def __init__(self, snap: HardwareSnapshot, parent=None) -> None:
        super().__init__(parent)
        self.snap = snap
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 10, 18, 18)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Component / property", "Value"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setColumnWidth(0, 380)
        self.tree.setUniformRowHeights(True)
        lay.addWidget(self.tree)
        cats = sorted(snap.expanded, key=lambda c: CATEGORY_ORDER.index(c) if c in CATEGORY_ORDER else 99)
        for cat in cats:
            items = snap.expanded[cat]
            top = QTreeWidgetItem([f"{cat}", f"{len(items)} item(s)"])
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            top.setForeground(1, Qt.GlobalColor.gray)
            self.tree.addTopLevelItem(top)
            for title, props in items:
                dev = QTreeWidgetItem([title, ""])
                top.addChild(dev)
                for k, v in props:
                    child = QTreeWidgetItem([k, v])
                    child.setToolTip(1, v)
                    dev.addChild(child)
        if snap.warnings:
            top = QTreeWidgetItem(["Notes", f"{len(snap.warnings)}"])
            self.tree.addTopLevelItem(top)
            for w in snap.warnings:
                it = QTreeWidgetItem(["", w])
                it.setToolTip(1, w)
                top.addChild(it)
        self.sensor_root = QTreeWidgetItem(["Sensors (live)", ""])
        f = self.sensor_root.font(0)
        f.setBold(True)
        self.sensor_root.setFont(0, f)
        self.tree.addTopLevelItem(self.sensor_root)
        self._sensor_items: dict[str, QTreeWidgetItem] = {}
        self._groups: dict[str, QTreeWidgetItem] = {}
        self.tree.expandAll()

    def update_sensors(self, readings: list[SensorReading]) -> None:
        for r in readings:
            grp = self._groups.get(r.group)
            if grp is None:
                grp = QTreeWidgetItem([r.group, ""])
                self.sensor_root.addChild(grp)
                grp.setExpanded(True)
                self._groups[r.group] = grp
            key = f"{r.group}/{r.label}/{r.unit}"
            it = self._sensor_items.get(key)
            if it is None:
                it = QTreeWidgetItem([r.label, ""])
                grp.addChild(it)
                self._sensor_items[key] = it
            fmt = f"{r.value:.0f}" if r.unit in ("RPM", "MHz", "%") else f"{r.value:.1f}"
            it.setText(1, f"{fmt} {r.unit}")
            if r.unit == "°C":
                it.setForeground(1, QColor(temp_color(r.value)))
        self.sensor_root.setText(1, f"{len(readings)} readings")

    def filter(self, text: str) -> None:
        text = text.lower().strip()

        def visit(item: QTreeWidgetItem) -> bool:
            own = not text or text in item.text(0).lower() or text in item.text(1).lower()
            any_child = False
            for i in range(item.childCount()):
                child = item.child(i)
                if own and text:  # matching parent shows its whole subtree
                    self._show_all(child)
                    any_child = True
                elif visit(child):
                    any_child = True
            visible = own or any_child
            item.setHidden(not visible)
            return visible

        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i))

    def _show_all(self, item: QTreeWidgetItem) -> None:
        item.setHidden(False)
        for i in range(item.childCount()):
            self._show_all(item.child(i))

    def report_text(self) -> str:
        lines = []

        def walk(item: QTreeWidgetItem, depth: int) -> None:
            a, b = item.text(0), item.text(1)
            lines.append("  " * depth + (f"{a}: {b}" if b and item.childCount() == 0 else a))
            for i in range(item.childCount()):
                walk(item.child(i), depth + 1)

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i), 0)
            lines.append("")
        return "\n".join(lines)


class HardwareTab(QWidget):
    def __init__(self, snap: HardwareSnapshot, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.setContentsMargins(18, 14, 18, 0)
        self.group = QButtonGroup(self)
        for i, name in enumerate(("Simple", "Expanded")):
            b = QPushButton(name)
            b.setObjectName("Segment")
            b.setCheckable(True)
            b.setChecked(i == 0)
            self.group.addButton(b, i)
            bar.addWidget(b)
        bar.setSpacing(0)
        bar.addSpacing(16)
        self.hint = muted("Quick overview of the main components with live temperatures.")
        bar.addWidget(self.hint, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search everything detected...")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(240)
        self.search.hide()
        bar.addWidget(self.search)
        bar.addSpacing(8)
        self.copy_btn = QPushButton("Copy report")
        self.save_btn = QPushButton("Save report...")
        self.copy_btn.hide()
        self.save_btn.hide()
        bar.addWidget(self.copy_btn)
        bar.addWidget(self.save_btn)
        lay.addLayout(bar)

        self.stack = QStackedWidget()
        self.simple = SimpleView(snap)
        self.expanded = ExpandedView(snap)
        self.stack.addWidget(self.simple)
        self.stack.addWidget(self.expanded)
        lay.addWidget(self.stack, 1)

        self.group.idToggled.connect(self._switch)
        self.search.textChanged.connect(self.expanded.filter)
        self.copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.expanded.report_text()))
        self.save_btn.clicked.connect(self._save)

    def _switch(self, idx: int, checked: bool) -> None:
        if not checked:
            return
        self.stack.setCurrentIndex(idx)
        expanded = idx == 1
        for w in (self.search, self.copy_btn, self.save_btn):
            w.setVisible(expanded)
        self.hint.setText("Everything physical Haffop can detect - expand, search or export." if expanded
                          else "Quick overview of the main components with live temperatures.")
        self.hint.setStyleSheet(f"color: {MUTED};")

    def _save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save hardware report", "haffop-hardware-report.txt", "Text files (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.expanded.report_text())

    def update_sensors(self, readings: list[SensorReading]) -> None:
        self.simple.update_sensors(readings)
        self.expanded.update_sensors(readings)
