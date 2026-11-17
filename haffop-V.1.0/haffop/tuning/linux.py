"""Linux tuning backend: cpufreq, intel_pstate, RAPL, platform profiles, ryzenadj,
thermald, Intel/AMD GPU sysfs and NVIDIA (shared module)."""

from __future__ import annotations

import glob
import json
import os
import re
import sys
from pathlib import Path

from haffop import nvidia
from haffop.hardware.model import GPUInfo, HardwareSnapshot
from haffop.platform_info import DEBIAN, FEDORA, PlatformInfo
from haffop.tuning import helper as helper_mod
from haffop.tuning.common import classify_cpu, ram_component
from haffop.tuning.model import LOCKED, TUNABLE, UNLOCKED, Component, Control
from haffop.tuning.nvidia_controls import nvidia_component
from haffop.util import is_admin, read, read_int, run, run_full, which

CPU = "/sys/devices/system/cpu"
INSTALLED_HELPER = "/usr/libexec/haffop/haffop-helper"


def parse_amd_od(text: str) -> dict:
    """Parse amdgpu pp_od_clk_voltage into {'sclk': {idx: MHz}, 'mclk': {...}, 'range': {'SCLK': (lo, hi)}}."""
    res: dict = {"sclk": {}, "mclk": {}, "range": {}}
    section = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("OD_SCLK"):
            section = "sclk"
        elif line.startswith("OD_MCLK"):
            section = "mclk"
        elif line.startswith("OD_RANGE"):
            section = "range"
        elif line.startswith("OD_"):
            section = None
        elif section in ("sclk", "mclk") and (m := re.match(r"(\d+):\s*(\d+)\s*Mhz", line, re.I)):
            res[section][int(m.group(1))] = int(m.group(2))
        elif section == "range" and (m := re.match(r"(\w+):\s*(-?\d+)\s*Mhz\s+(-?\d+)\s*Mhz", line, re.I)):
            res["range"][m.group(1).upper()] = (int(m.group(2)), int(m.group(3)))
    return res


def _mhz_khz(path: str) -> int | None:
    v = read_int(path)
    return v // 1000 if v else None


class LinuxTuning:
    def __init__(self, snap: HardwareSnapshot, plat: PlatformInfo) -> None:
        self.snap = snap
        self.plat = plat

    # ================================================================ build
    def components(self) -> list[Component]:
        comps = [self._cpu()]
        for g in self.snap.gpus:
            comps.append(self._gpu(g))
        comps.append(ram_component(self.snap, [{"op": "user", "argv": ["systemctl", "reboot", "--firmware-setup"]}]))
        if self.snap.is_laptop:
            comps.append(self._laptop())
        else:
            # Desktops still benefit from power-limit controls; show them on the CPU page.
            comps[0].controls += self._power_limit_controls(include_unlock=True)
        return comps

    # ---------------------------------------------------------------- cpu
    def _cpu(self) -> Component:
        unlocked, text = classify_cpu(self.snap.cpu.name)
        cpufreq = sorted(glob.glob(f"{CPU}/cpu[0-9]*/cpufreq"), key=lambda p: int(re.search(r"cpu(\d+)", p).group(1)))
        controls: list[Control] = []
        details = [text]
        c0 = cpufreq[0] if cpufreq else ""
        hw_min, hw_max = _mhz_khz(f"{c0}/cpuinfo_min_freq"), _mhz_khz(f"{c0}/cpuinfo_max_freq")
        if c0 and hw_min and hw_max:
            def freq_ops(fname):
                return lambda v: [{"op": "write", "path": f"{p}/{fname}", "value": str(int(v) * 1000)} for p in cpufreq]
            controls.append(Control(
                id="cpu.max_freq", label="Maximum CPU clock", kind="slider", value=_mhz_khz(f"{c0}/scaling_max_freq") or hw_max,
                minimum=hw_min, maximum=hw_max, step=100, unit="MHz", unlock=hw_max, to_ops=freq_ops("scaling_max_freq"),
                help=f"Highest clock the OS may request (hardware max {hw_max} MHz). Lower it to run cooler and quieter."))
            controls.append(Control(
                id="cpu.min_freq", label="Minimum CPU clock", kind="slider", value=_mhz_khz(f"{c0}/scaling_min_freq") or hw_min,
                minimum=hw_min, maximum=hw_max, step=100, unit="MHz", to_ops=freq_ops("scaling_min_freq"),
                help="Raising the floor keeps the CPU fast at idle (more power and heat)."))
        govs = (read(f"{c0}/scaling_available_governors", "") or "").split()
        if govs:
            controls.append(Control(
                id="cpu.governor", label="Frequency governor", kind="choice", value=read(f"{c0}/scaling_governor"),
                options=[(g, g.capitalize()) for g in govs], unlock="performance" if "performance" in govs else None,
                help="'performance' keeps clocks at the top of the allowed range; 'powersave'/'schedutil' scale with load.",
                to_ops=lambda v: [{"op": "write", "path": f"{p}/scaling_governor", "value": v} for p in cpufreq]))
        epps = (read(f"{c0}/energy_performance_available_preferences", "") or "").split()
        if epps:
            controls.append(Control(
                id="cpu.epp", label="Energy/performance preference", kind="choice",
                value=read(f"{c0}/energy_performance_preference"), options=[(e, e.replace("_", " ").capitalize()) for e in epps],
                help="Hint for the CPU's own boost logic (HWP/CPPC). Only adjustable while the governor is not 'performance'.",
                to_ops=lambda v: [{"op": "write", "path": f"{p}/energy_performance_preference", "value": v} for p in cpufreq]))
        if os.path.exists(f"{CPU}/intel_pstate/no_turbo"):
            controls.append(Control(
                id="cpu.turbo", label="Turbo Boost", kind="toggle", value=read(f"{CPU}/intel_pstate/no_turbo") == "0", unlock=True,
                help="Allows clocks above the base frequency.",
                to_ops=lambda v: [{"op": "write", "path": f"{CPU}/intel_pstate/no_turbo", "value": "0" if v else "1"}]))
        elif os.path.exists(f"{CPU}/cpufreq/boost"):
            controls.append(Control(
                id="cpu.turbo", label="Core performance boost", kind="toggle", value=read(f"{CPU}/cpufreq/boost") == "1", unlock=True,
                help="Allows clocks above the base frequency (AMD Precision Boost / acpi-cpufreq boost).",
                to_ops=lambda v: [{"op": "write", "path": f"{CPU}/cpufreq/boost", "value": "1" if v else "0"}]))
        oc = UNLOCKED if unlocked else TUNABLE if controls else LOCKED
        summary = ("Overclockable: multiplier unlocked." if unlocked else
                   "Not overclockable (locked multiplier) - but boost, clock limits and power limits are adjustable.")
        if hw_max:
            details.append(f"Hardware clock range: {hw_min}-{hw_max} MHz. Driver: {read(f'{c0}/scaling_driver')}.")
        return Component(key="cpu", kind="cpu", title=self.snap.cpu.name, device=f"{self.snap.cpu.cores} cores / {self.snap.cpu.threads} threads",
                         oc=oc, summary=summary, details=details, controls=controls)

    # ---------------------------------------------------------------- power limits
    def _rapl_zones(self) -> list[str]:
        return [z for z in ("/sys/class/powercap/intel-rapl:0", "/sys/class/powercap/intel-rapl-mmio:0")
                if os.path.exists(f"{z}/constraint_0_power_limit_uw")]

    def _power_limit_controls(self, include_unlock: bool) -> list[Control]:
        zones = self._rapl_zones()
        if not zones or self.snap.cpu.vendor != "GenuineIntel":
            return []
        info = {}
        for name in ("long_term", "short_term"):
            targets, values, maxes = [], [], []
            for z in zones:
                for c in (0, 1, 2):
                    if read(f"{z}/constraint_{c}_name") == name:
                        targets.append(f"{z}/constraint_{c}_power_limit_uw")
                        if (cur := read_int(f"{z}/constraint_{c}_power_limit_uw")):
                            values.append(cur / 1e6)
                        if (mx := read_int(f"{z}/constraint_{c}_max_power_uw")):
                            maxes.append(mx / 1e6)
            if targets and values:
                info[name] = (targets, values, maxes)
        if "long_term" not in info:
            return []
        pl1_targets, pl1_vals, pl1_max = info["long_term"]
        tdp = max(pl1_max) if pl1_max else max(pl1_vals)  # constraint_0_max_power is the rated TDP
        pl2_now = max(info["short_term"][1]) if "short_term" in info else tdp * 2
        top = round(min(max(pl2_now, tdp * 2, max(pl1_vals)), 400))
        # Effective limit is the lowest one firmware enforces (MSR vs MMIO)
        controls = [Control(
            id="rapl.long_term", label="CPU sustained power limit (PL1)", kind="slider", value=round(min(pl1_vals)),
            minimum=5, maximum=top, step=1, unit="W", unlock=round(min(pl2_now, tdp * 2)) if include_unlock else None,
            help=(f"Power the CPU may draw indefinitely (rated TDP {tdp:.0f} W). Raising it is the biggest laptop speed "
                  "unlock - the CPU holds boost clocks under long loads. Temperatures rise; Intel CPUs still protect "
                  "themselves at TjMax (~100 °C).")
            + (" Applied to both the MSR and the firmware (MMIO) limit - laptops enforce the lower one." if len(pl1_targets) > 1 else ""),
            to_ops=lambda v, t=tuple(pl1_targets): [{"op": "write", "path": p, "value": str(int(v) * 1_000_000)} for p in t])]
        if "short_term" in info:
            t2, v2, _ = info["short_term"]
            controls.append(Control(
                id="rapl.short_term", label="CPU boost power limit (PL2)", kind="slider", value=round(min(v2)), minimum=5,
                maximum=round(min(max(pl2_now * 1.5, top), 400)), step=1, unit="W",
                unlock=round(pl2_now) if include_unlock else None,
                help="Short burst power budget (seconds to a minute). Unlock sets every zone to the highest firmware value.",
                to_ops=lambda v, t=tuple(t2): [{"op": "write", "path": p, "value": str(int(v) * 1_000_000)} for p in t]))
        return controls

    # ---------------------------------------------------------------- laptop unlock
    def _laptop(self) -> Component:
        controls: list[Control] = []
        details = ["Laptops ship with conservative power and temperature limits to stay cool and quiet. "
                   "Unlocking them lets the CPU/GPU hold higher clocks for longer at the cost of higher "
                   "temperatures, fan noise and battery life. Settings reset on reboot (safe to experiment).",
                   "Keep the laptop on a hard surface and plugged in when unlocked."]
        if which("powerprofilesctl"):
            out = run(["powerprofilesctl", "list"])
            profiles, current = [], None
            for line in out.splitlines():
                if (m := re.match(r"^(\*?)\s*([\w-]+):\s*$", line)):
                    profiles.append(m.group(2))
                    if m.group(1):
                        current = m.group(2)
            if profiles:
                controls.append(Control(
                    id="laptop.profile", label="System power profile", kind="choice", value=current,
                    options=[(p, p.replace("-", " ").capitalize()) for p in profiles],
                    unlock="performance" if "performance" in profiles else None,
                    help="Firmware/OS power profile (power-profiles-daemon). 'Performance' unlocks higher fan curves and "
                         "power limits on many laptops.",
                    to_ops=lambda v: [{"op": "user", "argv": ["powerprofilesctl", "set", v]}]))
        elif os.path.exists("/sys/firmware/acpi/platform_profile"):
            choices = (read("/sys/firmware/acpi/platform_profile_choices", "") or "").split()
            controls.append(Control(
                id="laptop.profile", label="Firmware platform profile", kind="choice", value=read("/sys/firmware/acpi/platform_profile"),
                options=[(c, c.replace("-", " ").capitalize()) for c in choices],
                unlock="performance" if "performance" in choices else None,
                help="ACPI platform profile: changes the laptop firmware's fan curve and power limits.",
                to_ops=lambda v: [{"op": "write", "path": "/sys/firmware/acpi/platform_profile", "value": v}]))
        controls += self._power_limit_controls(include_unlock=True)
        if self.snap.cpu.vendor == "AuthenticAMD":
            controls += self._ryzenadj_controls(details)
        if run(["systemctl", "is-active", "thermald"]).strip() == "active" or (
                run(["systemctl", "is-enabled", "thermald"]).strip() in ("enabled", "disabled") and which("thermald")):
            active = run(["systemctl", "is-active", "thermald"]).strip() == "active"
            controls.append(Control(
                id="laptop.thermald", label="Intel thermal daemon (early throttling)", kind="toggle", value=active, unlock=False,
                help="thermald throttles the CPU before it gets hot. Turning it off lets the CPU run up to its own "
                     "hardware limit (TjMax) = higher temperatures but higher sustained speed. Restarts on reboot.",
                to_ops=lambda v: [{"op": "exec", "argv": ["systemctl", "start" if v else "stop", "thermald"]}]))
        if os.path.exists("/usr/lib/systemd/system/nvidia-powerd.service") or os.path.exists("/lib/systemd/system/nvidia-powerd.service"):
            active = run(["systemctl", "is-active", "nvidia-powerd"]).strip() == "active"
            controls.append(Control(
                id="laptop.dynboost", label="NVIDIA Dynamic Boost", kind="toggle", value=active, unlock=True,
                help="Shifts power from the CPU to the NVIDIA GPU under gaming loads (nvidia-powerd).",
                to_ops=lambda v: [{"op": "exec", "argv": ["systemctl", "start" if v else "stop", "nvidia-powerd"]}]))
        if not controls:
            details.append("No laptop power controls were found on this machine.")
        return Component(key="laptop", kind="laptop", title="Laptop performance unlock", device=self.snap.system_name,
                         oc=TUNABLE if controls else LOCKED,
                         summary="Raise power and temperature limits for higher sustained speed (runs hotter).",
                         details=details, controls=controls)

    def _ryzenadj_controls(self, details: list[str]) -> list[Control]:
        if not which("ryzenadj"):
            details.append("AMD laptop: install ryzenadj (https://github.com/FlyGoat/RyzenAdj) to unlock STAPM/PPT power "
                           "limits and the temperature limit." + (" Fedora: available from COPR." if self.plat.edition == FEDORA else
                                                                 " Debian/Ubuntu: build from source or use the PPA." if self.plat.edition == DEBIAN else ""))
            return []
        name = self.snap.cpu.name
        cls = "HX" if re.search(r"\d{4}HX", name) else "H" if re.search(r"\d{4}H|HS\b", name) else "U"
        stock = {"U": (15, 25, 20), "H": (45, 65, 54), "HX": (55, 85, 65)}[cls]
        top = {"U": 35, "H": 80, "HX": 120}[cls]
        values = {}
        if is_admin():
            for line in run(["ryzenadj", "-i"]).splitlines():
                if (m := re.match(r"\|\s*(STAPM LIMIT|PPT LIMIT FAST|PPT LIMIT SLOW|THM LIMIT CORE)\s*\|\s*([\d.]+)", line)):
                    values[m.group(1)] = float(m.group(2))
        controls = []
        for key, label, flag, dflt in (("STAPM LIMIT", "Sustained power (STAPM)", "stapm-limit", stock[0]),
                                       ("PPT LIMIT FAST", "Boost power (fast PPT)", "fast-limit", stock[1]),
                                       ("PPT LIMIT SLOW", "Average power (slow PPT)", "slow-limit", stock[2])):
            controls.append(Control(
                id=f"ryzenadj.{flag}", label=label, kind="slider", value=round(values.get(key, dflt)), minimum=5, maximum=top,
                step=1, unit="W", unlock=top, known=key in values,
                help="Ryzen mobile power limit (ryzenadj). Higher = higher sustained clocks and temperatures.",
                to_ops=lambda v, f=flag: [{"op": "exec", "argv": ["ryzenadj", f"--{f}={int(v) * 1000}"]}]))
        controls.append(Control(
            id="ryzenadj.tctl", label="CPU temperature limit (Tctl)", kind="slider", value=round(values.get("THM LIMIT CORE", 95)),
            minimum=60, maximum=100, step=1, unit="°C", unlock=98, known="THM LIMIT CORE" in values,
            help="Temperature where the CPU starts throttling. Raising it allows hotter, faster operation.",
            to_ops=lambda v: [{"op": "exec", "argv": ["ryzenadj", f"--tctl-temp={int(v)}"]}]))
        return controls

    # ---------------------------------------------------------------- gpus
    def _gpu(self, g: GPUInfo) -> Component:
        if g.vendor == "nvidia":
            smi = nvidia.smi_path()
            row = next((r for r in nvidia.query() if g.index is not None and int(r["index"]) == g.index), None)
            if smi and row:
                return nvidia_component(g, row, smi)
            return Component(key=f"gpu:{g.key}", kind="gpu", title=g.name, device="NVIDIA GPU", oc=LOCKED,
                             summary="NVIDIA proprietary driver not detected (nouveau cannot be tuned).",
                             details=["Install the NVIDIA driver: " + self.plat.install_hint(
                                 {"debian": "nvidia-driver", "fedora": "akmod-nvidia (RPM Fusion)"})])
        if g.vendor == "amd" and g.sysfs:
            return self._amd_gpu(g)
        if g.vendor == "intel" and g.sysfs:
            return self._intel_gpu(g)
        return Component(key=f"gpu:{g.key}", kind="gpu", title=g.name, device="GPU", oc=LOCKED,
                         summary="No tuning interface available for this GPU/driver.")

    def _intel_gpu(self, g: GPUInfo) -> Component:
        card = g.sysfs
        xe = glob.glob(f"{card}/device/tile0/gt0/freq0")
        if xe:
            base = xe[0]
            names = {"min": "min_freq", "max": "max_freq", "rp0": "rp0_freq", "rpn": "rpn_freq"}
        else:
            base = card
            names = {"min": "gt_min_freq_mhz", "max": "gt_max_freq_mhz", "rp0": "gt_RP0_freq_mhz",
                     "rpn": "gt_RPn_freq_mhz", "boost": "gt_boost_freq_mhz"}
        rp0, rpn = read_int(f"{base}/{names['rp0']}"), read_int(f"{base}/{names['rpn']}")
        controls = []
        if rp0 and rpn:
            for key, label, unlock, hlp in (
                    ("max", "Maximum graphics clock", rp0, "Upper clock limit. Set to the hardware max for full speed."),
                    ("min", "Minimum graphics clock", None, "Raising the floor keeps the iGPU fast (more heat, less battery)."),
                    ("boost", "Boost graphics clock", rp0, "Clock used when the driver requests a boost.")):
                if key not in names or read_int(f"{base}/{names[key]}") is None:
                    continue
                controls.append(Control(
                    id=f"igpu.{key}", label=label, kind="slider", value=read_int(f"{base}/{names[key]}"), minimum=rpn,
                    maximum=rp0, step=50, unit="MHz", unlock=unlock, help=hlp,
                    to_ops=lambda v, f=f"{base}/{names[key]}": [{"op": "write", "path": f, "value": str(int(v))}]))
        return Component(
            key=f"gpu:{g.key}", kind="gpu", title=g.name, device="Integrated Intel graphics" if g.integrated else "Intel graphics",
            oc=TUNABLE if controls else LOCKED,
            summary=(f"Not overclockable - Intel graphics cannot exceed the factory max ({rp0} MHz). "
                     "The clock range below can be raised/pinned within that limit."),
            details=["The iGPU shares the CPU's power budget: raising the CPU power limits (laptop unlock) also helps graphics."],
            controls=controls)

    def _amd_gpu(self, g: GPUInfo) -> Component:
        dev = f"{g.sysfs}/device"
        controls = []
        details = []
        levels = ["auto", "low", "high", "manual", "profile_standard", "profile_peak"]
        perf_path = f"{dev}/power_dpm_force_performance_level"
        if os.path.exists(perf_path):
            controls.append(Control(
                id=f"amd{g.key}.perf", label="Performance level", kind="choice", value=read(perf_path),
                options=[(lv, lv.replace("_", " ").capitalize()) for lv in levels], unlock="high",
                help="'high' pins the GPU at its highest clocks; 'manual' is required for custom clocks.",
                to_ops=lambda v: [{"op": "write", "path": perf_path, "value": v}]))
        for hw in glob.glob(f"{dev}/hwmon/hwmon*")[:1]:
            cap, cmax, cmin = read_int(f"{hw}/power1_cap"), read_int(f"{hw}/power1_cap_max"), read_int(f"{hw}/power1_cap_min")
            if cap and cmax and cmax > (cmin or 0):
                cap_path = f"/sys/class/drm/{os.path.basename(g.sysfs)}/device/hwmon/{os.path.basename(hw)}/power1_cap"
                controls.append(Control(
                    id=f"amd{g.key}.power", label="Power limit", kind="slider", value=round(cap / 1e6), minimum=round((cmin or 0) / 1e6),
                    maximum=round(cmax / 1e6), step=1, unit="W", unlock=round(cmax / 1e6),
                    help="Board power limit. Raising it allows higher sustained clocks.",
                    to_ops=lambda v, p=cap_path: [{"op": "write", "path": p, "value": str(int(v) * 1_000_000)}]))
        od_path = f"{dev}/pp_od_clk_voltage"
        od = parse_amd_od(read(od_path, "") or "")
        oc = TUNABLE if controls else LOCKED
        if od["sclk"] and "SCLK" in od["range"]:
            lo, hi = od["range"]["SCLK"]
            top_idx = max(od["sclk"])
            manual = [{"op": "write", "path": perf_path, "value": "manual"}]
            controls.append(Control(
                id=f"amd{g.key}.sclk", label="Max core clock (overclock)", kind="slider", value=od["sclk"][top_idx], minimum=lo,
                maximum=hi, step=5, unit="MHz",
                help=f"Highest GPU core clock state (stock {od['sclk'][top_idx]} MHz). Raise gradually and stress test.",
                to_ops=lambda v, i=top_idx: manual + [{"op": "write", "path": od_path, "value": f"s {i} {int(v)}"},
                                                      {"op": "write", "path": od_path, "value": "c"}]))
            if od["mclk"] and "MCLK" in od["range"]:
                mlo, mhi = od["range"]["MCLK"]
                midx = max(od["mclk"])
                controls.append(Control(
                    id=f"amd{g.key}.mclk", label="Max memory clock (overclock)", kind="slider", value=od["mclk"][midx], minimum=mlo,
                    maximum=mhi, step=5, unit="MHz", help="Highest VRAM clock state.",
                    to_ops=lambda v, i=midx: manual + [{"op": "write", "path": od_path, "value": f"m {i} {int(v)}"},
                                                       {"op": "write", "path": od_path, "value": "c"}]))
            oc = UNLOCKED
            summary = "Overclockable - AMD OverDrive is enabled (core/memory clocks and power limit)."
        else:
            summary = ("Power limit and performance level are adjustable. Clock overclocking needs AMD OverDrive enabled."
                       if controls else "No tuning interface exposed by the amdgpu driver for this GPU.")
            if not g.integrated:
                hint = ('sudo grubby --update-kernel=ALL --args="amdgpu.ppfeaturemask=0xffffffff"' if self.plat.edition == FEDORA else
                        'add amdgpu.ppfeaturemask=0xffffffff to GRUB_CMDLINE_LINUX_DEFAULT in /etc/default/grub, then sudo update-grub'
                        if self.plat.edition == DEBIAN else "add amdgpu.ppfeaturemask=0xffffffff to the kernel command line")
                details.append(f"To enable OverDrive (clock overclocking): {hint} and reboot.")
        return Component(key=f"gpu:{g.key}", kind="gpu", title=g.name,
                         device="Integrated AMD graphics" if g.integrated else "Dedicated AMD GPU",
                         oc=oc, summary=summary, details=details, controls=controls)

    # ================================================================ apply
    def apply(self, ops: list[dict]) -> list[tuple[bool, str]]:
        results: list[tuple[bool, str]] = []
        root_ops = [o for o in ops if o["op"] in ("write", "exec", "nvml_offset")]
        if root_ops:
            results += self._run_root(root_ops)
        for o in ops:
            if o["op"] == "user":
                code, out, err = run_full(o["argv"])
                results.append((code == 0, " ".join(o["argv"]) + ("" if code == 0 else f": {(err or out).strip()}")))
        return results

    def _run_root(self, ops: list[dict]) -> list[tuple[bool, str]]:
        if is_admin():
            return [(r["ok"], r["msg"]) for r in helper_mod.process(ops)]
        if not which("pkexec"):
            return [(False, "pkexec (polkit) is required to apply settings - install " + self.plat.install_hint(
                {"debian": "pkexec", "fedora": "polkit"}))]
        if os.access(INSTALLED_HELPER, os.X_OK):
            argv = ["pkexec", INSTALLED_HELPER]
        elif getattr(sys, "frozen", False):
            argv = ["pkexec", sys.executable, "--haffop-helper"]
        else:
            argv = ["pkexec", sys.executable, str(Path(helper_mod.__file__).resolve())]
        code, out, err = run_full(argv, timeout=300, input_text=json.dumps(ops))
        if code in (126, 127) and not out.strip():
            return [(False, "Authorization was cancelled or denied.")]
        try:
            return [(r["ok"], r["msg"]) for r in json.loads(out)]
        except ValueError:
            return [(False, f"Helper failed: {(err or out).strip() or code}")]
