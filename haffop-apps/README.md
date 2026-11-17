# Haffop apps: download and run

Ready-made builds of Haffop. Python and Qt6 are bundled inside, so you don't need to install anything first.
All three editions use **Haffop 1.1.0**: a cleaner health dashboard, CPU/GPU/RAM/storage/network
benchmarks, component checks, and opt-in combined stress tests (30 seconds to 30 minutes).

| Folder | For | Files |
|---|---|---|
| [windows/](windows/) | Windows 10 / 11 (64-bit) | `Haffop.exe` |
| [ubuntu/](ubuntu/) | Ubuntu 22.04+, Debian 12+, Mint, Pop!_OS (x86_64) | `Haffop-ubuntu-x86_64`, `haffop_1.1.0_amd64.deb`, `install.sh` |
| [fedora/](fedora/) | Fedora 41+ (x86_64) | `Haffop-fedora-x86_64`, `haffop-1.1.0-1.x86_64.rpm`, `install.sh` |

## Windows 11

1. Download `windows/Haffop.exe`.
2. Double-click it. Windows asks for administrator rights, which Haffop needs to change power and overclock settings.
3. If SmartScreen says "Windows protected your PC", click **More info**, then **Run anyway**. The exe isn't code-signed, so this is expected.

## Ubuntu / Debian

Pick one of these:

- **Install package (recommended):** double-click `haffop_1.1.0_amd64.deb`, or run
  `sudo apt install ./haffop_1.1.0_amd64.deb`. Haffop then shows up in your app menu.
- **Install for your user only (no sudo):** download the whole `ubuntu/` folder, then run `./install.sh`.
  Use `./install.sh --remove` to uninstall it.
- **Just run it:** `chmod +x Haffop-ubuntu-x86_64 && ./Haffop-ubuntu-x86_64`

## Fedora

Pick one of these:

- **Install package (recommended):** `sudo dnf install ./haffop-1.1.0-1.x86_64.rpm`. Haffop then shows up in your app menu.
- **Install for your user only (no sudo):** download the whole `fedora/` folder, then run `./install.sh`.
  Use `./install.sh --remove` to uninstall it.
- **Just run it:** `chmod +x Haffop-fedora-x86_64 && ./Haffop-fedora-x86_64`

On Linux, Haffop asks for your password through polkit (`pkexec`) when you apply tuning changes. Viewing hardware never needs root.
For full detail, install the optional tools: `sensors`, `lspci`, `lsusb`, `iw`, `ethtool`, and `nvidia-smi` on NVIDIA systems.

## Testing and benchmarks

* GPU benchmarks require your vendor's **OpenCL runtime/driver**; unsupported GPUs are clearly marked unavailable.
* Wi-Fi/Ethernet throughput requires **iperf3** locally and your own trusted LAN peer running `iperf3 -s`.
  The OS route selects the adapter. Other tests work without network access.
* Storage tests use a 64 MiB temporary file on the selected filesystem; cached scores are not raw SSD performance.
* Offline historical public reference tables (CC BY 4.0, September 2021) are included for CPU, GPU, RAM and storage.
  They are searchable context, not factory health standards or directly comparable Haffop scores.
* Save/load matching JSON baselines for numeric offline comparisons. No scores or hardware data are uploaded.
* Stress tests require confirmation and can be stopped. A temperature safety guard uses available telemetry;
  absent sensors cannot protect against heat. Windows CPU temperatures usually need LibreHardwareMonitor's WMI provider.

See the [source documentation](../haffop-V.1.0/README.md) for precise workloads, thresholds and limitations.

## Rebuilding the apps

All three builds are made on Linux with containers. Run this from this folder (it needs `podman` or `docker`):

```sh
./build-apps.sh                  # all three
./build-apps.sh fedora windows   # or just some
```

- **ubuntu** builds in an `ubuntu:22.04` container, so the binary works on Ubuntu 22.04 and newer.
- **fedora** builds in a `fedora:41` container.
- **windows** cross-builds with Wine and Windows Python (`tobix/pywine`).
  It pins PyQt6 6.7 because newer Qt DLLs don't load under that Wine.
  On a real Windows PC you can build it instead with `..\haffop-V.1.0\packaging\windows\build.bat`.

The source code lives in [../haffop-V.1.0](../haffop-V.1.0).
