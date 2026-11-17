import copy
import json
from pathlib import Path
import subprocess
import threading

import pytest

from haffop import diagnostics as d


@pytest.mark.parametrize("component", ["cpu", "ram", "storage"])
def test_workers_verify_and_report(component, tmp_path):
    output = tmp_path / "result.json"
    code = d.worker_main([json.dumps({"component": component, "seconds": 0.01,
                                      "directory": str(tmp_path), "result_path": str(output)})])
    result = json.loads(output.read_text())
    assert code == 0
    assert result["status"] == "completed"
    assert result["score"] > 0
    assert result["elapsed"] > 0
    if component == "storage":
        assert (tmp_path / "storage-test.bin").stat().st_size == 64 * d.MIB
        assert "cache" in result["detail"].lower()


def test_storage_verification_detects_corruption(tmp_path, monkeypatch):
    original = Path.open

    def corrupt(path, mode="r", *args, **kwargs):
        if path.name == "storage-test.bin" and mode == "rb":
            with original(path, "r+b") as fh:
                fh.write(b"corrupted")
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", corrupt)
    with pytest.raises(RuntimeError, match="verification failed"):
        d._storage(0.01, str(tmp_path))


def test_subprocess_workers_and_temporary_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "DURATIONS", (0.05,))
    result = d.TestRunner(threading.Event(), lambda *_: None).run(
        ["cpu", "ram", "storage"], 0.05, 1, str(tmp_path), combined=True)
    assert {r["component"] for r in result["results"]} == {"cpu", "ram", "storage"}
    assert all(r["status"] == "completed" for r in result["results"])
    assert not list(tmp_path.iterdir())


def test_cancellation_reaps_workers_and_cleans_storage(tmp_path):
    cancel = threading.Event()
    timer = threading.Timer(0.4, cancel.set)
    timer.start()
    try:
        report = d.TestRunner(cancel, lambda *_: None).run(
            ["cpu", "storage"], 30, 1, str(tmp_path), combined=True)
    finally:
        timer.cancel()
    assert report["cancelled"]
    assert all(row["status"] == "cancelled" for row in report["results"])
    assert not list(tmp_path.iterdir())


def test_missing_gpu_and_network_are_explicit(tmp_path, monkeypatch):
    from haffop import opencl

    def missing():
        raise opencl.Unavailable("No GPU runtime installed")

    monkeypatch.setattr(opencl, "OpenCL", missing)
    monkeypatch.setattr(d.shutil, "which", lambda _: None)
    for component in ("gpu", "network"):
        path = tmp_path / (component + ".json")
        d.worker_main([json.dumps({"component": component, "seconds": 0.01, "gpu_index": 0,
                                  "host": "localhost", "port": 5201, "result_path": str(path)})])
        result = json.loads(path.read_text())
        assert result["status"] == "unavailable"
        assert "score" not in result
        assert result["detail"]


def test_network_uses_explicit_peer_and_received_throughput(monkeypatch):
    monkeypatch.setattr(d.shutil, "which", lambda _: "/usr/bin/iperf3")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "end": {"sum_received": {"bits_per_second": 125000000, "seconds": 30}}}), "")

    monkeypatch.setattr(d.subprocess, "run", run)
    result = d._network(30, "192.0.2.1", 5202)
    assert result["score"] == 125
    assert calls[0][0] == ["/usr/bin/iperf3", "-c", "192.0.2.1", "-p", "5202", "-t", "30", "-J"]
    with pytest.raises(ValueError):
        d._network(30, "--help", 5201)


@pytest.mark.parametrize("seconds,workers,components", [
    (0, 1, ["cpu"]), (1801, 1, ["cpu"]), (30, 0, ["cpu"]),
    (30, 1000, ["cpu"]), (30, 1, ["cpu", "cpu"]), (30, 1, ["bogus"]),
])
def test_invalid_runs_never_start(seconds, workers, components, tmp_path):
    with pytest.raises(ValueError):
        d.TestRunner(threading.Event(), lambda *_: None).run(components, seconds, workers, str(tmp_path))
    assert not list(tmp_path.iterdir())


def _report():
    return {"schema": 1, "workload_version": 1, "identity": {"cpu": "test"}, "platform": "test",
            "python": "test", "seconds": 30, "cpu_workers": 1, "combined": False,
            "storage_directory": "/test", "gpu_index": 0, "network_host": "", "network_port": 5201,
            "cancelled": False, "results": [
                {"component": "cpu", "status": "completed", "score": 200, "unit": "MiB/s SHA-256"}]}


def test_comparison_requires_matching_workload_and_success():
    baseline = _report()
    current = copy.deepcopy(baseline)
    current["results"][0]["score"] = 150
    assert d.compare(current, baseline, "cpu")[0] == 75
    for key, value in (("combined", True), ("seconds", 60), ("cpu_workers", 2),
                       ("identity", {"cpu": "different"}), ("platform", "different")):
        changed = copy.deepcopy(current)
        changed[key] = value
        assert d.compare(changed, baseline, "cpu")[0] is None
    for score in (float("nan"), float("inf"), -1, 0, None):
        changed = copy.deepcopy(current)
        changed["results"][0]["score"] = score
        assert d.compare(changed, baseline, "cpu")[0] is None
    current["cancelled"] = True
    assert d.compare(current, baseline, "cpu")[0] is None
