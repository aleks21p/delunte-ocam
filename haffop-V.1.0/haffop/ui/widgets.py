"""Reusable widgets: cards, badges, stat lines and tuning control editors."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QSlider,
                             QSpinBox, QVBoxLayout, QWidget)

from haffop.tuning.model import OC_BADGES, Control
from haffop.ui.theme import MUTED, WARN, temp_color


class Card(QFrame):
    def __init__(self, title: str = "", subtitle: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(16, 14, 16, 14)
        self.layout_.setSpacing(6)
        if title:
            head = QHBoxLayout()
            self.title_label = QLabel(title)
            self.title_label.setObjectName("CardTitle")
            self.title_label.setWordWrap(True)
            head.addWidget(self.title_label, 1)
            self.head = head
            self.layout_.addLayout(head)
        if subtitle:
            sub = muted(subtitle)
            sub.setWordWrap(True)
            self.layout_.addWidget(sub)

    def add(self, w: QWidget) -> QWidget:
        self.layout_.addWidget(w)
        return w


def muted(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("Muted")
    lbl.setWordWrap(True)
    return lbl


def badge(oc: str) -> QLabel:
    text, color = OC_BADGES[oc]
    lbl = QLabel(text)
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    lbl.setStyleSheet(f"background: rgba({r},{g},{b},40); color: {color}; border: 1px solid {color}; border-radius: 10px;"
                      "padding: 2px 10px; font-weight: 600;")
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return lbl


class StatGrid(QWidget):
    """Two-column 'label: value' list whose values can be updated live."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 4, 0, 0)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(4)
        self.grid.setColumnStretch(1, 1)
        self._values: dict[str, QLabel] = {}

    def set(self, key: str, value: str, label: str | None = None, color: str | None = None) -> None:
        if key not in self._values:
            row = self.grid.rowCount()
            k = QLabel(label or key)
            k.setStyleSheet(f"color: {MUTED};")
            v = QLabel()
            v.setWordWrap(True)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.grid.addWidget(k, row, 0, Qt.AlignmentFlag.AlignTop)
            self.grid.addWidget(v, row, 1)
            self._values[key] = v
        lbl = self._values[key]
        lbl.setText(value)
        lbl.setStyleSheet(f"color: {color}; font-weight: 600;" if color else "")

    def set_temp(self, key: str, value: float | None, label: str | None = None) -> None:
        self.set(key, f"{value:.0f} °C" if value is not None else "n/a", label, temp_color(value) if value is not None else MUTED)


class ControlEditor(QWidget):
    """Editor for one tuning Control (slider + spin box, combo box or switch)."""

    changed = pyqtSignal()

    def __init__(self, control: Control, parent=None) -> None:
        super().__init__(parent)
        self.control = control
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 8)
        head = QHBoxLayout()
        self.title = QLabel(f"<b>{control.label}</b>")
        head.addWidget(self.title)
        head.addStretch(1)
        self.modified = QLabel("● modified")
        self.modified.setStyleSheet(f"color: {WARN};")
        self.modified.hide()
        head.addWidget(self.modified)
        lay.addLayout(head)

        row = QHBoxLayout()
        c = control
        self._scale = 1
        if c.kind == "slider":
            self.slider = QSlider(Qt.Orientation.Horizontal)
            self.slider.setRange(int(c.minimum), int(c.maximum))
            self.slider.setSingleStep(int(c.step))
            self.slider.setPageStep(int(c.step) * 5)
            self.spin = QSpinBox()
            self.spin.setRange(int(c.minimum), int(c.maximum))
            self.spin.setSingleStep(int(c.step))
            self.spin.setSuffix(f" {c.unit}")
            self.spin.setMinimumWidth(110)
            self.slider.valueChanged.connect(self.spin.setValue)
            self.spin.valueChanged.connect(self.slider.setValue)
            self.spin.valueChanged.connect(self._emit)
            row.addWidget(muted(f"{int(c.minimum)}"))
            row.addWidget(self.slider, 1)
            row.addWidget(muted(f"{int(c.maximum)} {c.unit}"))
            row.addWidget(self.spin)
        elif c.kind == "choice":
            self.combo = QComboBox()
            for val, label in c.options:
                self.combo.addItem(label, val)
            if c.value not in [o[0] for o in c.options] and c.value is not None:
                self.combo.addItem(str(c.value), c.value)
            self.combo.currentIndexChanged.connect(self._emit)
            row.addWidget(self.combo, 1)
        else:
            self.check = QCheckBox("Enabled")
            self.check.toggled.connect(self._emit)
            row.addWidget(self.check)
            row.addStretch(1)
        lay.addLayout(row)
        help_text = c.help + ("" if c.known else "  (Current hardware value could not be read - showing the stock/estimated value.)")
        if help_text:
            lay.addWidget(muted(help_text))
        self.set_value(c.value)

    def _emit(self) -> None:
        self.modified.setVisible(self.is_modified())
        self.changed.emit()

    def value(self):
        c = self.control
        if c.kind == "slider":
            return self.spin.value()
        if c.kind == "choice":
            return self.combo.currentData()
        return self.check.isChecked()

    def set_value(self, v) -> None:
        c = self.control
        if v is None:
            return
        if c.kind == "slider":
            self.spin.setValue(int(round(float(v))))
        elif c.kind == "choice":
            idx = self.combo.findData(v)
            if idx >= 0:
                self.combo.setCurrentIndex(idx)
        else:
            self.check.setChecked(bool(v))
        self.modified.setVisible(self.is_modified())

    def is_modified(self) -> bool:
        v, cur = self.value(), self.control.value
        if self.control.kind == "slider":
            return cur is None or int(v) != int(round(float(cur)))
        return v != cur
