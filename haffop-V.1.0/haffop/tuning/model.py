"""Data model for tunable components (CPU, GPU, RAM, laptop unlock).

Every control converts a chosen value into a list of *ops* - small dicts
executed by the platform backend:
  {"op": "write", "path": ..., "value": ...}   privileged sysfs write (Linux)
  {"op": "exec", "argv": [...]}                privileged command (root/admin)
  {"op": "user", "argv": [...]}                unprivileged command
  {"op": "nvml_offset", "index": i, "gpc": mhz, "mem": mhz}
  {"op": "win_ultimate"}                        create + activate Ultimate Performance plan
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

UNLOCKED, TUNABLE, BIOS, LOCKED = "unlocked", "tunable", "bios", "locked"

OC_BADGES = {
    UNLOCKED: ("Overclockable", "#2ecc71"),
    TUNABLE: ("Tunable (limits & boost)", "#f1c40f"),
    BIOS: ("BIOS / UEFI only", "#3498db"),
    LOCKED: ("Locked", "#e74c3c"),
}


@dataclass
class Control:
    id: str
    label: str
    kind: str  # slider | choice | toggle
    value: Any
    to_ops: Callable[[Any], list[dict]]
    minimum: float = 0
    maximum: float = 0
    step: float = 1
    unit: str = ""
    options: list[tuple[str, str]] = field(default_factory=list)  # (value, label)
    help: str = ""
    unlock: Any = None  # value used by the one-click "unlock" preset (None = untouched)
    known: bool = True  # False when the current hardware value could not be read
    default: Any = None  # value at startup, used by "Restore"

    def __post_init__(self) -> None:
        if self.default is None:
            self.default = self.value


@dataclass
class Component:
    key: str
    kind: str  # cpu | gpu | ram | laptop
    title: str
    device: str
    oc: str
    summary: str
    details: list[str] = field(default_factory=list)
    controls: list[Control] = field(default_factory=list)
    actions: list[tuple[str, list[dict], str]] = field(default_factory=list)  # (label, ops, confirm text)
