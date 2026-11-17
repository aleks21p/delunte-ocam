#!/usr/bin/python3
"""Haffop privileged helper (Linux).

Run as root through pkexec. Reads a JSON list of operations on stdin, applies
only operations that match a strict allowlist, and prints JSON results.
Deliberately standard-library only and independent from the rest of Haffop.
"""

import json
import os
import re
import shutil
import subprocess
import sys

SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

WRITE_PATTERNS = [re.compile(p) for p in (
    r"^/sys/devices/system/cpu/cpu\d+/cpufreq/(scaling_max_freq|scaling_min_freq|scaling_governor|energy_performance_preference)$",
    r"^/sys/devices/system/cpu/intel_pstate/(no_turbo|max_perf_pct|min_perf_pct)$",
    r"^/sys/devices/system/cpu/cpufreq/boost$",
    r"^/sys/firmware/acpi/platform_profile$",
    r"^/sys/class/powercap/intel-rapl(-mmio)?:\d+/constraint_[01]_power_limit_uw$",
    r"^/sys/class/drm/card\d+/(gt_max_freq_mhz|gt_min_freq_mhz|gt_boost_freq_mhz)$",
    r"^/sys/class/drm/card\d+/device/tile\d+/gt\d+/freq\d+/(max_freq|min_freq)$",
    r"^/sys/class/drm/card\d+/device/(power_dpm_force_performance_level|pp_od_clk_voltage|pp_power_profile_mode)$",
    r"^/sys/class/drm/card\d+/device/hwmon/hwmon\d+/power1_cap$",
)]
REAL_PREFIXES = ("/sys/devices/", "/sys/firmware/acpi/")
VALUE_RE = re.compile(r"^[A-Za-z0-9_ .:+-]{1,64}$")

ARG_RULES = {
    "nvidia-smi": re.compile(r"^(-i|-pl|-lgc|-rgc|-lmc|-rmc|-pm|\d{1,4}(\.\d+)?|\d{1,5},\d{1,5})$"),
    "ryzenadj": re.compile(r"^--(stapm-limit|fast-limit|slow-limit|tctl-temp|apu-skin-temp)=\d{1,6}$|^--(max-performance|power-saving)$"),
}
SYSTEMCTL_UNITS = {"thermald", "thermald.service", "nvidia-powerd", "nvidia-powerd.service"}


def _write(path, value):
    if not any(p.match(path) for p in WRITE_PATTERNS):
        raise PermissionError(f"path not allowed: {path}")
    real = os.path.realpath(path)
    if not real.startswith(REAL_PREFIXES):
        raise PermissionError(f"path resolves outside sysfs: {real}")
    value = str(value)
    if not VALUE_RE.match(value):
        raise ValueError(f"value not allowed: {value!r}")
    with open(real, "w") as fh:
        fh.write(value + "\n")
    return f"{path} <- {value}"


def _exec(argv):
    if not argv or not isinstance(argv, list):
        raise ValueError("empty command")
    name = os.path.basename(argv[0])
    args = [str(a) for a in argv[1:]]
    if name == "systemctl":
        if len(args) != 2 or args[0] not in ("start", "stop", "restart") or args[1] not in SYSTEMCTL_UNITS:
            raise PermissionError(f"systemctl arguments not allowed: {args}")
    elif name in ARG_RULES:
        bad = [a for a in args if not ARG_RULES[name].match(a)]
        if bad:
            raise PermissionError(f"{name} arguments not allowed: {bad}")
    else:
        raise PermissionError(f"command not allowed: {name}")
    exe = shutil.which(name, path=SAFE_PATH)
    if not exe:
        raise FileNotFoundError(f"{name} is not installed")
    res = subprocess.run([exe] + args, capture_output=True, text=True, timeout=60,
                         env={"PATH": SAFE_PATH, "LANG": "C"})
    msg = (res.stdout + res.stderr).strip().splitlines()
    if res.returncode != 0:
        raise RuntimeError(f"{name} failed: {msg[-1] if msg else res.returncode}")
    return f"{name} {' '.join(args)}" + (f": {msg[-1]}" if msg else "")


def _nvml_offset(index, gpc=None, mem=None):
    index = int(index)
    if gpc is not None and not -1000 <= int(gpc) <= 1500:
        raise ValueError("core offset out of range")
    if mem is not None and not -2000 <= int(mem) <= 4000:
        raise ValueError("memory offset out of range")
    import pynvml
    pynvml.nvmlInit()
    try:
        h = pynvml.nvmlDeviceGetHandleByIndex(index)
        if gpc is not None:
            pynvml.nvmlDeviceSetGpcClkVfOffset(h, int(gpc))
        if mem is not None:
            pynvml.nvmlDeviceSetMemClkVfOffset(h, int(mem))
    finally:
        pynvml.nvmlShutdown()
    return f"GPU {index}: core offset {gpc}, memory offset {mem}"


def process(ops):
    results = []
    for op in ops:
        try:
            kind = op.get("op")
            if kind == "write":
                msg = _write(op["path"], op["value"])
            elif kind == "exec":
                msg = _exec(op["argv"])
            elif kind == "nvml_offset":
                msg = _nvml_offset(op["index"], op.get("gpc"), op.get("mem"))
            else:
                raise PermissionError(f"unknown op {kind!r}")
            results.append({"ok": True, "msg": msg})
        except Exception as exc:  # report per-op so one failure doesn't hide the rest
            results.append({"ok": False, "msg": f"{type(exc).__name__}: {exc}"})
    return results


def main():
    if os.geteuid() != 0:
        print(json.dumps([{"ok": False, "msg": "helper must run as root"}]))
        return 1
    try:
        ops = json.loads(sys.stdin.read() or "[]")
        if not isinstance(ops, list) or len(ops) > 512:
            raise ValueError("bad request")
    except ValueError as exc:
        print(json.dumps([{"ok": False, "msg": str(exc)}]))
        return 1
    print(json.dumps(process(ops)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
