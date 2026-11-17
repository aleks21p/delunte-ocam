# Haffop V1.0 — hardware viewer & performance unlocker

A PyQt6 desktop app in the style of Polychromatic. It finds your hardware, shows live temperatures,
tells you whether your **CPU, GPU and RAM can be overclocked**, and lets you change their speed.
On laptops it can also unlock higher power and temperature limits.

It comes in three editions built from the same code:

| Edition | Target | Package | How it gets privileges |
|---|---|---|---|
| **Windows 11** | Windows 10/11 x64 | `Haffop.exe` (PyInstaller) | Asks for administrator rights (UAC) |
| **Debian / Ubuntu** | Debian 12+, Ubuntu 22.04+, Mint, Pop!_OS | `haffop_1.0.0_all.deb` | polkit (`pkexec`) helper |
| **Fedora** | Fedora 38+ (also RHEL-likes) | `haffop-1.0.0-1.noarch.rpm` | polkit (`pkexec`) helper |

At runtime Haffop checks which edition it's on and adjusts its hints (`apt` vs `dnf`, GRUB vs `grubby`, and so on).

> **Just want to run it?** Ready-built apps (Windows `.exe`, plus a one-file binary and a `.deb`/`.rpm` for Ubuntu and Fedora)
> are in [../haffop-apps](../haffop-apps/README.md). These bundle Python and Qt, so nothing else needs installing.

## Features

### Tuning tab (main tab)
* **Overview**: one card each for the CPU, every GPU and RAM. Each card shows a badge and a plain answer to
  *"Can it be overclocked?"*, plus live temperature, clock speed and power.
* **CPU**:
  * Checks whether the multiplier is unlocked (Intel K/KF/KS/HX/X, desktop Ryzen) or locked.
  * Linux: max/min clock, governor, energy-performance preference and Turbo/boost on/off.
    Desktops also get PL1/PL2 power limits.
  * Windows: power plan (it can create **Ultimate Performance**), max/min processor state and boost mode.
* **GPU**:
  * NVIDIA (Linux and Windows): power limit, core clock cap, and **core/memory clock offsets** (real
    overclocking through NVML, or `nvidia-settings` on X11).
  * AMD (Linux): power limit, performance level and **OverDrive core/memory clocks**. If OverDrive is off,
    Haffop shows the kernel option that turns it on for your distro.
  * Intel iGPU (Linux, i915 and xe drivers): min/max/boost clocks within the factory range.
  * Windows AMD/Intel cards: Haffop tells you which official vendor tool to use.
* **RAM**: RAM speed can only be changed in the BIOS. Haffop compares rated and running speed, tells you
  when to enable XMP/EXPO, and warns about mixed or soldered modules. A button reboots straight into
  the BIOS/UEFI setup.
* **Laptop performance unlock**: shown only on laptops, with a one-click **Unlock maximum performance** button:
  * Linux:
    * Power profile set to *performance*.
    * Intel PL1/PL2 raised. Both the MSR and the firmware MMIO limits are changed, because many laptops
      enforce the lower one.
    * Ryzen STAPM/PPT power limits and the temperature limit (`ryzenadj`).
    * Stops `thermald` throttling early.
    * Starts NVIDIA Dynamic Boost.
  * Windows: *Best performance* power mode, active cooling policy, aggressive boost and the Ultimate plan.
  * GPU power limits are raised to their maximum.
* **Restore startup settings** puts every setting back to the value it had when Haffop started. All changes
  also reset on reboot.

### Hardware tab
* **Simple** view: CPU (cores, clock, load, temperature), RAM (capacity, speed vs. rated, modules),
  GPU (integrated or dedicated, VRAM, clock, temperature), storage (SSD/NVMe/HDD, capacity, temperature),
  Wi-Fi card (highest standard it supports, link speed, signal, temperature), and a live list of
  every temperature sensor.
* **Expanded** view: a searchable tree of every physical component Haffop can find:
  * System, motherboard and BIOS
  * CPU caches and instruction sets
  * Every RAM slot, including empty ones
  * GPUs
  * Displays, with size in inches, native mode and maker decoded from the EDID
  * Drives, with firmware and PCIe link
  * Network adapters, Bluetooth, audio codecs, cameras, input devices and USB devices
  * Battery health and cycle count
  * All PCI devices
  * Every sensor (temperature, fans, clocks, power)

  You can copy or save the whole tree as a text report.

## Running from source

### Fedora: install it as a desktop app

From the extracted `haffop-V.1.0` folder, run:

```bash
./install-fedora.sh
```

This installs Haffop and its Python dependencies for your user, creates a launcher in
`~/.local/bin/haffop`, and adds it to the desktop applications menu. No `sudo` is needed for
installation. Changes that require administrator privileges will still ask for authorization.
To remove the user installation, run `./uninstall-fedora.sh`.

To run directly from the source folder instead:

```bash
./run-fedora.sh
```

The first launch creates a local `.venv` and installs the Python dependencies; later launches reuse it.

```bash
cd haffop-V.1.0
python3 -m pip install -r requirements.txt      # or: apt/dnf install python3-pyqt6 python3-psutil
python3 -m haffop
```
On Windows, run `py -m haffop` from an **administrator** terminal so changes can be applied.
For NVIDIA overclocking offsets, also run `pip install nvidia-ml-py`.

## Building the editions

| Edition | Command | Output |
|---|---|---|
| Windows 11 | `packaging\windows\build.bat` (or `build.ps1`) | `dist\Haffop.exe` |
| Debian/Ubuntu | `sh packaging/debian/build-deb.sh` (needs `dpkg-deb`) | `dist/haffop_1.0.0_all.deb` |
| Fedora | `sh packaging/fedora/build-rpm.sh` (needs `rpm-build`) | `dist/haffop-1.0.0-1.*.noarch.rpm` |

Install with `sudo apt install ./dist/haffop_1.0.0_all.deb` or `sudo dnf install ./dist/haffop-*.rpm`.

The Linux packages install:
* `/usr/bin/haffop`
* the app itself in `/usr/share/haffop`
* the privileged helper as `/usr/libexec/haffop/haffop-helper`
* a polkit policy, so you're asked for your password once per session
* a desktop entry and an icon

## Security model
* The GUI always runs as your normal user on Linux.
* Changes go to `haffop/tuning/helper.py` through `pkexec`. The helper:
  * uses only the Python standard library;
  * accepts only allow-listed sysfs files (cpufreq, intel_pstate, RAPL, platform_profile, drm/amdgpu/i915);
  * resolves symlinks and rejects any path that ends up outside `/sys/devices` or `/sys/firmware/acpi`;
  * checks values against a strict pattern;
  * runs only `nvidia-smi`, `ryzenadj` and `systemctl start|stop thermald|nvidia-powerd`, and only with
    argument shapes on its allow-list.
* On Windows the app needs administrator rights and only calls `powercfg`, `nvidia-smi`, NVML and `shutdown /fw`.

## Notes and limits
* No operating system can raise RAM clocks or a locked CPU multiplier at runtime. Those settings live only in
  the BIOS, and Haffop says so instead of pretending otherwise.
* On Windows, CPU and motherboard temperatures need administrator rights or a running
  [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor). Haffop reads its
  sensors automatically when it's running.
* Overclocking and raising limits increase heat, can cause instability, and can shorten component life.
  Use these features at your own risk.

## Tests
```bash
pip install pytest && python3 -m pytest tests
```
