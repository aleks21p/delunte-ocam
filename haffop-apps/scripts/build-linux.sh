#!/bin/sh
# Runs INSIDE an ubuntu:22.04 or fedora container (see ../build-apps.sh).
# Builds the one-file Haffop executable with PyInstaller plus the native package.
#   /src = haffop-V.1.0 (read only)   /out = haffop-apps/<ubuntu|fedora>
set -eu
DISTRO=$1
SRC=/src
OUT=/out
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC/haffop/__init__.py")
WORK=/tmp/haffop-build

echo "==> [$DISTRO] installing build dependencies"
case $DISTRO in
ubuntu)
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    # Qt's xcb plugin needs these; PyInstaller bundles the ones that aren't always installed (e.g. libxcb-cursor0)
    apt-get install -y -qq --no-install-recommends python3 python3-venv python3-pip libpython3-dev binutils dpkg-dev \
        libxcb-cursor0 libxkbcommon-x11-0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
        libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 libegl1 libgl1 libfontconfig1 \
        libdbus-1-3 libglib2.0-0 >/dev/null
    ;;
fedora)
    dnf install -y -q python3 python3-pip binutils rpm-build xcb-util-cursor libxkbcommon-x11 \
        xcb-util-wm xcb-util-image xcb-util-keysyms xcb-util-renderutil mesa-libEGL mesa-libGL \
        fontconfig dbus-libs glib2 >/dev/null
    ;;
*)
    echo "unknown distro $DISTRO" >&2; exit 1 ;;
esac

echo "==> [$DISTRO] building Haffop $VERSION with PyInstaller"
rm -rf "$WORK"; mkdir -p "$WORK"
cp -r "$SRC/haffop" "$WORK/"
find "$WORK" -name '__pycache__' -type d -prune -exec rm -rf {} +
printf 'from haffop.app import main\nraise SystemExit(main())\n' > "$WORK/run_haffop.py"
python3 -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install -q --upgrade pip
"$WORK/venv/bin/pip" install -q -r "$SRC/requirements.txt" pyinstaller
cd "$WORK"
"$WORK/venv/bin/pyi-makespec" --onefile --windowed \
    --name Haffop \
    --add-data "$WORK/haffop/resources:resources" \
    --collect-submodules haffop \
    --specpath "$WORK/work" \
    "$WORK/run_haffop.py" >/dev/null
# Use the desktop's own fontconfig/freetype: an older bundled copy can't parse newer fonts.conf files
sed -i 's/^pyz = PYZ/a.binaries = [b for b in a.binaries if not b[0].startswith(("libfontconfig", "libfreetype"))]\npyz = PYZ/' \
    "$WORK/work/Haffop.spec"
grep -q libfontconfig "$WORK/work/Haffop.spec"
"$WORK/venv/bin/pyinstaller" --noconfirm --clean --log-level ERROR \
    --distpath "$WORK/dist" --workpath "$WORK/work" "$WORK/work/Haffop.spec"
BIN="$WORK/dist/Haffop"

# Smoke test: the bundled binary must start and answer as its own (root) helper
test "$(echo '[]' | "$BIN" --haffop-helper)" = "[]"

ARCH=$(uname -m)
install -m 0755 "$BIN" "$OUT/Haffop-$DISTRO-$ARCH"
install -m 0644 "$SRC/haffop/resources/haffop.svg" "$OUT/haffop.svg"

echo "==> [$DISTRO] building the native package"
PKG="$WORK/pkgroot"
install -d "$PKG/usr/bin" "$PKG/usr/share/applications" "$PKG/usr/share/icons/hicolor/scalable/apps" \
           "$PKG/usr/share/doc/haffop"
install -m 0755 "$BIN" "$PKG/usr/bin/haffop"
install -m 0644 "$SRC/packaging/common/haffop.desktop" "$PKG/usr/share/applications/haffop.desktop"
install -m 0644 "$SRC/haffop/resources/haffop.svg" "$PKG/usr/share/icons/hicolor/scalable/apps/haffop.svg"
install -m 0644 "$SRC/README.md" "$PKG/usr/share/doc/haffop/README.md"

case $DISTRO in
ubuntu)
    install -d "$PKG/DEBIAN"
    cat > "$PKG/DEBIAN/control" <<EOF
Package: haffop
Version: $VERSION
Section: utils
Priority: optional
Architecture: $(dpkg --print-architecture)
Maintainer: Haffop <haffop@localhost>
Recommends: pkexec | policykit-1, lm-sensors, pciutils, usbutils, iw, ethtool
Suggests: ryzenadj
Description: Hardware viewer and performance unlocker (Ubuntu / Debian edition)
 Shows CPU, GPU, RAM, storage, Wi-Fi and live temperatures, and lets you
 change CPU/GPU clocks and laptop power limits where the hardware allows it.
 Self-contained build: Python and Qt6 are bundled.
EOF
    dpkg-deb --root-owner-group --build "$PKG" "$OUT/haffop_${VERSION}_$(dpkg --print-architecture).deb" >/dev/null
    ;;
fedora)
    mkdir -p "$WORK/rpm/SPECS"
    cat > "$WORK/rpm/SPECS/haffop.spec" <<EOF
Name:           haffop
Version:        $VERSION
Release:        1
Summary:        Hardware viewer and performance unlocker (Fedora edition)
License:        Unspecified
Recommends:     polkit lm_sensors pciutils usbutils iw ethtool
# PyInstaller one-file binaries carry their payload after the ELF; stripping would destroy it
%global debug_package %{nil}
%global __os_install_post %{nil}
%global _build_id_links none
AutoReqProv:    no

%description
Shows CPU, GPU, RAM, storage, Wi-Fi and live temperatures, and lets you
change CPU/GPU clocks and laptop power limits where the hardware allows it.
Self-contained build: Python and Qt6 are bundled.

%install
cp -a $PKG/. %{buildroot}/

%files
/usr/bin/haffop
/usr/share/applications/haffop.desktop
/usr/share/icons/hicolor/scalable/apps/haffop.svg
%doc /usr/share/doc/haffop/README.md
EOF
    rpmbuild --quiet -bb --define "_topdir $WORK/rpm" "$WORK/rpm/SPECS/haffop.spec"
    cp "$WORK"/rpm/RPMS/*/haffop-*.rpm "$OUT/"
    ;;
esac

echo "==> [$DISTRO] done:"; ls -lh "$OUT"
