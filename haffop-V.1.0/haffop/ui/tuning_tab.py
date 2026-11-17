"""Tuning tab: overclock status of CPU / GPU / RAM, adjustable controls and laptop unlock."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
                             QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QStackedWidget, QVBoxLayout,
                             QWidget)

from haffop.hardware.model import HardwareSnapshot, SensorReading
from haffop.tuning.model import BIOS, LOCKED, OC_BADGES, TUNABLE, UNLOCKED, Component, Control
from haffop.ui.theme import ACCENT, DANGER, temp_color
from haffop.ui.widgets import Card, ControlEditor, StatGrid, badge, muted
from haffop.ui.workers import Job

CAN_OC = {
    UNLOCKED: "Yes",
    TUNABLE: "No (locked) - but speed limits, boost and power can be adjusted here",
    BIOS: "Yes - from BIOS/UEFI only (not from an operating system)",
    LOCKED: "No",
}
ALWAYS_SHOWN = {"Temperature", "Clock", "Load", "In use"}  # other live rows appear once a sensor reports
KIND_LABEL = {"cpu": "CPU", "gpu": "GPU", "ram": "RAM", "laptop": "LAPTOP"}
DISCLAIMER = (
    "Overclocking and raising power/temperature limits make your hardware run hotter and can cause crashes, "
    "instability or reduced component lifespan. Laptops will get hot and loud.\n\n"
    "Haffop only uses limits exposed by the hardware/driver and changes reset on reboot, but you use these "
    "features at your own risk.\n\nContinue?")


def _live_keys(comp: Component, snap: HardwareSnapshot) -> dict[str, str]:
    """Map stat-row name -> sensor key for a component."""
    if comp.kind in ("cpu", "laptop"):
        return {"Temperature": "cpu", "Clock": "cpu_clock", "Power": "cpu_power", "Load": "cpu_load"}
    if comp.kind == "gpu":
        k = comp.key.split(":", 1)[1]
        return {"Temperature": f"gpu:{k}", "Clock": f"gpu_clock:{k}", "Power": f"gpu_power:{k}"}
    if comp.kind == "ram":
        return {"In use": "ram_used", "Temperature": "ram"}
    return {}


def _fmt(row: str, r: SensorReading) -> tuple[str, str | None]:
    if r.unit == "°C":
        return f"{r.value:.0f} °C", temp_color(r.value)
    if r.unit == "MHz":
        return (f"{r.value / 1000:.2f} GHz" if r.value >= 1000 else f"{r.value:.0f} MHz"), None
    if r.unit == "W":
        return f"{r.value:.1f} W", None
    return f"{r.value:.0f} {r.unit}", None


class ComponentPage(QScrollArea):
    apply_requested = pyqtSignal(list)  # [(Control, value)]
    action_requested = pyqtSignal(str, list, str)
    unlock_requested = pyqtSignal()
    restore_requested = pyqtSignal()

    def __init__(self, comp: Component, snap: HardwareSnapshot, parent=None) -> None:
        super().__init__(parent)
        self.comp = comp
        self.setWidgetResizable(True)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(14)

        head = QHBoxLayout()
        title = QLabel(comp.title)
        title.setObjectName("PageTitle")
        title.setWordWrap(True)
        head.addWidget(title, 1)
        head.addWidget(badge(comp.oc))
        lay.addLayout(head)
        lay.addWidget(muted(comp.device))

        if comp.kind == "laptop":
            unlock_card = Card("One-click unlock", "Applies the maximum-performance value for every setting marked "
                                                   "below (and raises GPU power limits). Expect higher temperatures.")
            row = QHBoxLayout()
            b = QPushButton("Unlock maximum performance")
            b.setObjectName("Danger")
            b.clicked.connect(self.unlock_requested.emit)
            r = QPushButton("Restore startup settings")
            r.clicked.connect(self.restore_requested.emit)
            row.addWidget(b)
            row.addWidget(r)
            row.addStretch(1)
            unlock_card.layout_.addLayout(row)
            lay.addWidget(unlock_card)

        self.keys = _live_keys(comp, snap)
        self.live = StatGrid()
        if self.keys:
            live = Card("Live")
            for row in self.keys:
                if row in ALWAYS_SHOWN:
                    self.live.set(row, "...", row)
            live.add(self.live)
            lay.addWidget(live)

        info = Card("What does unlocking do?" if comp.kind == "laptop" else "Can it be overclocked?")
        if comp.kind != "laptop":
            ans = QLabel(f"<b>{CAN_OC[comp.oc]}</b>")
            ans.setWordWrap(True)
            ans.setStyleSheet(f"color: {OC_BADGES[comp.oc][1]};")
            info.add(ans)
        info.add(QLabel(comp.summary)).setWordWrap(True)
        for d in comp.details:
            info.add(muted("• " + d))
        lay.addWidget(info)

        self.editors: list[ControlEditor] = []
        if comp.controls:
            card = Card("Settings", "Changes apply immediately after you press Apply and reset on reboot.")
            for c in comp.controls:
                ed = ControlEditor(c)
                if c.unlock is not None and comp.kind == "laptop":
                    ed.title.setText(f"<b>{c.label}</b>  <span style='color:{ACCENT}'>⚡ unlock: {c.unlock}{(' ' + c.unit) if c.unit else ''}</span>")
                ed.changed.connect(self._update_buttons)
                self.editors.append(ed)
                card.add(ed)
            row = QHBoxLayout()
            self.apply_btn = QPushButton("Apply")
            self.apply_btn.setObjectName("Primary")
            self.discard_btn = QPushButton("Discard changes")
            self.restore_btn = QPushButton("Restore startup values")
            self.apply_btn.clicked.connect(self._apply)
            self.discard_btn.clicked.connect(self._discard)
            self.restore_btn.clicked.connect(self._restore)
            row.addWidget(self.apply_btn)
            row.addWidget(self.discard_btn)
            row.addStretch(1)
            row.addWidget(self.restore_btn)
            card.layout_.addLayout(row)
            lay.addWidget(card)
            self._update_buttons()

        for label, ops, confirm in comp.actions:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _=False, lb=label, o=ops, cf=confirm: self.action_requested.emit(lb, o, cf))
            lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addStretch(1)
        self.setWidget(body)

    def _update_buttons(self) -> None:
        n = sum(e.is_modified() for e in self.editors)
        self.apply_btn.setEnabled(n > 0)
        self.discard_btn.setEnabled(n > 0)
        self.apply_btn.setText(f"Apply {n} change{'s' if n != 1 else ''}" if n else "Apply")

    def _apply(self) -> None:
        self.apply_requested.emit([(e.control, e.value()) for e in self.editors if e.is_modified()])

    def _discard(self) -> None:
        for e in self.editors:
            e.set_value(e.control.value)
        self._update_buttons()

    def _restore(self) -> None:
        for e in self.editors:
            e.set_value(e.control.default)
        self._update_buttons()

    def update_sensors(self, by_key: dict[str, SensorReading]) -> None:
        self.live.clear_values()
        for row, key in self.keys.items():
            if (r := by_key.get(key)):
                text, color = _fmt(row, r)
                self.live.set(row, text, row, color)


class OverviewPage(QScrollArea):
    open_requested = pyqtSignal(str)
    unlock_requested = pyqtSignal()
    restore_requested = pyqtSignal()

    def __init__(self, comps: list[Component], snap: HardwareSnapshot, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(14)
        t = QLabel("Performance overview")
        t.setObjectName("PageTitle")
        lay.addWidget(t)
        lay.addWidget(muted("Your CPU, graphics and memory - whether each can be overclocked, and what you can change."))

        laptop = next((c for c in comps if c.kind == "laptop"), None)
        if laptop:
            card = Card("Laptop detected - unlock higher speed",
                        "Raise power and temperature limits so the CPU/GPU hold higher clocks for longer. "
                        "The laptop will run hotter and louder. Changes reset on reboot.")
            row = QHBoxLayout()
            b = QPushButton("Unlock maximum performance")
            b.setObjectName("Danger")
            b.clicked.connect(self.unlock_requested.emit)
            d = QPushButton("Details...")
            d.clicked.connect(lambda: self.open_requested.emit("laptop"))
            r = QPushButton("Restore startup settings")
            r.clicked.connect(self.restore_requested.emit)
            row.addWidget(b)
            row.addWidget(d)
            row.addStretch(1)
            row.addWidget(r)
            card.layout_.addLayout(row)
            lay.addWidget(card)

        grid = QGridLayout()
        grid.setSpacing(14)
        self.lives: list[tuple[StatGrid, dict[str, str]]] = []
        main = [c for c in comps if c.kind in ("cpu", "gpu", "ram")]
        for i, c in enumerate(main):
            card = Card(f"{KIND_LABEL[c.kind]}")
            card.head.addWidget(badge(c.oc))
            name = QLabel(f"<b>{c.title}</b>")
            name.setWordWrap(True)
            card.add(name)
            card.add(muted(c.device))
            ans = QLabel(f"Can be overclocked: <b>{CAN_OC[c.oc]}</b>")
            ans.setWordWrap(True)
            ans.setStyleSheet(f"color: {OC_BADGES[c.oc][1]};")
            card.add(ans)
            st = StatGrid()
            keys = _live_keys(c, snap)
            for row in keys:
                if row in ALWAYS_SHOWN:
                    st.set(row, "...", row)
            card.add(st)
            self.lives.append((st, keys))
            n = len(c.controls)
            btn = QPushButton(f"Tune ({n} setting{'s' if n != 1 else ''})" if n else "Details")
            btn.clicked.connect(lambda _=False, k=c.key: self.open_requested.emit(k))
            card.add(btn)
            card.layout_.addStretch(1)
            grid.addWidget(card, i // 3, i % 3)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        lay.addLayout(grid)
        lay.addStretch(1)
        self.setWidget(body)

    def update_sensors(self, by_key: dict[str, SensorReading]) -> None:
        for st, keys in self.lives:
            st.clear_values()
            for row, key in keys.items():
                if (r := by_key.get(key)):
                    text, color = _fmt(row, r)
                    st.set(row, text, row, color)


class TuningTab(QWidget):
    def __init__(self, snap: HardwareSnapshot, backend, comps: list[Component], parent=None) -> None:
        super().__init__(parent)
        self.snap = snap
        self.backend = backend
        self.comps = comps
        self.startup = {c.id: c.default for comp in comps for c in comp.controls}
        self._accepted_risk = False
        self._job: Job | None = None
        self._last_by_key: dict[str, SensorReading] = {}

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(260)
        self.sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sidebar.setTextElideMode(Qt.TextElideMode.ElideRight)
        lay.addWidget(self.sidebar)

        right = QSplitter(Qt.Orientation.Vertical)
        self.stack = QStackedWidget()
        right.addWidget(self.stack)
        logw = QWidget()
        ll = QVBoxLayout(logw)
        ll.setContentsMargins(22, 4, 22, 10)
        self.status = muted("Ready.")
        ll.addWidget(self.status)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Applied changes and results appear here.")
        ll.addWidget(self.log)
        right.addWidget(logw)
        right.setStretchFactor(0, 5)
        right.setStretchFactor(1, 1)
        right.setSizes([700, 140])
        lay.addWidget(right, 1)
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self._build()

    # ---------------------------------------------------------------- build
    def _build(self, keep_row: int = 0) -> None:
        self.sidebar.blockSignals(True)
        self.sidebar.clear()
        while self.stack.count():
            w = self.stack.widget(0)
            self.stack.removeWidget(w)
            w.deleteLater()
        self.pages: list = []
        ov = OverviewPage(self.comps, self.snap)
        ov.open_requested.connect(self._open)
        ov.unlock_requested.connect(self.unlock_all)
        ov.restore_requested.connect(self.restore_all)
        self._add_page("Overview", ov, None)
        for c in self.comps:
            page = ComponentPage(c, self.snap)
            page.apply_requested.connect(self.apply_pairs)
            page.action_requested.connect(self.run_action)
            page.unlock_requested.connect(self.unlock_all)
            page.restore_requested.connect(self.restore_all)
            short = c.title if len(c.title) < 28 else c.title[:26] + "…"
            self._add_page(f"{KIND_LABEL[c.kind]}  ·  {short}", page, c)
        self.sidebar.blockSignals(False)
        self.sidebar.setCurrentRow(min(keep_row, self.sidebar.count() - 1))
        self.stack.setCurrentIndex(self.sidebar.currentRow())
        if self._last_by_key:
            for p in self.pages:
                p.update_sensors(self._last_by_key)

    def _add_page(self, label: str, page, comp: Component | None) -> None:
        item = QListWidgetItem(label)
        if comp:
            item.setToolTip(f"{comp.title}\n{OC_BADGES[comp.oc][0]}")
            item.setData(Qt.ItemDataRole.UserRole, comp.key)
            item.setText(f"●  {label}")
            item.setForeground(QColor(OC_BADGES[comp.oc][1]))
        self.sidebar.addItem(item)
        self.stack.addWidget(page)
        self.pages.append(page)

    def _open(self, key: str) -> None:
        for i in range(self.sidebar.count()):
            if self.sidebar.item(i).data(Qt.ItemDataRole.UserRole) == key:
                self.sidebar.setCurrentRow(i)
                return

    # ---------------------------------------------------------------- actions
    def _confirm_risk(self) -> bool:
        if self._accepted_risk:
            return True
        ok = QMessageBox.warning(self, "Before you continue", DISCLAIMER,
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
        self._accepted_risk = ok
        return ok

    def _all_controls(self) -> list[Control]:
        return [c for comp in self.comps for c in comp.controls]

    def unlock_all(self) -> None:
        pairs = [(c, c.unlock) for c in self._all_controls()
                 if c.unlock is not None and (not c.known or c.unlock != c.value)]
        if not pairs:
            QMessageBox.information(self, "Already unlocked", "Every unlockable setting is already at its maximum-performance value.")
            return
        lines = "\n".join(f"• {c.label}: {c.value} → {v} {c.unit}".rstrip() for c, v in pairs)
        if QMessageBox.question(self, "Unlock maximum performance",
                                f"These settings will be changed:\n\n{lines}\n\nApply now?") != QMessageBox.StandardButton.Yes:
            return
        self.apply_pairs(pairs)

    def restore_all(self) -> None:
        pairs = [(c, self.startup[c.id]) for c in self._all_controls()
                 if c.id in self.startup and self.startup[c.id] is not None and self.startup[c.id] != c.value]
        if not pairs:
            QMessageBox.information(self, "Nothing to restore", "All settings already match the values from when Haffop started.")
            return
        lines = "\n".join(f"• {c.label}: {c.value} → {v} {c.unit}".rstrip() for c, v in pairs)
        if QMessageBox.question(self, "Restore startup settings", f"Restore these settings?\n\n{lines}") == QMessageBox.StandardButton.Yes:
            self.apply_pairs(pairs, risky=False)

    def apply_pairs(self, pairs: list, risky: bool = True) -> None:
        if not pairs or (self._job and self._job.isRunning()):
            return
        if risky and not self._confirm_risk():
            return
        order = {c.id: i for i, c in enumerate(self._all_controls())}
        pairs = sorted(pairs, key=lambda p: order.get(p[0].id, 0))
        ops = [op for c, v in pairs for op in c.to_ops(v)]
        applied = {c.id: v for c, v in pairs}
        self._run(f"Applying {len(pairs)} setting(s)...", ops, applied)

    def run_action(self, label: str, ops: list, confirm: str) -> None:
        if confirm and QMessageBox.question(self, label, confirm) != QMessageBox.StandardButton.Yes:
            return
        self._run(f"{label}...", ops, {})

    def _run(self, message: str, ops: list, applied: dict) -> None:
        self.status.setText(message)
        self.setEnabled(False)

        def work():
            results = self.backend.apply(ops)
            return results, self.backend.components()

        self._job = Job(work, self)
        self._job.done.connect(lambda res: self._finished(res, applied))
        self._job.failed.connect(self._failed)
        self._job.start()

    def _finished(self, res, applied: dict) -> None:
        results, new_comps = res
        self.setEnabled(True)
        ok = all(r[0] for r in results)
        for success, msg in results:
            self.log.appendPlainText(("✔ " if success else "✖ ") + msg)
        self.status.setText("All changes applied." if ok else "Some changes failed - see the log below.")
        self.status.setStyleSheet(f"color: {ACCENT if ok else DANGER};")
        for comp in new_comps:
            for c in comp.controls:
                c.default = self.startup.get(c.id, c.value)
                if not c.known and c.id in applied and ok:
                    c.value = applied[c.id]
                    c.known = True
        row = self.sidebar.currentRow()
        self.comps = new_comps
        self._build(row)

    def _failed(self, tb: str) -> None:
        self.setEnabled(True)
        self.status.setText("Unexpected error - see the log.")
        self.status.setStyleSheet(f"color: {DANGER};")
        self.log.appendPlainText(tb)

    def update_sensors(self, readings: list[SensorReading]) -> None:
        self._last_by_key = {r.key: r for r in readings if r.key}
        for p in self.pages:
            p.update_sensors(self._last_by_key)

