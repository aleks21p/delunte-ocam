"""Tuning (overclock / unlock) entry point."""

from __future__ import annotations

from haffop.hardware.model import HardwareSnapshot
from haffop.platform_info import PlatformInfo
from haffop.util import IS_WINDOWS


def get_backend(snap: HardwareSnapshot, plat: PlatformInfo):
    if IS_WINDOWS:
        from haffop.tuning.windows import WindowsTuning
        return WindowsTuning(snap, plat)
    from haffop.tuning.linux import LinuxTuning
    return LinuxTuning(snap, plat)
