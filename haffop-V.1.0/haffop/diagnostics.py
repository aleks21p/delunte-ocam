"""Isolated, bounded diagnostic workloads and portable result comparison."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Callable

import psutil

from haffop import __version__
from haffop.opencl import Unavailable

DURATIONS = (30, 60, 300, 600, 1800)
COMPONENTS = ("cpu", "ram", "storage", "gpu", "network")
WORKLOAD_VERSION = 1
MIB = 1024**2


def _cpu(seconds: float) -> dict:
    block = bytes(range(256)) * 4096
    expected = hashlib.sha256(block).digest()
    start = time.perf_counter()
    count = 0
    while time.perf_counter() - start < seconds:
        if hashlib.sha256(block).digest() != expected:
            raise RuntimeError("CPU SHA-256 verification failed")
        count += len(block)
    elapsed = time.perf_counter() - start
    return {"score": count / elapsed / MIB, "unit": "MiB/s SHA-256", "elapsed": elapsed,
            "detail": "1 MiB SHA-256 blocks verified; CPU crypto throughput, not a general CPU score."}


def _ram(seconds: float) -> dict:
    size = 16 * MIB
    if psutil.virtual_memory().available < 128 * MIB:
        raise Unavailable("At least 128 MiB of available memory is required.")
    source = bytearray(os.urandom(size))
    target = bytearray(size)
    start = time.perf_counter()
    count = 0
    while time.perf_counter() - start < seconds:
        target[:] = source
        if target != source:
            raise RuntimeError("RAM copy verification failed")
        source[0] ^= 0xFF
        source[-1] ^= 0xFF
        count += size
    elapsed = time.perf_counter() - start
    return {"score": count / elapsed / MIB, "unit": "MiB/s copy+verify", "elapsed": elapsed,
            "detail": "32 MiB buffers; copy plus full comparison. Cache/CPU affected; not all installed RAM tested."}


def _storage(seconds: float, directory: str) -> dict:
    root = Path(directory)
    if not root.is_dir():
        raise ValueError("Choose an existing folder on the drive to test.")
    if shutil.disk_usage(root).free < 256 * MIB:
        raise Unavailable("At least 256 MiB of free space is required on the selected filesystem.")
    block = os.urandom(MIB)
    digest = hashlib.sha256(block).digest()
    path = root / "storage-test.bin"
    written = 0
    start = time.perf_counter()
    with path.open("xb", buffering=0) as fh:
        for _ in range(64):
            if fh.write(block) != len(block):
                raise OSError("Short storage write")
            written += len(block)
        os.fsync(fh.fileno())
    write_elapsed = time.perf_counter() - start
    count = 0
    read_start = time.perf_counter()
    while time.perf_counter() - start < seconds:
        with path.open("rb", buffering=0) as fh:
            for _ in range(64):
                data = fh.read(MIB)
                if len(data) != MIB or hashlib.sha256(data).digest() != digest:
                    raise RuntimeError("Storage read-back verification failed")
                count += len(data)
    elapsed = time.perf_counter() - read_start
    if count == 0:
        with path.open("rb") as fh:
            for _ in range(64):
                data = fh.read(MIB)
                if len(data) != MIB or hashlib.sha256(data).digest() != digest:
                    raise RuntimeError("Storage read-back verification failed")
                count += len(data)
        elapsed = time.perf_counter() - read_start
    return {"score": count / elapsed / MIB, "unit": "MiB/s cached read+verify", "elapsed": time.perf_counter() - start,
            "detail": f"64 MiB temporary file; {written / MIB / write_elapsed:.1f} MiB/s write+fsync. "
                      "Reads may use OS cache; NOT raw SSD speed, SMART health, or a full surface scan."}


def _network(seconds: float, host: str, port: int) -> dict:
    exe = shutil.which("iperf3")
    if not exe:
        raise Unavailable("Install iperf3 and run 'iperf3 -s' on a trusted LAN peer.")
    if not host or host.startswith("-") or any(c.isspace() for c in host):
        raise ValueError("Enter the hostname or IP address of your own iperf3 server (no URL/options).")
    proc = subprocess.run([exe, "-c", host, "-p", str(port), "-t", str(int(seconds)), "-J"],
                          capture_output=True, text=True, timeout=seconds + 15,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        raise RuntimeError(proc.stderr.strip() or "iperf3 did not return JSON") from exc
    if proc.returncode or data.get("error"):
        raise RuntimeError(data.get("error") or proc.stderr or "iperf3 failed")
    return {"score": float(data["end"]["sum_received"]["bits_per_second"]) / 1e6,
            "unit": "Mbit/s TCP", "elapsed": data["end"]["sum_received"]["seconds"],
            "detail": "End-to-end TCP to your selected peer. Uses OS routing: Wi-Fi only if routed over Wi-Fi."}


def worker_main(argv: list[str]) -> int:
    config = {}
    try:
        config = json.loads(argv[0])
        component = config["component"]
        seconds = float(config["seconds"])
        if not math.isfinite(seconds) or not 0 < seconds <= 1800:
            raise ValueError("Workload duration must be in (0, 1800] seconds.")
        if component == "cpu":
            result = _cpu(seconds)
        elif component == "ram":
            result = _ram(seconds)
        elif component == "storage":
            result = _storage(seconds, config["directory"])
        elif component == "gpu":
            from haffop.opencl import benchmark
            result = benchmark(seconds, int(config["gpu_index"]))
        elif component == "network":
            result = _network(seconds, config["host"], int(config["port"]))
        else:
            raise ValueError(f"Unknown test component: {component}")
        result["status"] = "completed"
    except Unavailable as exc:
        result = {"status": "unavailable", "detail": str(exc)}
    except Exception as exc:
        result = {"status": "failed", "detail": f"{type(exc).__name__}: {exc}"}
    result["app_version"] = __version__
    if config.get("result_path"):
        Path(config["result_path"]).write_text(json.dumps(result), encoding="utf-8")
    elif sys.stdout is not None:
        print(json.dumps(result), flush=True)
    return 0


def worker_command(config: dict) -> list[str]:
    args = ["--haffop-test-worker", json.dumps(config)]
    return [sys.executable, *args] if getattr(sys, "frozen", False) else [sys.executable, "-m", "haffop", *args]


def stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    # iperf3 is the only possible grandchild. Never kill by process name.
    try:
        children = psutil.Process(proc.pid).children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    for child in children:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            continue
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)
    _, alive = psutil.wait_procs(children, timeout=1)
    for child in alive:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            continue


class TestRunner:
    def __init__(self, cancel: threading.Event, progress: Callable[[int, str], None]) -> None:
        self.cancel = cancel
        self.progress = progress

    def run(self, components: list[str], seconds: int, workers: int, directory: str,
            gpu_index: int = 0, host: str = "", port: int = 5201, combined: bool = False,
            identity: dict | None = None) -> dict:
        if seconds not in DURATIONS or not 1 <= workers <= min(32, psutil.cpu_count() or 1):
            raise ValueError("Unsupported duration or CPU worker count.")
        if not components or len(set(components)) != len(components) or any(c not in COMPONENTS for c in components):
            raise ValueError("Select valid, unique components.")
        if not 1 <= port <= 65535 or not 0 <= gpu_index <= 31:
            raise ValueError("Invalid network port or GPU index.")
        groups = [components] if combined else [[c] for c in components]
        results = []
        for i, group in enumerate(groups):
            if self.cancel.is_set():
                break
            # Parent owns the directory, so cancellation/crashes still remove the test file.
            with tempfile.TemporaryDirectory(prefix="haffop-test-", dir=directory if "storage" in group else None) as tmp:
                configs = [
                    {"component": c, "seconds": seconds, "directory": tmp, "gpu_index": gpu_index,
                     "host": host, "port": port}
                    for c in group for _ in range(workers if c == "cpu" else 1)
                ]
                for n, config in enumerate(configs):
                    config["result_path"] = str(Path(tmp) / f"result-{n}.json")
                results.extend(self._group(configs, seconds, i, len(groups)))
        return {"schema": 1, "app_version": __version__, "workload_version": WORKLOAD_VERSION,
                "created": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
                "python": platform.python_version(), "identity": identity or {}, "seconds": seconds,
                "cpu_workers": workers, "combined": combined, "storage_directory": str(Path(directory).resolve()),
                "gpu_index": gpu_index, "network_host": host, "network_port": port,
                "cancelled": self.cancel.is_set(), "results": results}

    def _group(self, configs: list[dict], seconds: int, index: int, total: int) -> list[dict]:
        processes = []
        start = time.monotonic()
        try:
            env = os.environ.copy()
            if not getattr(sys, "frozen", False):
                env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent) + os.pathsep + env.get("PYTHONPATH", "")
            for config in configs:
                proc = subprocess.Popen(worker_command(config), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        env=env,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                processes.append((config, proc))
            timed_out = False
            while any(proc.poll() is None for _, proc in processes):
                elapsed = time.monotonic() - start
                if self.cancel.is_set() or elapsed > seconds + 30:
                    timed_out = not self.cancel.is_set()
                    break
                self.progress(int(100 * (index + min(elapsed / seconds, 0.99)) / total),
                              f"Running {', '.join(dict.fromkeys(c['component'] for c in configs))}: "
                              f"{int(elapsed)} / {seconds} s")
                self.cancel.wait(0.2)
            results = []
            for config, proc in processes:
                interrupted = proc.poll() is None
                stop_process(proc)
                if interrupted or self.cancel.is_set():
                    result = {"status": "cancelled" if self.cancel.is_set() else "failed",
                              "detail": "Stopped by user or safety guard." if self.cancel.is_set() else "Workload timed out."}
                elif proc.returncode:
                    result = {"status": "failed", "detail": f"Worker exited with code {proc.returncode}."}
                else:
                    try:
                        result = json.loads(Path(config["result_path"]).read_text(encoding="utf-8"))
                    except (OSError, ValueError) as exc:
                        result = {"status": "failed", "detail": f"Missing/invalid worker response: {exc}"}
                result["component"] = config["component"]
                results.append(result)
            if timed_out:
                self.progress(int(100 * (index + 1) / total), "One or more workloads timed out.")
            return results
        finally:
            for _, proc in processes:
                stop_process(proc)


def compare(current: dict, baseline: dict, component: str) -> tuple[float | None, str]:
    if current.get("cancelled") or baseline.get("cancelled"):
        return None, "Cancelled runs cannot be compared."
    keys = ("schema", "workload_version", "identity", "platform", "python", "seconds", "cpu_workers",
            "combined", "storage_directory", "gpu_index", "network_host", "network_port")
    if any(key not in current or current[key] != baseline.get(key) for key in keys):
        return None, "Settings, hardware, runtime or OS differ from the baseline."
    left = [r for r in current.get("results", []) if r.get("component") == component]
    right = [r for r in baseline.get("results", []) if r.get("component") == component]
    if not left or len(left) != len(right):
        return None, "No matching baseline workload."
    for row in left + right:
        score = row.get("score")
        if (row.get("status") != "completed" or not isinstance(score, (int, float))
                or not math.isfinite(score) or score <= 0):
            return None, "Only completed, positive finite scores can be compared."
    if [(r.get("unit"), r.get("device")) for r in left] != [(r.get("unit"), r.get("device")) for r in right]:
        return None, "Units or actual GPU devices differ."
    ratio = sum(r["score"] for r in left) / sum(r["score"] for r in right) * 100
    return ratio, f"{ratio:.1f}% of saved baseline (not a factory health rating)."
