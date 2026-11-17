from types import SimpleNamespace

import pytest

from haffop.hardware import linux, windows
from haffop.hardware.model import GPUInfo, HardwareSnapshot, SensorReading
from haffop.hardware.sensors import select_cpu_temperature, valid_temperature


def test_cpu_prefers_die_over_control_and_hottest_package_over_first():
    candidates = [SensorReading("CPU", name, value, "°C") for name, value in
                  [("Tctl", 85), ("Tdie", 65), ("Tccd1", 90)]]
    select_cpu_temperature(candidates)
    assert [r.label for r in candidates if r.key == "cpu"] == ["Tdie"]
    candidates = [SensorReading("CPU", f"Package id {i}", value, "°C") for i, value in enumerate((55, 75))]
    select_cpu_temperature(candidates)
    assert [r.value for r in candidates if r.key == "cpu"] == [75]


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -273, 200])
def test_invalid_temperatures_are_rejected(value):
    assert not valid_temperature(value)


def test_windows_no_acpi_cpu_guess_or_null_to_zero(monkeypatch):
    data = {"zones": [{"InstanceName": "ACPI", "CurrentTemperature": 3200}],
            "lhmhw": [{"Identifier": "/cpu/0", "Name": "Test CPU", "HardwareType": "Cpu"}],
            "lhm": [{"Parent": "/cpu/0", "Name": "CPU Package", "SensorType": "Temperature", "Value": None}]}
    monkeypatch.setattr(windows, "ps_json", lambda *a, **k: data)
    monkeypatch.setattr(windows.nvidia, "query", lambda: [])
    readings = windows.WindowsHardware().read_sensors()
    assert all(r.key != "cpu" for r in readings)
    assert not any(r.value == 0 and r.unit == "°C" for r in readings)
    assert any(r.group == "ACPI thermal zone" for r in readings)


def test_windows_hottest_package_and_unambiguous_gpu_mapping(monkeypatch):
    data = {"lhmhw": [
        {"Identifier": "/cpu/0", "Name": "Test CPU", "HardwareType": "Cpu"},
        {"Identifier": "/gpu/0", "Name": "", "HardwareType": "GpuNvidia"}],
        "lhm": [
            {"Parent": "/cpu/0", "Name": "CPU Package", "SensorType": "Temperature", "Value": 60},
            {"Parent": "/cpu/0", "Name": "CPU Package #2", "SensorType": "Temperature", "Value": 75},
            {"Parent": "/gpu/0", "Name": "GPU Core", "SensorType": "Temperature", "Value": 50}]}
    monkeypatch.setattr(windows, "ps_json", lambda *a, **k: data)
    monkeypatch.setattr(windows.nvidia, "query", lambda: [])
    backend = windows.WindowsHardware()
    backend.snapshot = HardwareSnapshot(gpus=[GPUInfo("x", "NVIDIA test", "nvidia", False)])
    readings = backend.read_sensors()
    assert [r.value for r in readings if r.key == "cpu"] == [75]
    assert not any(r.key == "gpu:x" for r in readings)


def test_linux_acpi_not_cpu_and_tdie_selected(monkeypatch):
    backend = linux.LinuxHardware()
    monkeypatch.setattr(backend, "_hwmon_semantic_keys", lambda _: [])
    monkeypatch.setattr(backend, "_rapl_power", lambda: [])
    monkeypatch.setattr(linux.nvidia, "query", lambda: [])
    monkeypatch.setattr(linux.psutil, "sensors_temperatures", lambda: {
        "acpitz": [SimpleNamespace(label="", current=40)],
        "k10temp": [SimpleNamespace(label="Tctl", current=85), SimpleNamespace(label="Tdie", current=65)]})
    readings = backend.read_sensors()
    assert [r.value for r in readings if r.key == "cpu"] == [65]
    assert not any(r.key == "cpu" and "ACPI" in r.group for r in readings)
