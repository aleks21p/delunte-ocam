"""Identify which of the three supported editions we are running on."""

from __future__ import annotations

import platform
from dataclasses import dataclass

from haffop.util import IS_LINUX, IS_WINDOWS, read

WINDOWS, DEBIAN, FEDORA, LINUX_OTHER = "windows", "debian", "fedora", "linux"


@dataclass(frozen=True)
class PlatformInfo:
    edition: str
    pretty_name: str

    def install_hint(self, packages: dict[str, str]) -> str:
        """packages maps edition -> package name(s)."""
        pkg = packages.get(self.edition) or packages.get("any")
        if not pkg:
            return ""
        if self.edition == DEBIAN:
            return f"sudo apt install {pkg}"
        if self.edition == FEDORA:
            return f"sudo dnf install {pkg}"
        if self.edition == WINDOWS:
            return pkg
        return f"install '{pkg}' with your package manager"


def _parse_os_release(text: str) -> dict[str, str]:
    data = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip().strip('"')
    return data


def detect() -> PlatformInfo:
    if IS_WINDOWS:
        rel = platform.release()
        build = platform.version()
        name = "Windows 11" if rel == "10" and build.count(".") >= 2 and int(build.split(".")[2]) >= 22000 else f"Windows {rel}"
        return PlatformInfo(WINDOWS, f"{name} (build {build})")
    if IS_LINUX:
        osr = _parse_os_release(read("/etc/os-release", "") or "")
        ids = {osr.get("ID", "")} | set(osr.get("ID_LIKE", "").split())
        pretty = osr.get("PRETTY_NAME", "Linux")
        if ids & {"debian", "ubuntu"}:
            return PlatformInfo(DEBIAN, pretty)
        if ids & {"fedora", "rhel", "centos"}:
            return PlatformInfo(FEDORA, pretty)
        return PlatformInfo(LINUX_OTHER, pretty)
    return PlatformInfo(LINUX_OTHER, platform.platform())
