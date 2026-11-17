Name:           haffop
Version:        %{haffop_version}
Release:        1%{?dist}
Summary:        Hardware viewer and performance unlocker (PyQt6)
License:        Unspecified
URL:            https://github.com/aleks21p/delunte-ocam
Source0:        haffop-%{version}.tar.gz
BuildArch:      noarch

Requires:       python3 >= 3.10
Requires:       python3-pyqt6
Requires:       python3-psutil
Requires:       pciutils
Requires:       usbutils
Requires:       iw
Requires:       util-linux
Requires:       systemd-udev
Requires:       polkit
Recommends:     lm_sensors
Recommends:     dmidecode
Suggests:       python3-pynvml

%description
Haffop detects your hardware (CPU, RAM, GPU, storage, Wi-Fi, displays, USB,
battery, sensors ...) and shows live temperatures. Its tuning tab shows
whether CPU, GPU and RAM can be overclocked and lets you change clock limits,
boost, GPU overclock offsets and power limits, plus a one-click laptop
performance unlock (higher power/temperature limits).

%prep
%setup -q

%build
# Pure Python - nothing to build.

%install
sh packaging/common/stage-linux.sh . %{buildroot}

%files
%{_docdir}/haffop/
%{_bindir}/haffop
%{_datadir}/haffop/
%{_libexecdir}/haffop/
%{_datadir}/polkit-1/actions/org.haffop.helper.policy
%{_datadir}/applications/haffop.desktop
%{_datadir}/icons/hicolor/scalable/apps/haffop.svg

%changelog
* Tue Nov 17 2026 Haffop developers <haffop@users.noreply.github.com> - 1.0.0-1
- Initial release
