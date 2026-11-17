"""Linux hardware detection (Debian/Ubuntu and Fedora editions).

Uses only unprivileged sources where possible: sysfs, /proc, udev's DMI memory
properties, lspci, lsblk and iw. dmidecode is used as a fallback when root.
"""

from __future__ import annotations

import glob
import json
import os
import re
import time

import psutil

from haffop import nvidia
from haffop.hardware.model import (CPUInfo, DiskInfo, GPUInfo, HardwareSnapshot, MemoryModule,
                                   SensorReading, WifiInfo)
from haffop.hardware.parsers import (CHASSIS_LAPTOP, CHASSIS_NAMES, max_wifi_standard_from_iw_phy,
                                     parse_edid, parse_iw_link, parse_key_value_blocks, strip_pci_id)
from haffop.util import human_bytes, is_admin, read, read_bytes, read_int, run, which

DMI = "/sys/class/dmi/id"

_HWMON_GROUP_NAMES = {
    "coretemp": "CPU (Intel)", "k10temp": "CPU (AMD)", "zenpower": "CPU (AMD)",
    "amdgpu": "GPU (AMD)", "nouveau": "GPU (NVIDIA)", "nvme": "NVMe drive", "drivetemp": "SATA drive",
    "acpitz": "ACPI thermal zone", "iwlwifi_1": "Wi-Fi card", "pch_cannonlake": "Chipset (PCH)",
    "thinkpad": "ThinkPad EC", "spd5118": "RAM (DDR5 SPD)", "jc42": "RAM (DIMM)",
    "x86_pkg_temp": "CPU package",
}


class LinuxHardware:
    def __init__(self) -> None:
        self._pci: dict[str, dict] = {}
        self._rapl_prev: dict[str, tuple[float, int]] = {}
        self.snapshot: HardwareSnapshot | None = None

    # ================================================================ detect
    def detect(self) -> HardwareSnapshot:
        snap = HardwareSnapshot()
        self._pci = self._load_pci()
        self._usb_names = self._load_usb_names()
        steps = [self._system, self._cpu, self._memory, self._gpus, self._displays, self._storage,
                 self._network, self._bluetooth, self._audio, self._camera, self._input, self._usb,
                 self._battery, self._pci_devices]
        for step in steps:
            try:
                step(snap)
            except Exception as exc:  # one broken source must not hide everything else
                snap.warnings.append(f"{step.__name__.strip('_')}: {exc}")
        self.snapshot = snap
        return snap

    # ---------------------------------------------------------------- pci
    def _load_pci(self) -> dict[str, dict]:
        out = run(["lspci", "-vmm", "-nn", "-D", "-k"])
        devices = {}
        for blk in parse_key_value_blocks(out):
            if "Slot" in blk:
                devices[blk["Slot"].lower()] = blk
        return devices

    @staticmethod
    def _load_usb_names() -> dict[str, str]:
        names = {}
        for line in run(["lsusb"]).splitlines():
            m = re.search(r"ID ([0-9a-f]{4}:[0-9a-f]{4})\s+(.*)", line)
            if m:
                names[m.group(1)] = m.group(2).strip()
        return names

    def _usb_device_name(self, dev: str) -> str:
        """Name for a USB device dir (walks up from interfaces to the device)."""
        dev = os.path.realpath(dev)
        for _ in range(3):
            if os.path.exists(f"{dev}/idVendor"):
                vidpid = f"{read(f'{dev}/idVendor')}:{read(f'{dev}/idProduct')}"
                own = f"{read(f'{dev}/manufacturer', '')} {read(f'{dev}/product', '')}".strip()
                return self._usb_names.get(vidpid) or own or vidpid
            dev = os.path.dirname(dev)
        return ""

    def _pci_name(self, slot: str) -> str:
        d = self._pci.get(slot.lower())
        if not d:
            return ""
        return f"{strip_pci_id(d.get('Vendor', ''))} {strip_pci_id(d.get('Device', ''))}".strip()

    @staticmethod
    def _slot_of(sys_device_path: str) -> str:
        try:
            real = os.path.realpath(sys_device_path)
        except OSError:
            return ""
        m = re.findall(r"([0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7])", real)
        return m[-1] if m else ""

    # ---------------------------------------------------------------- system
    def _system(self, snap: HardwareSnapshot) -> None:
        chassis = read_int(f"{DMI}/chassis_type", 0) or 0
        has_battery = bool(glob.glob("/sys/class/power_supply/BAT*"))
        snap.is_laptop = chassis in CHASSIS_LAPTOP or has_battery
        vendor = read(f"{DMI}/sys_vendor", "")
        product = read(f"{DMI}/product_name", "")
        snap.system_name = product if product.lower().startswith(vendor.lower()) else f"{vendor} {product}".strip()
        snap.add("System", snap.system_name or "Computer", [
            ("Manufacturer", vendor), ("Product", product),
            ("Version", read(f"{DMI}/product_version")), ("Family", read(f"{DMI}/product_family")),
            ("SKU", read(f"{DMI}/product_sku")),
            ("Chassis", CHASSIS_NAMES.get(chassis, f"Type {chassis}") if chassis else None),
            ("Form factor", "Laptop / portable" if snap.is_laptop else "Desktop"),
            ("Firmware", "UEFI" if os.path.isdir("/sys/firmware/efi") else "Legacy BIOS"),
            ("Kernel", os.uname().release), ("Architecture", os.uname().machine),
        ])
        snap.add("Motherboard & BIOS", "Motherboard", [
            ("Manufacturer", read(f"{DMI}/board_vendor")), ("Model", read(f"{DMI}/board_name")),
            ("Revision", read(f"{DMI}/board_version")), ("Serial", read(f"{DMI}/board_serial")),
        ])
        snap.add("Motherboard & BIOS", "BIOS / UEFI", [
            ("Vendor", read(f"{DMI}/bios_vendor")), ("Version", read(f"{DMI}/bios_version")),
            ("Release date", read(f"{DMI}/bios_date")), ("Revision", read(f"{DMI}/bios_release")),
            ("EC firmware", read(f"{DMI}/ec_firmware_release")),
        ])

    # ---------------------------------------------------------------- cpu
    def _cpu(self, snap: HardwareSnapshot) -> None:
        info: dict[str, str] = {}
        sockets = set()
        for blk in parse_key_value_blocks(read("/proc/cpuinfo", "") or ""):
            sockets.add(blk.get("physical id", "0"))
            if not info:
                info = blk
        name = re.sub(r"\s+", " ", info.get("model name", "") or info.get("Model", "Unknown CPU"))
        cpu = CPUInfo(name=name, vendor=info.get("vendor_id", ""),
                      cores=psutil.cpu_count(logical=False) or 0, threads=psutil.cpu_count() or 0)
        base = "/sys/devices/system/cpu/cpu0/cpufreq"
        if (v := read_int(f"{base}/cpuinfo_max_freq")):
            cpu.max_mhz = v / 1000
        if (v := read_int(f"{base}/cpuinfo_min_freq")):
            cpu.min_mhz = v / 1000
        if (v := read_int(f"{base}/base_frequency")):
            cpu.base_mhz = v / 1000
        elif (m := re.search(r"@ ([\d.]+)GHz", name)):
            cpu.base_mhz = float(m.group(1)) * 1000
        snap.cpu = cpu

        caches = []
        try:
            lscpu = json.loads(run(["lscpu", "-J", "-C"]) or "{}")
            for c in lscpu.get("caches", []):
                caches.append((f"Cache {c.get('name')}", f"{c.get('all-size')} ({c.get('ways')}-way)"))
        except ValueError:
            pass
        snap.add("Processor", name, [
            ("Vendor", cpu.vendor), ("Sockets", len(sockets)), ("Cores", cpu.cores), ("Threads", cpu.threads),
            ("Base clock", f"{cpu.base_mhz:.0f} MHz" if cpu.base_mhz else None),
            ("Max boost clock", f"{cpu.max_mhz:.0f} MHz" if cpu.max_mhz else None),
            ("Min clock", f"{cpu.min_mhz:.0f} MHz" if cpu.min_mhz else None),
            ("Family / model / stepping",
             f"{info.get('cpu family')} / {info.get('model')} / {info.get('stepping')}" if "cpu family" in info else None),
            ("Microcode", info.get("microcode")),
            ("Frequency driver", read(f"{base}/scaling_driver")),
            ("Governor", read(f"{base}/scaling_governor")),
            *caches,
            ("Virtualization", "VT-x" if " vmx" in info.get("flags", "") else "AMD-V" if " svm" in info.get("flags", "") else None),
            ("Instruction sets", " ".join(f for f in ("sse4_2", "avx", "avx2", "avx512f", "aes", "sha_ni")
                                          if f in info.get("flags", "").split())),
        ])

    # ---------------------------------------------------------------- memory
    def _memory(self, snap: HardwareSnapshot) -> None:
        snap.ram_total = psutil.virtual_memory().total
        props = {}
        for line in run(["udevadm", "info", "--query=property", f"--path={DMI}"]).splitlines():
            if line.startswith(("MEMORY_", "E: MEMORY_")) and "=" in line:
                k, v = line.split("=", 1)
                props[k.removeprefix("E: ")] = v
        modules: list[MemoryModule] = []
        slots = 0
        if props:
            count = int(props.get("MEMORY_ARRAY_NUM_DEVICES", "0") or 0) or 1 + max(
                (int(m.group(1)) for k in props if (m := re.match(r"MEMORY_DEVICE_(\d+)_", k))), default=-1)
            slots = count
            for i in range(count):
                p = lambda f: props.get(f"MEMORY_DEVICE_{i}_{f}")  # noqa: E731
                size = int(p("SIZE") or 0)
                mod = MemoryModule(
                    slot=p("LOCATOR") or f"Slot {i}", size=size,
                    rated_mts=int(p("SPEED_MTS")) if p("SPEED_MTS") else None,
                    configured_mts=int(p("CONFIGURED_SPEED_MTS")) if p("CONFIGURED_SPEED_MTS") else None,
                    mem_type=p("TYPE") or "", form_factor=p("FORM_FACTOR") or "",
                    manufacturer=p("MANUFACTURER") or "", part_number=(p("PART_NUMBER") or "").strip(),
                    extra={"Bank": p("BANK_LOCATOR"), "Rank": p("RANK"), "Data width": p("DATA_WIDTH"),
                           "Configured voltage": f"{int(p('CONFIGURED_VOLTAGE')) / 1000:.2f} V" if (p("CONFIGURED_VOLTAGE") or "").isdigit() and int(p("CONFIGURED_VOLTAGE")) >= 500 else None,
                           "Serial": p("SERIAL_NUMBER")},
                )
                modules.append(mod)
            if props.get("MEMORY_ARRAY_MAX_CAPACITY"):
                snap.add("Memory", "Memory controller", [
                    ("Maximum supported", human_bytes(int(props["MEMORY_ARRAY_MAX_CAPACITY"]))),
                    ("Slots", count), ("ECC", props.get("MEMORY_ARRAY_EC_TYPE")),
                ])
        elif is_admin() and which("dmidecode"):
            modules = self._dmidecode_memory()
            slots = len(modules)
        else:
            snap.warnings.append("Per-module RAM details need udev DMI data or root (dmidecode).")
        snap.ram_slots_total = slots or None
        snap.ram_modules = [m for m in modules if m.size]
        for m in modules:
            if not m.size:
                snap.add("Memory", f"{m.slot} (empty)", [("Status", "Empty slot")])
                continue
            snap.add("Memory", f"{m.slot}: {human_bytes(m.size)} {m.mem_type}", [
                ("Size", human_bytes(m.size)), ("Type", m.mem_type), ("Form factor", m.form_factor),
                ("Rated speed", f"{m.rated_mts} MT/s" if m.rated_mts else None),
                ("Running speed", f"{m.configured_mts} MT/s" if m.configured_mts else None),
                ("Manufacturer", m.manufacturer), ("Part number", m.part_number),
                *[(k, v) for k, v in m.extra.items()],
            ])

    def _dmidecode_memory(self) -> list[MemoryModule]:
        mods = []
        for blk in run(["dmidecode", "-t", "17"]).split("\n\n"):
            if "Memory Device" not in blk:
                continue
            f = dict(re.findall(r"^\s+([^:\n]+):\s*(.*)$", blk, re.M))
            size_s = f.get("Size", "")
            m = re.match(r"(\d+)\s*(MB|GB)", size_s)
            size = int(m.group(1)) * (1 << (20 if m.group(2) == "MB" else 30)) if m else 0
            num = lambda s: int(re.match(r"\d+", s).group()) if re.match(r"\d+", s or "") else None  # noqa: E731
            mods.append(MemoryModule(slot=f.get("Locator", ""), size=size, rated_mts=num(f.get("Speed")),
                                     configured_mts=num(f.get("Configured Memory Speed")),
                                     mem_type=f.get("Type", ""), form_factor=f.get("Form Factor", ""),
                                     manufacturer=f.get("Manufacturer", ""), part_number=f.get("Part Number", "").strip()))
        return mods

    # ---------------------------------------------------------------- gpu
    def _gpus(self, snap: HardwareSnapshot) -> None:
        smi = {nvidia.normalize_bus_id(g["pci.bus_id"]): g for g in nvidia.query() if g.get("pci.bus_id")}
        drm = {}
        for card in glob.glob("/sys/class/drm/card[0-9]*"):
            if re.search(r"card\d+$", card):
                drm[self._slot_of(f"{card}/device")] = card
        for slot, d in sorted(self._pci.items()):
            cls = d.get("Class", "")
            if not re.search(r"\[(0300|0302|0380)\]", cls):
                continue
            vid = re.search(r"\[([0-9a-f]{4})\]$", d.get("Vendor", ""))
            vid = vid.group(1) if vid else ""
            vendor = {"10de": "nvidia", "1002": "amd", "8086": "intel"}.get(vid, "other")
            name = strip_pci_id(d.get("Device", "GPU"))
            card = drm.get(slot, "")
            vram = None
            smi_row = smi.get(slot)
            if vendor == "amd" and card:
                vram = read_int(f"{card}/device/mem_info_vram_total")
            if smi_row:
                name = smi_row.get("name") or name
                vram = int(smi_row["memory.total"] * 1024 * 1024) if smi_row.get("memory.total") else None
            if vendor == "intel":
                integrated = slot.endswith("00:02.0")
            elif vendor == "amd":
                integrated = bool(vram) and vram <= 4 * 1024**3 and not re.search(r"\bR[X9]\s?\d|Radeon Pro|Instinct", name)
            else:
                integrated = False
            gpu = GPUInfo(key=slot, name=f"{strip_pci_id(d.get('Vendor', ''))} {name}".replace("Corporation ", "").strip(),
                          vendor=vendor, integrated=integrated, vram=vram, driver=d.get("Driver", ""),
                          sysfs=card, index=int(smi_row["index"]) if smi_row else None)
            snap.gpus.append(gpu)
            props: list = [
                ("Type", "Integrated graphics" if integrated else "Dedicated graphics card"),
                ("Vendor", strip_pci_id(d.get("Vendor", ""))), ("Board vendor", strip_pci_id(d.get("SVendor", ""))),
                ("PCI slot", slot), ("Kernel driver", d.get("Driver")),
                ("Video memory", human_bytes(vram) if vram else ("Shared system RAM" if integrated else None)),
            ]
            if card and vendor == "intel":
                for f, lbl in (("gt_RPn_freq_mhz", "Min hardware clock"), ("gt_RP0_freq_mhz", "Max hardware clock")):
                    if (v := read_int(f"{card}/{f}")):
                        props.append((lbl, f"{v} MHz"))
            if card and vendor == "amd":
                props += [("VBIOS", read(f"{card}/device/vbios_version")),
                          ("PCIe link", f"Gen speed {read(f'{card}/device/current_link_speed')} x{read(f'{card}/device/current_link_width')}")]
            if smi_row:
                props += [("Driver version", smi_row.get("driver_version")), ("VBIOS", smi_row.get("vbios_version")),
                          ("Max graphics clock", f"{smi_row['clocks.max.gr']:.0f} MHz" if smi_row.get("clocks.max.gr") else None),
                          ("Max memory clock", f"{smi_row['clocks.max.mem']:.0f} MHz" if smi_row.get("clocks.max.mem") else None),
                          ("Default power limit", f"{smi_row['power.default_limit']:.0f} W" if smi_row.get("power.default_limit") else None),
                          ("PCIe", f"Gen {smi_row['pcie.link.gen.max']:.0f} x{smi_row['pcie.link.width.max']:.0f}" if smi_row.get("pcie.link.gen.max") else None)]
            snap.add("Graphics", gpu.name, props)

    # ---------------------------------------------------------------- displays
    def _displays(self, snap: HardwareSnapshot) -> None:
        for conn in sorted(glob.glob("/sys/class/drm/card*-*")):
            if read(f"{conn}/status") != "connected":
                continue
            port = os.path.basename(conn).split("-", 1)[1]
            edid = parse_edid(read_bytes(f"{conn}/edid") or b"") or {}
            modes = (read(f"{conn}/modes", "") or "").splitlines()
            builtin = port.startswith(("eDP", "LVDS", "DSI"))
            title = edid.get("name") or edid.get("text") or ("Built-in screen" if builtin else "External monitor")
            size = None
            if edid.get("diagonal_in"):
                size = f'{edid["diagonal_in"]}" ({edid["width_mm"] / 10:.1f} x {edid["height_mm"] / 10:.1f} cm)'
            snap.add("Displays", f"{title} [{port}]", [
                ("Connection", "Internal (built-in panel)" if builtin else port),
                ("Manufacturer code", edid.get("manufacturer")), ("Product code", edid.get("product_code")),
                ("Serial", edid.get("serial")), ("Physical size", size),
                ("Native resolution", f"{edid['native_width']} x {edid['native_height']}" if "native_width" in edid else (modes[0] if modes else None)),
                ("Native refresh", f"{edid['native_refresh']} Hz" if edid.get("native_refresh") else None),
                ("Supported modes", len(set(modes)) or None), ("Manufactured", edid.get("year")),
                ("EDID version", edid.get("edid_version")), ("Signal", "Digital" if edid.get("digital") else ("Analog" if edid else None)),
            ])

    # ---------------------------------------------------------------- storage
    def _storage(self, snap: HardwareSnapshot) -> None:
        try:
            data = json.loads(run(["lsblk", "-J", "-b", "-d", "-o",
                                   "NAME,MODEL,SIZE,ROTA,TRAN,TYPE,SERIAL,REV,VENDOR,RM"]) or "{}")
        except ValueError:
            data = {}
        for dev in data.get("blockdevices", []):
            name = dev.get("name", "")
            if dev.get("type") != "disk" or name.startswith(("loop", "zram", "ram", "dm-", "md", "sr")):
                continue
            tran = (dev.get("tran") or "").lower()
            if name.startswith("nvme") or tran == "nvme":
                kind = "NVMe SSD"
            elif name.startswith("mmcblk"):
                kind = "eMMC / SD"
            elif tran == "usb":
                kind = "USB drive"
            else:
                kind = "HDD" if dev.get("rota") in (True, "1", 1) else "SSD"
            model = " ".join(filter(None, [(dev.get("vendor") or "").strip(), (dev.get("model") or "").strip()])) or name
            size = int(dev.get("size") or 0)
            snap.disks.append(DiskInfo(key=name, model=model, size=size, kind=kind, interface=tran.upper()))
            snap.add("Storage", f"{model} ({human_bytes(size, si=True)})", [
                ("Device", f"/dev/{name}"), ("Type", kind), ("Capacity", f"{human_bytes(size, si=True)} ({size:,} bytes)"),
                ("Interface", tran.upper() or None), ("Firmware", dev.get("rev")), ("Serial", dev.get("serial")),
                ("Removable", "Yes" if dev.get("rm") in (True, "1", 1) else None),
                ("PCIe link", self._nvme_link(name)),
            ])

    @staticmethod
    def _nvme_link(name: str) -> str | None:
        if not name.startswith("nvme"):
            return None
        ctrl = re.match(r"(nvme\d+)", name).group(1)
        base = f"/sys/class/nvme/{ctrl}/device"
        sp, wd = read(f"{base}/current_link_speed"), read(f"{base}/current_link_width")
        return f"{sp} x{wd}" if sp else None

    # ---------------------------------------------------------------- network
    def _network(self, snap: HardwareSnapshot) -> None:
        for iface_path in sorted(glob.glob("/sys/class/net/*")):
            iface = os.path.basename(iface_path)
            if iface == "lo" or "/virtual/" in os.path.realpath(iface_path):
                continue
            dev = f"{iface_path}/device"
            slot = self._slot_of(dev)
            name = self._pci_name(slot) if slot else ""
            if not name:  # USB adapters
                name = self._usb_device_name(dev)
            driver = os.path.basename(os.path.realpath(f"{dev}/driver")) if os.path.exists(f"{dev}/driver") else ""
            is_wifi = os.path.exists(f"{iface_path}/wireless") or os.path.exists(f"{iface_path}/phy80211")
            props: list = [("Interface", iface), ("Type", "Wi-Fi" if is_wifi else "Ethernet"),
                           ("Driver", driver), ("MAC address", read(f"{iface_path}/address")),
                           ("State", read(f"{iface_path}/operstate"))]
            if is_wifi:
                link = parse_iw_link(run(["iw", "dev", iface, "link"]))
                phy = read(f"{iface_path}/phy80211/name")
                max_std = max_wifi_standard_from_iw_phy(run(["iw", "phy", phy, "info"])) if phy else ""
                wifi = WifiInfo(key=iface, name=name or iface, interface=iface, connected=link["connected"],
                                link_speed=link.get("tx", "").split(" ")[0] + " Mbit/s" if link.get("tx") else "",
                                standard=link.get("standard", ""), max_standard=max_std, signal=link.get("signal", ""))
                snap.wifi.append(wifi)
                props += [("Supports", max_std), ("Connected", "Yes" if wifi.connected else "No"),
                          ("Current standard", wifi.standard), ("Link speed (tx)", link.get("tx")),
                          ("Link speed (rx)", link.get("rx")), ("Signal", wifi.signal),
                          ("Frequency", f"{link['freq']:.0f} MHz" if link.get("freq") else None)]
            else:
                speed = read_int(f"{iface_path}/speed")
                props.append(("Link speed", f"{speed} Mbit/s" if speed and speed > 0 else "Not connected"))
            snap.add("Network", name or iface, props)

    def _bluetooth(self, snap: HardwareSnapshot) -> None:
        for hci in sorted(glob.glob("/sys/class/bluetooth/hci[0-9]*")):
            name = self._usb_device_name(f"{hci}/device") or self._pci_name(self._slot_of(f"{hci}/device"))
            snap.add("Bluetooth", name or os.path.basename(hci), [
                ("Adapter", os.path.basename(hci)), ("Address", read(f"{hci}/address")),
                ("Bus", "USB" if "/usb" in os.path.realpath(f"{hci}/device") else "Other"),
            ])

    # ---------------------------------------------------------------- audio / camera / input / usb
    def _audio(self, snap: HardwareSnapshot) -> None:
        for slot, d in sorted(self._pci.items()):
            if "[0403]" in d.get("Class", ""):
                snap.add("Audio", self._pci_name(slot), [("Type", "PCI audio controller"), ("PCI slot", slot),
                                                         ("Driver", d.get("Driver"))])
        for line in (read("/proc/asound/cards", "") or "").splitlines():
            m = re.match(r"\s*(\d+)\s+\[(\S+)\s*\]:\s*(.+)", line)
            if m:
                codecs = sorted({read(p, "") for p in glob.glob(f"/proc/asound/card{m.group(1)}/codec#*")} - {""})
                codec = ", ".join(re.search(r"Codec: (.+)", c).group(1) for c in codecs if "Codec:" in c)
                snap.add("Audio", f"Sound card {m.group(1)}: {m.group(2)}", [("Description", m.group(3).strip()),
                                                                             ("Codec", codec)])

    def _camera(self, snap: HardwareSnapshot) -> None:
        seen = set()
        for v in sorted(glob.glob("/sys/class/video4linux/video*")):
            name = read(f"{v}/name", "")
            dev = os.path.realpath(f"{v}/device")
            if not name or dev in seen:
                continue
            seen.add(dev)
            snap.add("Camera", name, [("Device", f"/dev/{os.path.basename(v)}"),
                                      ("Bus", "USB" if "/usb" in dev else "Integrated / other")])

    def _input(self, snap: HardwareSnapshot) -> None:
        for blk in (read("/proc/bus/input/devices", "") or "").split("\n\n"):
            name = re.search(r'N: Name="(.*)"', blk)
            sysfs = re.search(r"S: Sysfs=(.*)", blk)
            handlers = re.search(r"H: Handlers=(.*)", blk)
            if not name or (sysfs and "/virtual/" in sysfs.group(1)):
                continue
            h = handlers.group(1) if handlers else ""
            kind = ("Keyboard" if "kbd" in h and "mouse" not in h else "Mouse / touchpad" if "mouse" in h
                    else "Joystick / gamepad" if "js" in h else "Buttons / switches")
            if kind == "Buttons / switches" and not re.search(r"(Lid|Power|Sleep|Video|Button|Switch)", name.group(1)):
                kind = "Other input"
            bus = re.search(r"Bus=(\w+)", blk)
            snap.add("Input devices", name.group(1), [
                ("Kind", kind), ("Bus", {"0003": "USB", "0005": "Bluetooth", "0011": "PS/2 (i8042)", "0018": "I2C",
                                         "0019": "Platform"}.get(bus.group(1) if bus else "", bus.group(1) if bus else None)),
            ])

    def _usb(self, snap: HardwareSnapshot) -> None:
        for dev in sorted(glob.glob("/sys/bus/usb/devices/*")):
            if not os.path.exists(f"{dev}/idVendor") or read(f"{dev}/bDeviceClass") == "09":
                continue
            speed = read(f"{dev}/speed")
            snap.add("USB devices", self._usb_device_name(dev) or "USB device", [
                ("ID", f"{read(f'{dev}/idVendor')}:{read(f'{dev}/idProduct')}"),
                ("Port", os.path.basename(dev)), ("USB version", read(f"{dev}/version")),
                ("Speed", f"{speed} Mbit/s" if speed else None),
                ("Max power", read(f"{dev}/bMaxPower")),
            ])

    # ---------------------------------------------------------------- battery
    def _battery(self, snap: HardwareSnapshot) -> None:
        for ps in sorted(glob.glob("/sys/class/power_supply/*")):
            typ = read(f"{ps}/type")
            name = os.path.basename(ps)
            if typ == "Battery" and read(f"{ps}/scope") != "Device":
                design = read_int(f"{ps}/energy_full_design") or read_int(f"{ps}/charge_full_design")
                full = read_int(f"{ps}/energy_full") or read_int(f"{ps}/charge_full")
                unit = "Wh" if os.path.exists(f"{ps}/energy_full") else "Ah"
                health = f"{full / design * 100:.0f}%" if design and full else None
                snap.add("Battery & power", f"Battery {name}", [
                    ("Manufacturer", read(f"{ps}/manufacturer")), ("Model", read(f"{ps}/model_name")),
                    ("Chemistry", read(f"{ps}/technology")), ("Status", read(f"{ps}/status")),
                    ("Charge", f"{read(f'{ps}/capacity')}%" if read(f"{ps}/capacity") else None),
                    ("Design capacity", f"{design / 1e6:.1f} {unit}" if design else None),
                    ("Full-charge capacity", f"{full / 1e6:.1f} {unit}" if full else None),
                    ("Health", health), ("Cycle count", read(f"{ps}/cycle_count")),
                    ("Voltage", f"{read_int(f'{ps}/voltage_now') / 1e6:.2f} V" if read_int(f"{ps}/voltage_now") else None),
                ])
            elif typ == "Mains":
                snap.add("Battery & power", f"AC adapter {name}", [("Plugged in", "Yes" if read(f"{ps}/online") == "1" else "No")])

    def _pci_devices(self, snap: HardwareSnapshot) -> None:
        for slot, d in sorted(self._pci.items()):
            snap.add("PCI devices", f"{slot}  {self._pci_name(slot)}", [
                ("Class", strip_pci_id(d.get("Class", ""))), ("Vendor", d.get("Vendor")), ("Device", d.get("Device")),
                ("Subsystem", f"{strip_pci_id(d.get('SVendor', ''))} {strip_pci_id(d.get('SDevice', ''))}".strip()),
                ("Revision", d.get("Rev")), ("Driver", d.get("Driver")),
            ])

    # ================================================================ sensors
    def read_sensors(self) -> list[SensorReading]:
        out: list[SensorReading] = []
        snap = self.snapshot
        hwmon_keys = self._hwmon_semantic_keys(snap)
        try:
            temps = psutil.sensors_temperatures()
        except Exception:
            temps = {}
        # Exact per-device readings first (disks, AMD GPUs, Wi-Fi cards) ...
        covered = set()
        for path, key, group, label, hw_name in hwmon_keys:
            v = read_int(path)
            if v is not None:
                out.append(SensorReading(group, label, v / 1000, "°C", key))
                if group == "Drive":  # amdgpu keeps its extra junction/memory sensors below
                    covered.add(hw_name)
        # ... then every other hwmon chip psutil knows about.
        cpu_key_set = False
        wifi_key = f"wifi:{snap.wifi[0].key}" if snap and len(snap.wifi) == 1 else None
        for group, entries in temps.items():
            if group in covered:
                continue
            pretty = _HWMON_GROUP_NAMES.get(group, "Wi-Fi card" if group.startswith("iwlwifi") else group)
            for i, e in enumerate(entries):
                if e.current is None or e.current <= 0 or e.current > 150:
                    continue
                label = e.label or f"Sensor {i + 1}"
                key = None
                if group in ("coretemp", "k10temp", "zenpower") and not cpu_key_set and (
                        label.startswith(("Package", "Tctl", "Tdie")) or group != "coretemp"):
                    key, cpu_key_set = "cpu", True
                elif group in ("spd5118", "jc42"):
                    key = "ram"
                elif group.startswith("iwlwifi") and wifi_key and i == 0:
                    key = wifi_key
                out.append(SensorReading(pretty, label, e.current, "°C", key))
        if not cpu_key_set:
            for r in out:
                if r.group in ("ACPI thermal zone", "CPU package"):
                    r.key = "cpu"
                    break
        try:
            for group, entries in (psutil.sensors_fans() or {}).items():
                for i, e in enumerate(entries):
                    out.append(SensorReading("Fans", e.label or f"{group} fan {i + 1}", e.current, "RPM"))
        except Exception:
            pass
        freq = psutil.cpu_freq()
        if freq:
            out.append(SensorReading("CPU", "Average clock", freq.current, "MHz", "cpu_clock"))
        out.append(SensorReading("CPU", "Load", psutil.cpu_percent(), "%", "cpu_load"))
        out.append(SensorReading("Memory", "Used", psutil.virtual_memory().percent, "%", "ram_used"))
        out += self._rapl_power()
        for g in (snap.gpus if snap else []):
            if g.vendor == "intel" and g.sysfs and (v := read_int(f"{g.sysfs}/gt_act_freq_mhz")) is not None:
                out.append(SensorReading("GPU (Intel)", "Graphics clock", v, "MHz", f"gpu_clock:{g.key}"))
            if g.vendor == "amd" and g.sysfs:
                for hw in glob.glob(f"{g.sysfs}/device/hwmon/hwmon*"):
                    if (v := read_int(f"{hw}/freq1_input")):
                        out.append(SensorReading("GPU (AMD)", "Graphics clock", v / 1e6, "MHz", f"gpu_clock:{g.key}"))
                    if (v := read_int(f"{hw}/power1_average") or read_int(f"{hw}/power1_input")):
                        out.append(SensorReading("GPU (AMD)", "Power", v / 1e6, "W", f"gpu_power:{g.key}"))
        for row in nvidia.query():
            key = nvidia.normalize_bus_id(row["pci.bus_id"]) if row.get("pci.bus_id") else None
            grp = f"GPU (NVIDIA) {row.get('name', '')}"
            if row.get("temperature.gpu") is not None:
                out.append(SensorReading(grp, "Core", row["temperature.gpu"], "°C", f"gpu:{key}"))
            if row.get("clocks.gr") is not None:
                out.append(SensorReading(grp, "Graphics clock", row["clocks.gr"], "MHz", f"gpu_clock:{key}"))
            if row.get("clocks.mem") is not None:
                out.append(SensorReading(grp, "Memory clock", row["clocks.mem"], "MHz"))
            if row.get("power.draw") is not None:
                out.append(SensorReading(grp, "Power", row["power.draw"], "W", f"gpu_power:{key}"))
            if row.get("fan.speed") is not None:
                out.append(SensorReading(grp, "Fan", row["fan.speed"], "%"))
        return out

    def _hwmon_semantic_keys(self, snap: HardwareSnapshot | None) -> list[tuple[str, str, str, str, str]]:
        """(temp file, semantic key, group, label, hwmon chip name) for devices we can pin exactly."""
        res = []
        if not snap:
            return res

        def first(pattern: str) -> tuple[str, str] | None:
            for hw in sorted(glob.glob(pattern)):
                return hw, read(os.path.join(os.path.dirname(hw), "name"), "") or ""
            return None

        for d in snap.disks:
            if d.key.startswith("nvme"):
                base = "/sys/class/nvme/" + re.match(r"(nvme\d+)", d.key).group(1)
                hit = first(f"{base}/hwmon*/temp1_input") or first(f"{base}/device/hwmon/hwmon*/temp1_input")
            else:
                hit = first(f"/sys/block/{d.key}/device/hwmon/hwmon*/temp1_input")
            if hit:
                res.append((hit[0], f"disk:{d.key}", "Drive", d.model, hit[1]))
        for g in snap.gpus:
            if g.vendor == "amd" and g.sysfs and (hit := first(f"{g.sysfs}/device/hwmon/hwmon*/temp1_input")):
                res.append((hit[0], f"gpu:{g.key}", "GPU", g.name, hit[1]))
        return res

    def _rapl_power(self) -> list[SensorReading]:
        out = []
        for zone in glob.glob("/sys/class/powercap/intel-rapl:[0-9]"):
            e = read_int(f"{zone}/energy_uj")  # root-only on most kernels; silently skipped otherwise
            if e is None:
                continue
            now = time.monotonic()
            prev = self._rapl_prev.get(zone)
            self._rapl_prev[zone] = (now, e)
            if prev and now > prev[0] and e >= prev[1]:
                watts = (e - prev[1]) / 1e6 / (now - prev[0])
                out.append(SensorReading("CPU", f"{read(f'{zone}/name', 'package')} power", watts, "W", "cpu_power"))
        return out

