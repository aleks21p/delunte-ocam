"""Hardware detection entry point."""

from __future__ import annotations

from haffop.util import IS_WINDOWS


def get_backend():
    if IS_WINDOWS:
        from haffop.hardware.windows import WindowsHardware
        return WindowsHardware()
    from haffop.hardware.linux import LinuxHardware
    return LinuxHardware()
