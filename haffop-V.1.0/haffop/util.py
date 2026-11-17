"""Small helpers shared by every backend."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")

_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW


def run(argv: list[str], timeout: float = 8.0, env: dict | None = None) -> str:
    """Run a command and return stdout ('' on any failure)."""
    try:
        res = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
            creationflags=_NO_WINDOW, env=env, errors="replace",
        )
        return res.stdout if res.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def run_full(argv: list[str], timeout: float = 30.0, input_text: str | None = None) -> tuple[int, str, str]:
    try:
        res = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
            creationflags=_NO_WINDOW, input=input_text, errors="replace",
        )
        return res.returncode, res.stdout, res.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)


def which(name: str) -> str | None:
    return shutil.which(name)


def read(path: str | os.PathLike, default: str | None = None) -> str | None:
    try:
        return Path(path).read_text(errors="replace").strip()
    except OSError:
        return default


def read_bytes(path: str | os.PathLike) -> bytes | None:
    try:
        return Path(path).read_bytes()
    except OSError:
        return None


def read_int(path: str | os.PathLike, default: int | None = None) -> int | None:
    val = read(path)
    if val is None:
        return default
    try:
        return int(val.split()[0])
    except (ValueError, IndexError):
        return default


def human_bytes(n: float | int | None, si: bool = False) -> str:
    if n is None:
        return "Unknown"
    base = 1000.0 if si else 1024.0
    units = ["B", "KB", "MB", "GB", "TB", "PB"] if si else ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    n = float(n)
    for unit in units:
        if abs(n) < base or unit == units[-1]:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= base
    return f"{n:.1f}"


def is_admin() -> bool:
    if IS_WINDOWS:
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0


def resource_path(*parts: str) -> Path:
    """Locate bundled resources both from source and from a PyInstaller build."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base.joinpath(*parts)
