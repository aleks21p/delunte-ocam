import pytest

from haffop.hardware.model import HardwareSnapshot, MemoryModule
from haffop.hardware.parsers import max_wifi_standard_from_radio_types, parse_edid, parse_iw_link
from haffop.tuning import helper
from haffop.tuning.common import classify_cpu, ram_component
from haffop.tuning.linux import parse_amd_od
from haffop.tuning.model import BIOS, LOCKED
from haffop.tuning.windows import parse_powercfg_index, parse_powercfg_list


def _edid() -> bytes:
    e = bytearray(128)
    e[0:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    e[8], e[9] = 0x09, 0xE5  # "BOE"
    e[17], e[18], e[19], e[20] = 28, 1, 4, 0x80
    dtd = bytearray(18)  # 1920x1080@60, 294x165 mm
    pclk = 14850
    dtd[0], dtd[1] = pclk & 0xFF, pclk >> 8
    dtd[2], dtd[3], dtd[4] = 1920 & 0xFF, 280 & 0xFF, ((1920 >> 8) << 4) | (280 >> 8)
    dtd[5], dtd[6], dtd[7] = 1080 & 0xFF, 45, ((1080 >> 8) << 4)
    dtd[12], dtd[13], dtd[14] = 294 & 0xFF, 165, ((294 >> 8) << 4)
    e[54:72] = dtd
    e[72:90] = b"\x00\x00\x00\xfc\x00" + b"Test Panel\n   "
    return bytes(e)


def test_parse_edid_size_and_mode():
    info = parse_edid(_edid())
    assert info["manufacturer"] == "BOE"
    assert info["name"] == "Test Panel"
    assert (info["native_width"], info["native_height"]) == (1920, 1080)
    assert info["native_refresh"] == pytest.approx(60, abs=0.1)
    assert info["diagonal_in"] == pytest.approx(13.3, abs=0.1)
    assert parse_edid(b"garbage") is None


def test_wifi_standard_detection():
    link = parse_iw_link("Connected to aa:bb\n\tSSID: home\n\tfreq: 5180\n\tsignal: -50 dBm\n"
                         "\ttx bitrate: 1201.0 MBit/s 80MHz HE-MCS 11 HE-NSS 2\n")
    assert link["connected"] and link["standard"] == "Wi-Fi 6 (802.11ax)"
    assert parse_iw_link("Connected to x\n\tfreq: 6115\n\ttx bitrate: 2401 MBit/s HE-MCS 11\n")["standard"].startswith("Wi-Fi 6E")
    assert parse_iw_link("Not connected.")["connected"] is False
    assert max_wifi_standard_from_radio_types("802.11b 802.11g 802.11n 802.11a 802.11ac 802.11ax").startswith("Wi-Fi 6")


@pytest.mark.parametrize("name,unlocked", [
    ("Intel(R) Core(TM) i5-8265U CPU @ 1.60GHz", False),
    ("Intel(R) Core(TM) i7-13700K", True),
    ("Intel(R) Core(TM) i9-13980HX", True),
    ("Intel(R) Core(TM) Ultra 9 285K", True),
    ("AMD Ryzen 7 5800X 8-Core Processor", True),
    ("AMD Ryzen 7 5800U with Radeon Graphics", False),
    ("AMD Ryzen AI 9 HX 370 w/ Radeon 890M", True),
])
def test_classify_cpu(name, unlocked):
    assert classify_cpu(name)[0] is unlocked


def test_ram_component_suggests_xmp_on_desktop():
    snap = HardwareSnapshot(ram_total=32 * 1024**3, ram_modules=[
        MemoryModule(slot="A", size=16 * 1024**3, rated_mts=3600, configured_mts=2133, mem_type="DDR4", form_factor="DIMM")] * 2)
    comp = ram_component(snap, [])
    assert comp.oc == BIOS and "XMP" in comp.summary and comp.actions
    snap.is_laptop = True
    assert ram_component(snap, []).oc == LOCKED


def test_parse_amd_od():
    od = parse_amd_od("OD_SCLK:\n0: 500Mhz\n1: 2615Mhz\nOD_MCLK:\n0: 97Mhz\n1: 1250MHz\n"
                      "OD_VDDGFX_OFFSET:\n0mV\nOD_RANGE:\nSCLK:     500Mhz       3150Mhz\nMCLK:      97Mhz       1500Mhz\n")
    assert od["sclk"] == {0: 500, 1: 2615}
    assert od["mclk"][1] == 1250
    assert od["range"]["SCLK"] == (500, 3150)


def test_parse_powercfg():
    en = "    Current AC Power Setting Index: 0x00000064\n    Current DC Power Setting Index: 0x00000032\n"
    assert parse_powercfg_index(en) == (100, 50)
    de = ("    Minimaler möglicher Wert: 0x00000000\n    Index der aktuellen Wechselstromeinstellung: 0x00000002\n"
          "    Index der aktuellen Gleichstromeinstellung: 0x00000001\n")
    assert parse_powercfg_index(de) == (2, 1)
    plans, active = parse_powercfg_list(
        "Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced) *\n"
        "Power Scheme GUID: 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c  (High performance)\n")
    assert active == "381b4222-f694-41f0-9685-ff5bb260df2e" and len(plans) == 2


@pytest.mark.parametrize("op", [
    {"op": "write", "path": "/etc/passwd", "value": "x"},
    {"op": "write", "path": "/sys/devices/system/cpu/cpu0/cpufreq/../../../../../etc/shadow", "value": "1"},
    {"op": "write", "path": "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor", "value": "performance\nx"},
    {"op": "write", "path": "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor", "value": "$(reboot)"},
    {"op": "exec", "argv": ["bash", "-c", "id"]},
    {"op": "exec", "argv": ["nvidia-smi", "-i", "0", "--gom", "0"]},
    {"op": "exec", "argv": ["nvidia-smi", "-pl", "300;id"]},
    {"op": "exec", "argv": ["systemctl", "stop", "sshd"]},
    {"op": "exec", "argv": ["ryzenadj", "--set-coall=1"]},
    {"op": "nvml_offset", "index": 0, "gpc": 5000},
    {"op": "shell", "cmd": "id"},
])
def test_helper_rejects_unsafe_operations(op):
    result = helper.process([op])
    assert result[0]["ok"] is False


def test_helper_reports_each_op_independently():
    res = helper.process([{"op": "exec", "argv": ["bash"]}, {"op": "bogus"}])
    assert [r["ok"] for r in res] == [False, False]
