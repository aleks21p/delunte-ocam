"""Parsers shared by the Linux and Windows backends (pure functions, unit-tested)."""

from __future__ import annotations

import math
import re

# --------------------------------------------------------------------- EDID


def parse_edid(data: bytes) -> dict | None:
    """Decode the base EDID block: maker, model, serial, physical size, native mode."""
    if not data or len(data) < 128 or data[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        return None
    m = (data[8] << 8) | data[9]
    maker = "".join(chr(((m >> s) & 0x1F) + 64) for s in (10, 5, 0))
    info: dict = {
        "manufacturer": maker,
        "product_code": f"{data[10] | (data[11] << 8):04X}",
        "year": 1990 + data[17] if data[17] else None,
        "edid_version": f"{data[18]}.{data[19]}",
        "digital": bool(data[20] & 0x80),
    }
    w_cm, h_cm = data[21], data[22]
    for off in (54, 72, 90, 108):
        block = data[off:off + 18]
        if block[0] or block[1]:  # detailed timing descriptor
            if "native_width" in info:
                continue
            pclk = (block[0] | (block[1] << 8)) * 10_000
            hact = block[2] | ((block[4] & 0xF0) << 4)
            hblank = block[3] | ((block[4] & 0x0F) << 8)
            vact = block[5] | ((block[7] & 0xF0) << 4)
            vblank = block[6] | ((block[7] & 0x0F) << 8)
            info["native_width"], info["native_height"] = hact, vact
            if (hact + hblank) and (vact + vblank):
                info["native_refresh"] = round(pclk / ((hact + hblank) * (vact + vblank)), 2)
            w_mm = block[12] | ((block[14] & 0xF0) << 4)
            h_mm = block[13] | ((block[14] & 0x0F) << 8)
            if w_mm and h_mm:
                info["width_mm"], info["height_mm"] = w_mm, h_mm
        else:
            tag = block[3]
            text = block[5:18].split(b"\n")[0].decode("ascii", "replace").strip()
            if tag == 0xFC:
                info["name"] = text
            elif tag == 0xFF:
                info["serial"] = text
            elif tag == 0xFE and text:
                info.setdefault("text", text)
    if "width_mm" not in info and w_cm and h_cm:
        info["width_mm"], info["height_mm"] = w_cm * 10, h_cm * 10
    if info.get("width_mm"):
        info["diagonal_in"] = round(math.hypot(info["width_mm"], info["height_mm"]) / 25.4, 1)
    return info


# --------------------------------------------------------------------- Wi-Fi


def wifi_standard_from_bitrate(text: str, freq_mhz: float | None = None) -> str:
    """Infer the Wi-Fi generation from an `iw link` bitrate line."""
    if "EHT" in text:
        return "Wi-Fi 7 (802.11be)"
    if "HE-" in text or "HE " in text:
        return "Wi-Fi 6E (802.11ax)" if freq_mhz and freq_mhz >= 5925 else "Wi-Fi 6 (802.11ax)"
    if "VHT" in text:
        return "Wi-Fi 5 (802.11ac)"
    if "MCS" in text:
        return "Wi-Fi 4 (802.11n)"
    return "802.11a/b/g" if text else ""


def parse_iw_link(text: str) -> dict:
    out: dict = {"connected": "Connected to" in text}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("SSID:"):
            out["ssid"] = line[5:].strip()
        elif line.startswith("freq:"):
            try:
                out["freq"] = float(line.split()[1])
            except (IndexError, ValueError):
                pass
        elif line.startswith("signal:"):
            out["signal"] = line.split(":", 1)[1].strip()
        elif line.startswith("tx bitrate:"):
            out["tx"] = line.split(":", 1)[1].strip()
        elif line.startswith("rx bitrate:"):
            out["rx"] = line.split(":", 1)[1].strip()
    if out.get("tx"):
        out["standard"] = wifi_standard_from_bitrate(out["tx"], out.get("freq"))
    return out


def max_wifi_standard_from_iw_phy(text: str) -> str:
    if "EHT Iftypes" in text:
        return "Wi-Fi 7 (802.11be)"
    if "HE Iftypes" in text:
        return "Wi-Fi 6E (802.11ax)" if re.search(r"\* 59[3-9]\d(\.\d+)? MHz|\* 6\d{3}(\.\d+)? MHz", text) else "Wi-Fi 6 (802.11ax)"
    if "VHT Capabilities" in text:
        return "Wi-Fi 5 (802.11ac)"
    if "HT Capabilities" in text or "HT20" in text:
        return "Wi-Fi 4 (802.11n)"
    return ""


def max_wifi_standard_from_radio_types(radio_types: str) -> str:
    """Windows `netsh wlan show drivers` "Radio types supported" line."""
    rt = radio_types.lower()
    if "802.11be" in rt:
        return "Wi-Fi 7 (802.11be)"
    if "802.11ax" in rt:
        return "Wi-Fi 6/6E (802.11ax)"
    if "802.11ac" in rt:
        return "Wi-Fi 5 (802.11ac)"
    if "802.11n" in rt:
        return "Wi-Fi 4 (802.11n)"
    return radio_types.strip()


# --------------------------------------------------------------------- misc


def parse_key_value_blocks(text: str, sep: str = ":") -> list[dict[str, str]]:
    """Split blank-line separated `Key: value` blocks (lspci -vmm, netsh, ...)."""
    blocks, cur = [], {}
    for line in text.splitlines():
        if not line.strip():
            if cur:
                blocks.append(cur)
                cur = {}
            continue
        if sep in line:
            k, v = line.split(sep, 1)
            k = k.strip()
            if k in cur:  # lspci -vmm repeats "Device" for subsystem lines
                k = "S" + k
            cur[k] = v.strip()
    if cur:
        blocks.append(cur)
    return blocks


def strip_pci_id(name: str) -> str:
    """'Intel Corporation [8086]' -> 'Intel Corporation'."""
    return re.sub(r"\s*\[[0-9a-fA-F]{4}\]$", "", name or "").strip()


CHASSIS_LAPTOP = {8, 9, 10, 11, 14, 30, 31, 32}
CHASSIS_NAMES = {
    3: "Desktop", 4: "Low-profile desktop", 6: "Mini tower", 7: "Tower", 8: "Portable",
    9: "Laptop", 10: "Notebook", 11: "Handheld", 13: "All-in-one", 14: "Sub-notebook",
    15: "Space-saving", 17: "Main server chassis", 23: "Rack mount", 30: "Tablet",
    31: "Convertible", 32: "Detachable", 35: "Mini PC", 36: "Stick PC",
}

DDR_TYPES = {20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4", 27: "LPDDR", 28: "LPDDR2",
             29: "LPDDR3", 30: "LPDDR4", 34: "DDR5", 35: "LPDDR5"}
