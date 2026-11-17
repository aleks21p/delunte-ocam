"""Temperature validation and deterministic CPU die/package selection."""

from __future__ import annotations

import math

from haffop.hardware.model import SensorReading


def valid_temperature(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value) and -20 <= value <= 150


def select_cpu_temperature(candidates: list[SensorReading]) -> None:
    ranked = []
    for reading in candidates:
        reading.key = None
        if not valid_temperature(reading.value):
            continue
        label = reading.label.lower()
        if "tdie" in label or "cpu die" in label:
            priority = 0
        elif "package" in label:
            priority = 1
        elif "tctl" in label:
            priority = 2
        elif "core" in label and "tccd" not in label:
            priority = 3
        else:
            continue
        ranked.append((priority, reading))
    if ranked:
        priority = min(rank for rank, _ in ranked)
        max((r for rank, r in ranked if rank == priority), key=lambda r: r.value).key = "cpu"
