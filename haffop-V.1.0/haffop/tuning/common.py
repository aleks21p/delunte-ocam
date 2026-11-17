"""Edition-independent overclocking knowledge: CPU unlock detection and RAM/XMP analysis."""

from __future__ import annotations

import re

from haffop.hardware.model import HardwareSnapshot
from haffop.tuning.model import BIOS, LOCKED, UNLOCKED, Component

_INTEL_UNLOCKED = re.compile(r"(?:i[3579]-\d{4,5}|Ultra \d+ \d{3}|Core\(TM\) Ultra \d+ \d{3})(?:K|KF|KS|HK|HX|X|XE)\b", re.I)
_AMD_MOBILE = re.compile(r"Ryzen.*?(?:\d{4}(?:U|H|HS|HX|HX3D|E)\b|\bAI\b|Z\d)", re.I)


def classify_cpu(name: str) -> tuple[bool | None, str]:
    """Return (multiplier unlocked?, explanation)."""
    if re.search(r"Intel", name, re.I):
        if _INTEL_UNLOCKED.search(name):
            return True, ("Unlocked multiplier (K/X/HX series). Real multiplier/voltage overclocking is done in the "
                          "BIOS/UEFI or Intel XTU; this app controls boost, clock limits and power limits.")
        return False, ("Locked multiplier - this Intel CPU cannot run above its rated max boost. You can still make "
                       "sure it reaches full boost more often by raising power/thermal limits.")
    if re.search(r"AMD|Ryzen|Threadripper|Athlon|EPYC", name, re.I):
        if re.search(r"EPYC|Athlon Silver|Athlon Gold|A\d-\d{4}|E\d-\d{4}", name, re.I):
            return False, "Locked multiplier - this AMD CPU cannot be overclocked."
        if _AMD_MOBILE.search(name):
            if re.search(r"HX", name, re.I):
                return True, ("Mobile HX Ryzen - partially unlocked. Curve/PBO options depend on the laptop BIOS; this "
                              "app raises STAPM/PPT power limits and the temperature limit (ryzenadj).")
            return False, ("Mobile Ryzen - multiplier locked, but power limits (STAPM / fast / slow PPT) and the "
                           "temperature limit can be raised with ryzenadj for higher sustained clocks.")
        return True, ("Unlocked multiplier (all desktop Ryzen/Threadripper). Use PBO / Curve Optimizer in BIOS or "
                      "Ryzen Master for real overclocking; this app controls boost and clock limits.")
    return None, "Unknown CPU family - only the generic frequency controls are available."


def ram_component(snap: HardwareSnapshot, firmware_ops: list[dict]) -> Component:
    mods = snap.ram_modules
    total = snap.ram_total
    types = sorted({m.mem_type for m in mods if m.mem_type})
    details = []
    slowest_rated = min((m.rated_mts for m in mods if m.rated_mts), default=None)
    running = max((m.configured_mts for m in mods if m.configured_mts), default=None)
    for m in mods:
        line = f"{m.slot}: {m.size / 1024**3:.0f} GiB {m.mem_type} {m.manufacturer} {m.part_number}".strip()
        if m.rated_mts:
            line += f" - rated {m.rated_mts} MT/s"
        if m.configured_mts:
            line += f", running {m.configured_mts} MT/s"
        details.append(line)
    rated_values = {m.rated_mts for m in mods if m.rated_mts}
    if len(rated_values) > 1:
        details.append(f"Mixed modules: all sticks run at the slowest module's speed ({slowest_rated} MT/s). "
                       "Matching kits overclock much better.")
    soldered = any(m.mem_type.startswith("LPDDR") or (m.form_factor or "").lower() in ("row of chips", "chip") for m in mods)
    sodimm = any("SODIMM" in (m.form_factor or "").upper() for m in mods)
    if not mods:
        oc, summary = BIOS, "Per-module details unavailable. RAM speed can only be changed in BIOS/UEFI."
    elif soldered:
        oc, summary = LOCKED, "Soldered (LPDDR) memory - speed is fixed by the manufacturer."
    elif snap.is_laptop or sodimm:
        oc, summary = LOCKED, ("Laptop SO-DIMM memory - most laptop BIOSes lock RAM timings. If your BIOS has an "
                               "XMP/EXPO or memory-overclock option you can use it there.")
        if running and slowest_rated and running < slowest_rated:
            summary += (f" Note: modules are rated {slowest_rated} MT/s but run at {running} MT/s - the CPU's "
                        "memory controller limit or the BIOS caps the speed.")
    else:
        oc = BIOS
        summary = ("Desktop RAM can be overclocked, but only from BIOS/UEFI (XMP for Intel, EXPO/DOCP for AMD). "
                   "Operating systems cannot change memory clocks or timings at runtime.")
        if running and slowest_rated and running < slowest_rated:
            summary += (f" Your RAM runs at {running} MT/s but is rated for {slowest_rated} MT/s: enable XMP/EXPO "
                        "in BIOS for a free speed boost.")
        elif running and slowest_rated and running >= slowest_rated:
            summary += " RAM already runs at (or above) its rated speed."
    total_txt = f"{total / 1024**3:.1f} GiB total" + (f" {', '.join(types)}" if types else "")
    if snap.ram_slots_total:
        total_txt += f" - {len(mods)} of {snap.ram_slots_total} slots used"
    comp = Component(key="ram", kind="ram", title="Memory (RAM)", device=total_txt, oc=oc, summary=summary, details=details)
    if oc in (BIOS, UNLOCKED) or (snap.is_laptop and mods):
        comp.actions.append(("Reboot into BIOS / UEFI setup", firmware_ops,
                             "The computer will restart straight into the firmware setup.\nSave your work first. Continue?"))
    return comp
