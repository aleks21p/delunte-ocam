"""Platform-neutral data structures for detected hardware and live sensors."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CPUInfo:
    name: str = "Unknown CPU"
    vendor: str = ""
    cores: int = 0
    threads: int = 0
    base_mhz: float | None = None
    max_mhz: float | None = None
    min_mhz: float | None = None


@dataclass
class MemoryModule:
    slot: str = ""
    size: int = 0
    rated_mts: int | None = None
    configured_mts: int | None = None
    mem_type: str = ""
    form_factor: str = ""
    manufacturer: str = ""
    part_number: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class GPUInfo:
    key: str  # stable id (pci slot / pnp id); sensors use "gpu:<key>"
    name: str
    vendor: str  # nvidia | amd | intel | other
    integrated: bool
    vram: int | None = None
    driver: str = ""
    sysfs: str = ""  # linux drm card path
    index: int | None = None  # nvidia-smi index


@dataclass
class DiskInfo:
    key: str
    model: str
    size: int
    kind: str  # NVMe SSD / SSD / HDD / USB / eMMC
    interface: str = ""


@dataclass
class WifiInfo:
    key: str
    name: str
    interface: str = ""
    connected: bool = False
    link_speed: str = ""
    standard: str = ""
    max_standard: str = ""
    signal: str = ""


@dataclass
class HardwareSnapshot:
    cpu: CPUInfo = field(default_factory=CPUInfo)
    ram_total: int = 0
    ram_modules: list[MemoryModule] = field(default_factory=list)
    ram_slots_total: int | None = None
    gpus: list[GPUInfo] = field(default_factory=list)
    disks: list[DiskInfo] = field(default_factory=list)
    wifi: list[WifiInfo] = field(default_factory=list)
    is_laptop: bool = False
    system_name: str = ""
    # Expanded view: category -> list of (device title, [(property, value), ...])
    expanded: dict[str, list[tuple[str, list[tuple[str, str]]]]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def add(self, category: str, title: str, props: list[tuple[str, object]]) -> None:
        clean = [(k, str(v)) for k, v in props if v not in (None, "", [], {})]
        if clean:
            self.expanded.setdefault(category, []).append((title, clean))


@dataclass
class SensorReading:
    group: str
    label: str
    value: float
    unit: str  # °C, RPM, MHz, W, %
    key: str | None = None  # semantic id for the simple view (cpu, gpu:<k>, disk:<k>, wifi:<k>, ram)


CATEGORY_ORDER = [
    "System", "Motherboard & BIOS", "Processor", "Memory", "Graphics", "Displays",
    "Storage", "Network", "Bluetooth", "Audio", "Camera", "Input devices",
    "USB devices", "Battery & power", "PCI devices", "Sensors",
]
