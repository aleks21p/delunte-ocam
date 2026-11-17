"""Windows 11 tuning backend: power plans, processor power settings, power mode
overlay, cooling policy and NVIDIA (nvidia-smi / NVML)."""

from __future__ import annotations

import re

from haffop import nvidia
from haffop.hardware.model import GPUInfo, HardwareSnapshot
from haffop.platform_info import PlatformInfo
from haffop.tuning.common import classify_cpu, ram_component
from haffop.tuning.model import LOCKED, TUNABLE, UNLOCKED, Component, Control
from haffop.tuning.nvidia_controls import nvidia_component, nvml_offset
from haffop.util import is_admin, run, run_full

ULTIMATE_GUID = "e9a42b02-d5df-448d-aa00-03f14749eb61"
OVERLAYS = [("961cc777-2547-4f9d-8174-7d86181b8a7a", "Best power efficiency"),
            ("00000000-0000-0000-0000-000000000000", "Balanced"),
            ("ded574b5-45a0-4f42-8737-46345c09c238", "Best performance")]
BOOST_MODES = [("0", "Disabled"), ("1", "Enabled"), ("2", "Aggressive"), ("3", "Efficient enabled"),
               ("4", "Efficient aggressive"), ("5", "Aggressive at guaranteed"), ("6", "Efficient aggressive at guaranteed")]


def parse_powercfg_index(text: str) -> tuple[int | None, int | None]:
    """Parse 'Current AC/DC Power Setting Index: 0x...' from `powercfg /q`."""
    ac = re.search(r"Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)", text)
    dc = re.search(r"Current DC Power Setting Index:\s*0x([0-9a-fA-F]+)", text)
    if not ac:  # localized Windows: AC and DC indices are the last two hex values printed
        vals = re.findall(r"0x([0-9a-fA-F]{8})\s*$", text, re.M)
        if len(vals) >= 2:
            return int(vals[-2], 16), int(vals[-1], 16)
    return (int(ac.group(1), 16) if ac else None, int(dc.group(1), 16) if dc else None)


def parse_powercfg_list(text: str) -> tuple[list[tuple[str, str]], str | None]:
    plans, active = [], None
    for m in re.finditer(r"GUID:\s*([0-9a-fA-F-]{36})\s+\((.+?)\)\s*(\*)?", text):
        plans.append((m.group(1).lower(), m.group(2)))
        if m.group(3):
            active = m.group(1).lower()
    return plans, active


def _setting_ops(alias: str, value: int) -> list[dict]:
    return [{"op": "exec", "argv": ["powercfg", "/setacvalueindex", "scheme_current", "sub_processor", alias, str(int(value))]},
            {"op": "exec", "argv": ["powercfg", "/setdcvalueindex", "scheme_current", "sub_processor", alias, str(int(value))]},
            {"op": "exec", "argv": ["powercfg", "/setactive", "scheme_current"]}]


class WindowsTuning:
    def __init__(self, snap: HardwareSnapshot, plat: PlatformInfo) -> None:
        self.snap = snap
        self.plat = plat

    def _proc_setting(self, alias: str) -> int | None:
        return parse_powercfg_index(run(["powercfg", "/q", "scheme_current", "sub_processor", alias]))[0]

    def components(self) -> list[Component]:
        comps = [self._cpu()]
        comps += [self._gpu(g) for g in self.snap.gpus]
        comps.append(ram_component(self.snap, [{"op": "exec", "argv": ["shutdown", "/r", "/fw", "/t", "5"]}]))
        if self.snap.is_laptop:
            comps.append(self._laptop())
        return comps

    def _cpu(self) -> Component:
        unlocked, text = classify_cpu(self.snap.cpu.name)
        controls: list[Control] = []
        plans, active = parse_powercfg_list(run(["powercfg", "/list"]))
        if plans:
            opts = list(plans)
            if not any(g == ULTIMATE_GUID or "ultimate" in n.lower() for g, n in plans):
                opts.append(("create-ultimate", "Ultimate Performance (create)"))
            ultimate = next((g for g, n in opts if "ultimate" in n.lower()), "create-ultimate")
            controls.append(Control(
                id="win.plan", label="Power plan", kind="choice", value=active, options=opts, unlock=ultimate,
                help="Ultimate/High performance plans keep the CPU ready to boost instantly.",
                to_ops=lambda v: [{"op": "win_ultimate"}] if v == "create-ultimate" else
                [{"op": "exec", "argv": ["powercfg", "/setactive", v]}]))
        for alias, label, unlock, hlp in (
                ("PROCTHROTTLEMAX", "Maximum processor state", 100,
                 "Upper CPU speed limit in % (100 % = full boost; 99 % disables turbo and runs much cooler)."),
                ("PROCTHROTTLEMIN", "Minimum processor state", None, "Lowest CPU speed in %. Raising it keeps clocks high at idle.")):
            cur = self._proc_setting(alias)
            if cur is not None:
                controls.append(Control(id=f"win.{alias.lower()}", label=label, kind="slider", value=cur, minimum=0 if "MIN" in alias else 5,
                                        maximum=100, step=1, unit="%", unlock=unlock, help=hlp,
                                        to_ops=lambda v, a=alias: _setting_ops(a, v)))
        boost = self._proc_setting("PERFBOOSTMODE")
        controls.append(Control(
            id="win.boost", label="Processor boost mode", kind="choice", value=str(boost) if boost is not None else "1",
            known=boost is not None, options=BOOST_MODES, unlock="2",
            help="'Aggressive' boosts as high and as often as power/thermal limits allow.",
            to_ops=lambda v: _setting_ops("PERFBOOSTMODE", int(v))))
        details = [text, "Windows changes apply to the active power plan (both plugged-in and battery)."]
        if not is_admin():
            details.append("Run Haffop as administrator to apply changes.")
        return Component(key="cpu", kind="cpu", title=self.snap.cpu.name,
                         device=f"{self.snap.cpu.cores} cores / {self.snap.cpu.threads} threads",
                         oc=UNLOCKED if unlocked else TUNABLE,
                         summary="Overclockable: multiplier unlocked." if unlocked else
                         "Not overclockable (locked multiplier) - boost behaviour and speed limits are adjustable.",
                         details=details, controls=controls)

    def _laptop(self) -> Component:
        controls: list[Control] = []
        cur = re.search(r"([0-9a-fA-F-]{36})", run(["powercfg", "/getactiveoverlayscheme"]))
        controls.append(Control(
            id="win.overlay", label="Windows power mode", kind="choice",
            value=cur.group(1).lower() if cur else OVERLAYS[1][0], known=bool(cur), options=OVERLAYS, unlock=OVERLAYS[2][0],
            help="Settings > System > Power > Power mode. 'Best performance' raises the laptop's power limits "
                 "(applies while the Balanced plan is active).",
            to_ops=lambda v: [{"op": "exec", "argv": ["powercfg", "/overlaysetactive", v]}]))
        cool = self._proc_setting("SYSCOOLPOL")
        controls.append(Control(
            id="win.cooling", label="System cooling policy", kind="choice", value=str(cool) if cool is not None else "1",
            known=cool is not None, options=[("1", "Active (fans first - faster)"), ("0", "Passive (slow CPU first - quieter)")],
            unlock="1", help="Active cooling spins fans up before reducing CPU speed.",
            to_ops=lambda v: _setting_ops("SYSCOOLPOL", int(v))))
        details = ["Unlock raises Windows' power mode, boost aggressiveness and cooling policy so the laptop holds higher "
                   "clocks (hotter and louder). NVIDIA power limits are raised too when the GPU allows it.",
                   "CPU temperature/power-limit (PL1/PL2) changes on Windows need vendor tools (Intel XTU, ThrottleStop, "
                   "the laptop's control centre) - Windows offers no safe built-in API for them.",
                   "Keep the laptop plugged in and on a hard surface when unlocked."]
        return Component(key="laptop", kind="laptop", title="Laptop performance unlock", device=self.snap.system_name,
                         oc=TUNABLE, summary="Raise Windows power limits for higher sustained speed (runs hotter).",
                         details=details, controls=controls)

    def _gpu(self, g: GPUInfo) -> Component:
        if g.vendor == "nvidia":
            smi = nvidia.smi_path()
            row = next((r for r in nvidia.query() if g.index is not None and int(r["index"]) == g.index), None)
            if smi and row:
                return nvidia_component(g, row, smi)
        if g.integrated:
            return Component(key=f"gpu:{g.key}", kind="gpu", title=g.name, device="Integrated graphics", oc=LOCKED,
                             summary="Integrated graphics cannot be overclocked on Windows; it follows the CPU power budget.",
                             details=["Use the laptop unlock / power plan settings to give the iGPU more power headroom."])
        tool = {"amd": "AMD Software: Adrenalin Edition > Performance > Tuning",
                "intel": "Intel Graphics Software (Arc Control) > Performance"}.get(g.vendor, "the GPU vendor's control panel")
        return Component(key=f"gpu:{g.key}", kind="gpu", title=g.name, device="Dedicated graphics", oc=TUNABLE,
                         summary=f"Overclockable through {tool}.",
                         details=["Windows does not expose a public overclocking API for this vendor; use the official tool."])

    # ---------------------------------------------------------------- apply
    def apply(self, ops: list[dict]) -> list[tuple[bool, str]]:
        if not is_admin():
            return [(False, "Administrator rights are required. Use 'Restart as administrator'.")]
        results = []
        for op in ops:
            try:
                if op["op"] in ("exec", "user"):
                    code, out, err = run_full(op["argv"])
                    msg = " ".join(op["argv"])
                    results.append((code == 0, msg if code == 0 else f"{msg}: {(err or out).strip()}"))
                elif op["op"] == "nvml_offset":
                    results.append((True, nvml_offset(op["index"], op.get("gpc"), op.get("mem"))))
                elif op["op"] == "win_ultimate":
                    code, out, err = run_full(["powercfg", "-duplicatescheme", ULTIMATE_GUID])
                    guid = re.search(r"([0-9a-fA-F-]{36})", out)
                    if code == 0 and guid:
                        run_full(["powercfg", "/setactive", guid.group(1)])
                        results.append((True, f"Created and activated Ultimate Performance plan {guid.group(1)}"))
                    else:
                        results.append((False, f"Could not create Ultimate Performance plan: {(err or out).strip()}"))
                else:
                    results.append((False, f"Unsupported operation {op['op']}"))
            except Exception as exc:
                results.append((False, f"{type(exc).__name__}: {exc}"))
        return results
