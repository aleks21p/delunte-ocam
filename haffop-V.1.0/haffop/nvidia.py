"""nvidia-smi / NVML helpers - identical on Linux and Windows."""

from __future__ import annotations

import os
from functools import lru_cache

from haffop.util import IS_WINDOWS, run, which

_FIELDS = [
    "index", "name", "pci.bus_id", "memory.total", "driver_version", "temperature.gpu",
    "clocks.gr", "clocks.mem", "clocks.max.gr", "clocks.max.mem", "power.draw",
    "power.limit", "power.default_limit", "power.min_limit", "power.max_limit",
    "fan.speed", "utilization.gpu", "pstate", "vbios_version", "pcie.link.gen.max",
    "pcie.link.width.max",
]


@lru_cache(maxsize=1)
def smi_path() -> str | None:
    path = which("nvidia-smi")
    if not path and IS_WINDOWS:
        cand = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe")
        path = cand if os.path.exists(cand) else None
    return path


def _num(v: str) -> float | None:
    try:
        return float(v)
    except ValueError:
        return None


def query() -> list[dict]:
    """One dict per NVIDIA GPU (numbers as float, missing values as None)."""
    smi = smi_path()
    if not smi:
        return []
    out = run([smi, f"--query-gpu={','.join(_FIELDS)}", "--format=csv,noheader,nounits"], timeout=6)
    gpus = []
    for line in out.splitlines():
        vals = [v.strip() for v in line.split(",")]
        if len(vals) != len(_FIELDS):
            continue
        row = {}
        for k, v in zip(_FIELDS, vals):
            if v in ("[N/A]", "N/A", "[Not Supported]", ""):
                row[k] = None
            elif k in ("name", "pci.bus_id", "driver_version", "pstate", "vbios_version"):
                row[k] = v
            else:
                row[k] = _num(v)
        gpus.append(row)
    return gpus


def normalize_bus_id(bus_id: str) -> str:
    """nvidia-smi '00000000:01:00.0' -> '0000:01:00.0' (sysfs style, lowercase)."""
    bus_id = bus_id.lower()
    dom, rest = bus_id.split(":", 1) if bus_id.count(":") == 2 else ("0000", bus_id)
    return f"{dom[-4:]}:{rest}"


@lru_cache(maxsize=1)
def nvml_available() -> bool:
    """True when the NVML Python bindings (package nvidia-ml-py / python3-pynvml) are installed."""
    import importlib.util
    return importlib.util.find_spec("pynvml") is not None
