"""Windows 11 hardware detection via CIM/WMI (PowerShell) + netsh + nvidia-smi."""

from __future__ import annotations

import json
import re

import psutil

from haffop import nvidia
from haffop.hardware.model import (CPUInfo, DiskInfo, GPUInfo, HardwareSnapshot, MemoryModule,
                                   SensorReading, WifiInfo)
from haffop.hardware.parsers import (CHASSIS_LAPTOP, CHASSIS_NAMES, DDR_TYPES,
                                     max_wifi_standard_from_radio_types, parse_key_value_blocks)
from haffop.hardware.sensors import select_cpu_temperature, valid_temperature
from haffop.util import human_bytes, run

POWERSHELL = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"]

_DETECT_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
function Q($cls, $ns = 'root\cimv2') { @(Get-CimInstance -Namespace $ns -ClassName $cls) }
function Dec($a) { if ($a) { -join ($a | Where-Object { $_ -ne 0 } | ForEach-Object { [char]$_ }) } }
$r = [ordered]@{}
$r.cpu = Q Win32_Processor | Select-Object Name,Manufacturer,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed,CurrentClockSpeed,L2CacheSize,L3CacheSize,SocketDesignation,Description,VirtualizationFirmwareEnabled
$r.mem = Q Win32_PhysicalMemory | Select-Object Capacity,Speed,ConfiguredClockSpeed,Manufacturer,PartNumber,SerialNumber,DeviceLocator,BankLabel,SMBIOSMemoryType,FormFactor,ConfiguredVoltage
$r.memarray = Q Win32_PhysicalMemoryArray | Select-Object MaxCapacityEx,MemoryDevices
$r.gpu = Q Win32_VideoController | Select-Object Name,AdapterCompatibility,AdapterRAM,DriverVersion,DriverDate,VideoProcessor,CurrentHorizontalResolution,CurrentVerticalResolution,CurrentRefreshRate,PNPDeviceID
$r.gpumem = @(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0*' | Select-Object DriverDesc,'HardwareInformation.qwMemorySize',MatchingDeviceId)
$r.disk = @(Get-PhysicalDisk | Select-Object DeviceId,FriendlyName,MediaType,BusType,Size,SerialNumber,FirmwareVersion,HealthStatus,SpindleSpeed)
$r.net = @(Get-NetAdapter -Physical | Select-Object Name,InterfaceDescription,MacAddress,LinkSpeed,Status,PhysicalMediaType,DriverVersionString,DriverProvider)
$r.wlan = (netsh wlan show interfaces) -join "`n"
$r.wlandrv = (netsh wlan show drivers) -join "`n"
$r.system = Q Win32_ComputerSystem | Select-Object Manufacturer,Model,SystemFamily,PCSystemType,TotalPhysicalMemory
$r.enclosure = Q Win32_SystemEnclosure | Select-Object ChassisTypes
$r.board = Q Win32_BaseBoard | Select-Object Manufacturer,Product,Version,SerialNumber
$r.bios = Q Win32_BIOS | Select-Object Manufacturer,SMBIOSBIOSVersion,ReleaseDate,SMBIOSMajorVersion,SMBIOSMinorVersion
$r.os = Q Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber,OSArchitecture
$r.battery = Q Win32_Battery | Select-Object Name,EstimatedChargeRemaining,BatteryStatus,Chemistry,DeviceID
$r.batstatic = Q BatteryStaticData 'root\wmi' | Select-Object DesignedCapacity,ManufactureName,DeviceName,SerialNumber
$r.batfull = Q BatteryFullChargedCapacity 'root\wmi' | Select-Object FullChargedCapacity
$r.batcycle = Q BatteryCycleCount 'root\wmi' | Select-Object CycleCount
$ids = Q WmiMonitorID 'root\wmi'
$par = Q WmiMonitorBasicDisplayParams 'root\wmi'
$con = Q WmiMonitorConnectionParams 'root\wmi'
$r.monitors = @($ids | ForEach-Object {
  $i = $_.InstanceName
  [ordered]@{ Name = Dec $_.UserFriendlyName; Maker = Dec $_.ManufacturerName; Code = Dec $_.ProductCodeID;
    Serial = Dec $_.SerialNumberID; Year = $_.YearOfManufacture;
    W = ($par | Where-Object InstanceName -eq $i).MaxHorizontalImageSize;
    H = ($par | Where-Object InstanceName -eq $i).MaxVerticalImageSize;
    Tech = ($con | Where-Object InstanceName -eq $i).VideoOutputTechnology }
})
$r.screens = @(Add-Type -AssemblyName System.Windows.Forms -PassThru | Out-Null; [System.Windows.Forms.Screen]::AllScreens | ForEach-Object { [ordered]@{ Name = $_.DeviceName; W = $_.Bounds.Width; H = $_.Bounds.Height; Primary = $_.Primary } })
$r.audio = Q Win32_SoundDevice | Select-Object Name,Manufacturer,Status
$r.pnp = @(Get-PnpDevice -PresentOnly | Where-Object { $_.Class -in 'Keyboard','Mouse','Camera','Image','Bluetooth','Biometric','USB','HIDClass','Printer','SmartCardReader','MEDIA' } | Select-Object FriendlyName,Class,Manufacturer,InstanceId,Status)
$r | ConvertTo-Json -Depth 4 -Compress
"""

_SENSORS_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$r = [ordered]@{}
$r.zones = @(Get-CimInstance -Namespace root\wmi -ClassName MSAcpi_ThermalZoneTemperature | Select-Object InstanceName,CurrentTemperature)
foreach ($ns in 'root\LibreHardwareMonitor','root\OpenHardwareMonitor') {
  $s = @(Get-CimInstance -Namespace $ns -ClassName Sensor | Where-Object { $_.SensorType -in 'Temperature','Clock','Fan','Power' } | Select-Object Name,SensorType,Value,Parent,Identifier)
  if ($s.Count) { $r.lhm = $s; $r.lhmhw = @(Get-CimInstance -Namespace $ns -ClassName Hardware | Select-Object Identifier,Name,HardwareType); break }
}
$r.disks = @(Get-PhysicalDisk | Get-StorageReliabilityCounter | Select-Object DeviceId,Temperature)
$r.perf = (Get-CimInstance Win32_PerfFormattedData_Counters_ProcessorInformation -Filter "Name='_Total'").PercentProcessorPerformance
$r.maxclock = (Get-CimInstance Win32_Processor | Select-Object -First 1).MaxClockSpeed
$r | ConvertTo-Json -Depth 4 -Compress
"""

_MEDIA = {3: "HDD", 4: "SSD", 0: "Unspecified"}
_BUS = {7: "USB", 8: "RAID", 11: "SATA", 17: "NVMe", 3: "ATA", 10: "SAS", 12: "SD", 13: "MMC"}
_FORM = {8: "DIMM", 12: "SODIMM", 0: ""}
_VIDEO_OUT = {-2: "Uninitialized", 0: "VGA", 4: "DVI", 5: "HDMI", 10: "DisplayPort", 11: "DisplayPort (embedded)",
              -2147483648: "Internal (built-in panel)", 15: "Miracast", 16: "Indirect wired"}


def ps_json(script: str, timeout: float = 60) -> dict:
    out = run(POWERSHELL + [script], timeout=timeout)
    try:
        return json.loads(out) if out.strip() else {}
    except ValueError:
        return {}


def _lst(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


class WindowsHardware:
    def __init__(self) -> None:
        self.snapshot: HardwareSnapshot | None = None
        self._disk_names: dict[str, str] = {}

    def detect(self) -> HardwareSnapshot:
        snap = HardwareSnapshot()
        d = ps_json(_DETECT_PS, timeout=90)
        if not d:
            snap.warnings.append("PowerShell/CIM query failed - only basic information is available.")
        for step in (self._system, self._cpu, self._memory, self._gpus, self._displays, self._storage,
                     self._network, self._devices, self._battery):
            try:
                step(snap, d)
            except Exception as exc:
                snap.warnings.append(f"{step.__name__.strip('_')}: {exc}")
        snap.warnings.append("Accurate CPU/board temperatures usually require LibreHardwareMonitor or "
                             "OpenHardwareMonitor with its WMI provider running (often elevated). "
                             "Administrator rights alone do not expose CPU die sensors. ACPI zones are not CPU readings.")
        self.snapshot = snap
        return snap

    # ---------------------------------------------------------------- sections
    def _system(self, snap: HardwareSnapshot, d: dict) -> None:
        cs = (_lst(d.get("system")) or [{}])[0]
        chassis = [int(c) for c in _lst((_lst(d.get("enclosure")) or [{}])[0].get("ChassisTypes"))]
        snap.is_laptop = cs.get("PCSystemType") == 2 or any(c in CHASSIS_LAPTOP for c in chassis) or bool(_lst(d.get("battery")))
        maker, model = (cs.get("Manufacturer") or "").strip(), (cs.get("Model") or "").strip()
        snap.system_name = model if model.lower().startswith(maker.lower()) else f"{maker} {model}".strip()
        osd = (_lst(d.get("os")) or [{}])[0]
        snap.add("System", snap.system_name or "Computer", [
            ("Manufacturer", cs.get("Manufacturer")), ("Model", cs.get("Model")), ("Family", cs.get("SystemFamily")),
            ("Chassis", ", ".join(CHASSIS_NAMES.get(c, f"Type {c}") for c in chassis)),
            ("Form factor", "Laptop / portable" if snap.is_laptop else "Desktop"),
            ("Operating system", f"{osd.get('Caption', '')} {osd.get('OSArchitecture', '')} (build {osd.get('BuildNumber', '?')})"),
        ])
        for b in _lst(d.get("board")):
            snap.add("Motherboard & BIOS", "Motherboard", [("Manufacturer", b.get("Manufacturer")), ("Model", b.get("Product")),
                                                           ("Revision", b.get("Version")), ("Serial", b.get("SerialNumber"))])
        for b in _lst(d.get("bios")):
            snap.add("Motherboard & BIOS", "BIOS / UEFI", [
                ("Vendor", b.get("Manufacturer")), ("Version", b.get("SMBIOSBIOSVersion")),
                ("Release date", str(b.get("ReleaseDate") or "")[:10]),
                ("SMBIOS", f"{b.get('SMBIOSMajorVersion')}.{b.get('SMBIOSMinorVersion')}")])

    def _cpu(self, snap: HardwareSnapshot, d: dict) -> None:
        cpus = _lst(d.get("cpu"))
        c = cpus[0] if cpus else {}
        name = re.sub(r"\s+", " ", c.get("Name") or "Unknown CPU").strip()
        freq = psutil.cpu_freq()
        snap.cpu = CPUInfo(name=name, vendor=c.get("Manufacturer", ""),
                           cores=sum(int(x.get("NumberOfCores") or 0) for x in cpus) or psutil.cpu_count(False) or 0,
                           threads=sum(int(x.get("NumberOfLogicalProcessors") or 0) for x in cpus) or psutil.cpu_count() or 0,
                           base_mhz=float(c.get("MaxClockSpeed") or 0) or None,
                           max_mhz=(freq.max if freq and freq.max else None))
        snap.add("Processor", name, [
            ("Vendor", c.get("Manufacturer")), ("Sockets", len(cpus)), ("Socket", c.get("SocketDesignation")),
            ("Cores", snap.cpu.cores), ("Threads", snap.cpu.threads),
            ("Base clock", f"{c.get('MaxClockSpeed')} MHz" if c.get("MaxClockSpeed") else None),
            ("L2 cache", human_bytes(int(c["L2CacheSize"]) * 1024) if c.get("L2CacheSize") else None),
            ("L3 cache", human_bytes(int(c["L3CacheSize"]) * 1024) if c.get("L3CacheSize") else None),
            ("Identifier", c.get("Description")),
            ("Virtualization enabled", {True: "Yes", False: "No"}.get(c.get("VirtualizationFirmwareEnabled"))),
        ])

    def _memory(self, snap: HardwareSnapshot, d: dict) -> None:
        snap.ram_total = psutil.virtual_memory().total
        arr = (_lst(d.get("memarray")) or [{}])[0]
        snap.ram_slots_total = int(arr.get("MemoryDevices") or 0) or None
        if arr.get("MaxCapacityEx"):
            snap.add("Memory", "Memory controller", [("Maximum supported", human_bytes(int(arr["MaxCapacityEx"]) * 1024)),
                                                     ("Slots", snap.ram_slots_total)])
        for m in _lst(d.get("mem")):
            mod = MemoryModule(
                slot=m.get("DeviceLocator") or m.get("BankLabel") or "", size=int(m.get("Capacity") or 0),
                rated_mts=int(m["Speed"]) if m.get("Speed") else None,
                configured_mts=int(m["ConfiguredClockSpeed"]) if m.get("ConfiguredClockSpeed") else None,
                mem_type=DDR_TYPES.get(int(m.get("SMBIOSMemoryType") or 0), ""),
                form_factor=_FORM.get(int(m.get("FormFactor") or 0), ""),
                manufacturer=(m.get("Manufacturer") or "").strip(), part_number=(m.get("PartNumber") or "").strip(),
                extra={"Bank": m.get("BankLabel"), "Serial": m.get("SerialNumber"),
                       "Configured voltage": f"{int(m['ConfiguredVoltage']) / 1000:.2f} V" if m.get("ConfiguredVoltage") else None},
            )
            snap.ram_modules.append(mod)
            snap.add("Memory", f"{mod.slot}: {human_bytes(mod.size)} {mod.mem_type}", [
                ("Size", human_bytes(mod.size)), ("Type", mod.mem_type), ("Form factor", mod.form_factor),
                ("Rated speed", f"{mod.rated_mts} MT/s" if mod.rated_mts else None),
                ("Running speed", f"{mod.configured_mts} MT/s" if mod.configured_mts else None),
                ("Manufacturer", mod.manufacturer), ("Part number", mod.part_number), *mod.extra.items()])

    def _gpus(self, snap: HardwareSnapshot, d: dict) -> None:
        regmem = {}
        for g in _lst(d.get("gpumem")):
            size = g.get("HardwareInformation.qwMemorySize")
            if g.get("DriverDesc") and size:
                regmem[g["DriverDesc"]] = int(size)
        smi = nvidia.query()
        smi_by_name = {g.get("name"): g for g in smi}
        for i, g in enumerate(_lst(d.get("gpu"))):
            name = (g.get("Name") or "GPU").strip()
            if "Microsoft Basic" in name or "Remote Display" in name:
                continue
            low = name.lower()
            vendor = "nvidia" if "nvidia" in low else "amd" if ("amd" in low or "radeon" in low) else "intel" if "intel" in low else "other"
            vram = regmem.get(name) or (int(g["AdapterRAM"]) if g.get("AdapterRAM") else None)
            if vendor == "intel":
                integrated = not re.search(r"Arc\(TM\) A\d{3}|Arc A\d{3}|Arc B\d{3}", name)
            elif vendor == "amd":
                integrated = not re.search(r"\bRX\s?\d|\bR[579] \d|Radeon Pro|FirePro|Instinct", name)
            else:
                integrated = False
            row = smi_by_name.get(name)
            gpu = GPUInfo(key=g.get("PNPDeviceID") or f"gpu{i}", name=name, vendor=vendor, integrated=integrated,
                          vram=vram, driver=g.get("DriverVersion", ""), index=int(row["index"]) if row else None)
            snap.gpus.append(gpu)
            snap.add("Graphics", name, [
                ("Type", "Integrated graphics" if integrated else "Dedicated graphics card"),
                ("Vendor", g.get("AdapterCompatibility")), ("Video memory", human_bytes(vram) if vram else None),
                ("Driver version", g.get("DriverVersion")), ("Driver date", str(g.get("DriverDate") or "")[:10]),
                ("Current mode", f"{g.get('CurrentHorizontalResolution')} x {g.get('CurrentVerticalResolution')} @ {g.get('CurrentRefreshRate')} Hz"
                 if g.get("CurrentHorizontalResolution") else None),
                ("Device ID", g.get("PNPDeviceID")),
                ("Max graphics clock", f"{row['clocks.max.gr']:.0f} MHz" if row and row.get("clocks.max.gr") else None),
                ("Default power limit", f"{row['power.default_limit']:.0f} W" if row and row.get("power.default_limit") else None),
            ])

    def _displays(self, snap: HardwareSnapshot, d: dict) -> None:
        for m in _lst(d.get("monitors")):
            w, h = m.get("W") or 0, m.get("H") or 0
            diag = round(((w ** 2 + h ** 2) ** 0.5) / 2.54, 1) if w and h else None
            tech = _VIDEO_OUT.get(m.get("Tech"), str(m.get("Tech")) if m.get("Tech") is not None else None)
            snap.add("Displays", m.get("Name") or ("Built-in screen" if tech and "Internal" in tech else "Monitor"), [
                ("Connection", tech), ("Manufacturer code", m.get("Maker")), ("Product code", m.get("Code")),
                ("Serial", m.get("Serial")), ("Physical size", f'{diag}" ({w} x {h} cm)' if diag else None),
                ("Manufactured", m.get("Year"))])
        for s in _lst(d.get("screens")):
            snap.add("Displays", f"Desktop {s.get('Name', '')}", [("Resolution", f"{s.get('W')} x {s.get('H')}"),
                                                                  ("Primary", "Yes" if s.get("Primary") else "No")])

    def _storage(self, snap: HardwareSnapshot, d: dict) -> None:
        for disk in _lst(d.get("disk")):
            bus = _BUS.get(disk.get("BusType"), str(disk.get("BusType") or ""))
            media = _MEDIA.get(disk.get("MediaType"), "")
            kind = "NVMe SSD" if bus == "NVMe" else "USB drive" if bus == "USB" else media or "Disk"
            size = int(disk.get("Size") or 0)
            key = str(disk.get("DeviceId"))
            model = (disk.get("FriendlyName") or "Disk").strip()
            self._disk_names[key] = model
            snap.disks.append(DiskInfo(key=key, model=model, size=size, kind=kind, interface=bus))
            snap.add("Storage", f"{model} ({human_bytes(size, si=True)})", [
                ("Disk number", key), ("Type", kind), ("Interface", bus),
                ("Capacity", f"{human_bytes(size, si=True)} ({size:,} bytes)"), ("Firmware", disk.get("FirmwareVersion")),
                ("Serial", (disk.get("SerialNumber") or "").strip()), ("Health", disk.get("HealthStatus")),
                ("Spindle speed", f"{disk['SpindleSpeed']} RPM" if disk.get("SpindleSpeed") not in (None, 0, 4294967295) else None)])

    def _network(self, snap: HardwareSnapshot, d: dict) -> None:
        wlan_blocks = parse_key_value_blocks(d.get("wlan") or "")
        radio = ""
        for line in (d.get("wlandrv") or "").splitlines():
            if "Radio types supported" in line:
                radio = line.split(":", 1)[1].strip()
        for n in _lst(d.get("net")):
            desc = n.get("InterfaceDescription") or n.get("Name")
            media = str(n.get("PhysicalMediaType") or "")
            is_wifi = "802.11" in media or "Wireless" in media or "Wi-Fi" in (n.get("Name") or "") or bool(
                re.search(r"wi-?fi|wireless|802\.11", desc or "", re.I))
            props = [("Connection name", n.get("Name")), ("Type", "Wi-Fi" if is_wifi else media or "Ethernet"),
                     ("MAC address", n.get("MacAddress")), ("Status", n.get("Status")),
                     ("Link speed", n.get("LinkSpeed")), ("Driver", f"{n.get('DriverProvider', '')} {n.get('DriverVersionString', '')}".strip())]
            if is_wifi:
                blk = next((b for b in wlan_blocks if b.get("Description") == desc or b.get("Name") == n.get("Name")), {})
                rx, tx = blk.get("Receive rate (Mbps)"), blk.get("Transmit rate (Mbps)")
                std = blk.get("Radio type", "")
                std_name = {"802.11be": "Wi-Fi 7 (802.11be)", "802.11ax": "Wi-Fi 6 (802.11ax)", "802.11ac": "Wi-Fi 5 (802.11ac)",
                            "802.11n": "Wi-Fi 4 (802.11n)"}.get(std, std)
                wifi = WifiInfo(key=n.get("Name") or desc, name=desc, interface=n.get("Name", ""),
                                connected=blk.get("State", "").lower() == "connected",
                                link_speed=f"{tx} Mbit/s" if tx else (n.get("LinkSpeed") or ""), standard=std_name,
                                max_standard=max_wifi_standard_from_radio_types(radio) if radio else "",
                                signal=blk.get("Signal", ""))
                snap.wifi.append(wifi)
                props += [("Supports", wifi.max_standard), ("Current standard", std_name), ("Band", blk.get("Band")),
                          ("Receive rate", f"{rx} Mbit/s" if rx else None), ("Transmit rate", f"{tx} Mbit/s" if tx else None),
                          ("Signal", wifi.signal), ("Channel", blk.get("Channel"))]
            snap.add("Network", desc, props)

    def _devices(self, snap: HardwareSnapshot, d: dict) -> None:
        for a in _lst(d.get("audio")):
            snap.add("Audio", a.get("Name"), [("Manufacturer", a.get("Manufacturer")), ("Status", a.get("Status"))])
        cat = {"Keyboard": "Input devices", "Mouse": "Input devices", "HIDClass": "Input devices", "Camera": "Camera",
               "Image": "Camera", "Bluetooth": "Bluetooth", "Biometric": "Input devices", "USB": "USB devices",
               "SmartCardReader": "USB devices", "Printer": "USB devices", "MEDIA": "Audio"}
        seen = set()
        for p in _lst(d.get("pnp")):
            name = p.get("FriendlyName")
            if not name or (p.get("Class"), name) in seen:
                continue
            if p.get("Class") in ("USB", "HIDClass") and re.search(r"Root Hub|Host Controller|Composite|HID-compliant (vendor|system|consumer)|USB Input Device", name):
                continue
            seen.add((p.get("Class"), name))
            snap.add(cat.get(p.get("Class"), "USB devices"), name, [("Class", p.get("Class")), ("Manufacturer", p.get("Manufacturer")),
                                                                     ("Status", p.get("Status")), ("Instance", p.get("InstanceId"))])

    def _battery(self, snap: HardwareSnapshot, d: dict) -> None:
        stat = _lst(d.get("batstatic"))
        full = _lst(d.get("batfull"))
        cyc = _lst(d.get("batcycle"))
        for i, b in enumerate(_lst(d.get("battery"))):
            design = stat[i].get("DesignedCapacity") if i < len(stat) else None
            fullc = full[i].get("FullChargedCapacity") if i < len(full) else None
            snap.add("Battery & power", b.get("Name") or f"Battery {i + 1}", [
                ("Manufacturer", stat[i].get("ManufactureName") if i < len(stat) else None),
                ("Charge", f"{b.get('EstimatedChargeRemaining')}%" if b.get("EstimatedChargeRemaining") is not None else None),
                ("Status", {1: "Discharging", 2: "On AC power", 3: "Fully charged", 6: "Charging"}.get(b.get("BatteryStatus"))),
                ("Design capacity", f"{design / 1000:.1f} Wh" if design else None),
                ("Full-charge capacity", f"{fullc / 1000:.1f} Wh" if fullc else None),
                ("Health", f"{fullc / design * 100:.0f}%" if design and fullc else None),
                ("Cycle count", cyc[i].get("CycleCount") if i < len(cyc) else None)])

    # ---------------------------------------------------------------- sensors
    def read_sensors(self) -> list[SensorReading]:
        out: list[SensorReading] = []
        d = ps_json(_SENSORS_PS, timeout=20)
        lhm = _lst(d.get("lhm"))
        hw_types = {h.get("Identifier"): (h.get("HardwareType") or "", h.get("Name") or "") for h in _lst(d.get("lhmhw"))}
        if not d:
            raise RuntimeError("Windows sensor query failed. Check PowerShell/CIM access.")
        cpu_candidates = []
        for s in lhm:
            parent = s.get("Parent") or ""
            matches = [ident for ident in hw_types if ident and (parent == ident or parent.startswith(ident + "/"))]
            typ, hw_name = hw_types[max(matches, key=len)] if matches else ("", "")
            unit = {"Temperature": "°C", "Clock": "MHz", "Fan": "RPM", "Power": "W"}.get(s.get("SensorType"))
            value = s.get("Value")
            if unit is None or not isinstance(value, (int, float)):
                continue
            if unit == "°C" and not valid_temperature(value):
                continue
            key = None
            if s["SensorType"] == "Temperature" and typ.lower().startswith("gpu") and s.get("Name") == "GPU Core":
                matches = [g for g in (self.snapshot.gpus if self.snapshot else [])
                           if hw_name and (g.name in hw_name or hw_name in g.name)]
                key = f"gpu:{matches[0].key}" if len(matches) == 1 else None
            elif s["SensorType"] == "Power" and typ.lower() == "cpu" and s.get("Name") == "CPU Package":
                key = "cpu_power"
            reading = SensorReading(hw_name or typ, s.get("Name", ""), float(value), unit, key)
            out.append(reading)
            if unit == "°C" and typ.lower() == "cpu":
                cpu_candidates.append(reading)
        select_cpu_temperature(cpu_candidates)
        for z in _lst(d.get("zones")):
            if z.get("CurrentTemperature"):
                c = z["CurrentTemperature"] / 10 - 273.15
                if 0 < c < 150:
                    out.append(SensorReading("ACPI thermal zone", str(z.get("InstanceName", "")).split("\\")[-1], c, "°C"))
        for disk in _lst(d.get("disks")):
            if disk.get("Temperature"):
                key = str(disk.get("DeviceId"))
                out.append(SensorReading("Drive", self._disk_names.get(key, f"Disk {key}"), float(disk["Temperature"]), "°C", f"disk:{key}"))
        if d.get("perf") is not None and d.get("maxclock"):
            out.append(SensorReading("CPU", "Average clock", float(d["perf"]) / 100 * float(d["maxclock"]), "MHz", "cpu_clock"))
        out.append(SensorReading("CPU", "Load", psutil.cpu_percent(), "%", "cpu_load"))
        out.append(SensorReading("Memory", "Used", psutil.virtual_memory().percent, "%", "ram_used"))
        gpus = self.snapshot.gpus if self.snapshot else []
        for row in nvidia.query():
            g = next((g for g in gpus if g.index == int(row["index"])), None)
            key = g.key if g else None
            grp = f"GPU (NVIDIA) {row.get('name', '')}"
            for fld, lbl, unit, k in (("temperature.gpu", "Core", "°C", f"gpu:{key}"), ("clocks.gr", "Graphics clock", "MHz", f"gpu_clock:{key}"),
                                      ("clocks.mem", "Memory clock", "MHz", None), ("power.draw", "Power", "W", f"gpu_power:{key}"),
                                      ("fan.speed", "Fan", "%", None)):
                if row.get(fld) is not None:
                    out.append(SensorReading(grp, lbl, row[fld], unit, k if key else None))
        return [r for r in out if r.unit != "°C" or valid_temperature(r.value)]
