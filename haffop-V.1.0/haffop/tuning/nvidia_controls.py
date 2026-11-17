"""NVIDIA tuning controls (nvidia-smi + NVML), shared by Linux and Windows."""

from __future__ import annotations

import os

from haffop import nvidia
from haffop.hardware.model import GPUInfo
from haffop.tuning.model import TUNABLE, UNLOCKED, Component, Control
from haffop.util import IS_WINDOWS, which


def nvidia_component(gpu: GPUInfo, row: dict, smi: str) -> Component:
    idx = int(row["index"])
    controls: list[Control] = []
    details = []
    pm = [] if IS_WINDOWS else [{"op": "exec", "argv": [smi, "-i", str(idx), "-pm", "1"]}]

    pl, pl_min, pl_max = row.get("power.limit"), row.get("power.min_limit"), row.get("power.max_limit")
    if pl and pl_min and pl_max and pl_max > pl_min:
        controls.append(Control(
            id=f"nv{idx}.power", label="Power limit", kind="slider", value=round(pl), minimum=round(pl_min),
            maximum=round(pl_max), step=1, unit="W", unlock=round(pl_max),
            help=f"Board power the GPU may draw (default {row.get('power.default_limit') or pl:.0f} W). Higher = "
                 "higher sustained clocks and temperatures.",
            to_ops=lambda v, i=idx: pm + [{"op": "exec", "argv": [smi, "-i", str(i), "-pl", str(int(v))]}]))
    else:
        details.append("Power limit is fixed by the vendor (common on laptop GPUs).")

    gr_max = row.get("clocks.max.gr")
    if gr_max:
        controls.append(Control(
            id=f"nv{idx}.clock_cap", label="Max core clock (cap)", kind="slider", value=int(gr_max), minimum=300,
            maximum=int(gr_max), step=15, unit="MHz", known=False,
            help="Caps the GPU core clock. Leave at the maximum for full speed; lower it to cut heat/noise.",
            to_ops=lambda v, i=idx, m=int(gr_max): pm + [{"op": "exec", "argv": (
                [smi, "-i", str(i), "-rgc"] if v >= m else [smi, "-i", str(i), "-lgc", f"0,{int(v)}"])}]))

    x11_settings = (not IS_WINDOWS and which("nvidia-settings") and os.environ.get("DISPLAY")
                    and os.environ.get("XDG_SESSION_TYPE", "x11") == "x11")
    if nvidia.nvml_available() or x11_settings:
        def offset_ops(kind: str):
            def ops(v, i=idx):
                if nvidia.nvml_available():
                    return [{"op": "nvml_offset", "index": i, kind: int(v)}]
                attr = "GPUGraphicsClockOffsetAllPerformanceLevels" if kind == "gpc" else "GPUMemoryTransferRateOffsetAllPerformanceLevels"
                return [{"op": "user", "argv": ["nvidia-settings", "-a", f"[gpu:{i}]/{attr}={int(v)}"]}]
            return ops
        controls.append(Control(
            id=f"nv{idx}.core_offset", label="Core clock offset (overclock)", kind="slider", value=0, minimum=-300,
            maximum=300, step=5, unit="MHz", known=False, to_ops=offset_ops("gpc"),
            help="Adds MHz to every point of the voltage/frequency curve. Raise in +15..+25 MHz steps and test "
                 "for crashes/artifacts; typical stable gains are +50..+150 MHz."))
        controls.append(Control(
            id=f"nv{idx}.mem_offset", label="Memory clock offset (overclock)", kind="slider", value=0, minimum=-1000,
            maximum=2000, step=50, unit="MHz", known=False, to_ops=offset_ops("mem"),
            help="Adds to the effective VRAM data rate. GDDR6 often handles +500..+1000 MHz. Errors show as "
                 "artifacts or lower performance (error correction)."))
        oc, summary = UNLOCKED, "Overclockable - core/memory clock offsets, power limit and clock caps are available."
    else:
        oc = TUNABLE
        summary = "Power limit and clock caps are adjustable."
        details.append("For real core/memory overclocking install NVML Python bindings: "
                       + ("pip install nvidia-ml-py (and run as administrator)." if IS_WINDOWS else
                          "pip install nvidia-ml-py / python3-pynvml (or use an X11 session with nvidia-settings + Coolbits)."))
    details.append(f"Driver {row.get('driver_version', '?')}, max boost {gr_max or '?'} MHz, "
                   f"VRAM {row.get('memory.total') or 0:.0f} MiB.")
    return Component(key=f"gpu:{gpu.key}", kind="gpu", title=gpu.name, device="Dedicated NVIDIA GPU",
                     oc=oc, summary=summary, details=details, controls=controls)


def nvml_offset(index: int, gpc: int | None = None, mem: int | None = None) -> str:
    """Apply clock offsets through NVML (needs root/admin). Returns a status message."""
    import pynvml
    pynvml.nvmlInit()
    try:
        h = pynvml.nvmlDeviceGetHandleByIndex(index)
        done = []
        if gpc is not None:
            pynvml.nvmlDeviceSetGpcClkVfOffset(h, int(gpc))
            done.append(f"core {gpc:+d} MHz")
        if mem is not None:
            pynvml.nvmlDeviceSetMemClkVfOffset(h, int(mem))
            done.append(f"memory {mem:+d} MHz")
        return f"GPU {index}: offset " + ", ".join(done)
    finally:
        pynvml.nvmlShutdown()
