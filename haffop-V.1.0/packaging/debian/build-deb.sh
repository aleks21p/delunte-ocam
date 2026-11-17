#!/bin/sh
# Build the Debian / Ubuntu edition: dist/haffop_<version>_all.deb
#   usage: packaging/debian/build-deb.sh        (run from anywhere; needs dpkg-deb)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
SRC=$(cd "$HERE/../.." && pwd)
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC/haffop/__init__.py")
BUILD=$(mktemp -d)
trap 'rm -rf "$BUILD"' EXIT
ROOT="$BUILD/haffop_${VERSION}_all"

sh "$SRC/packaging/common/stage-linux.sh" "$SRC" "$ROOT"

install -d "$ROOT/DEBIAN"
SIZE=$(du -sk "$ROOT/usr" | cut -f1)
cat > "$ROOT/DEBIAN/control" <<EOF
Package: haffop
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Installed-Size: $SIZE
Depends: python3 (>= 3.10), python3-pyqt6, python3-psutil, pciutils, usbutils, iw, util-linux, udev, pkexec | policykit-1
Recommends: lm-sensors, dmidecode
Suggests: python3-pynvml, nvidia-smi
Maintainer: Haffop developers <haffop@users.noreply.github.com>
Homepage: https://github.com/aleks21p/delunte-ocam
Description: Hardware viewer and performance unlocker (PyQt6)
 Haffop detects your hardware (CPU, RAM, GPU, storage, Wi-Fi, displays, USB,
 battery, sensors ...) and shows live temperatures. Its tuning tab shows
 whether CPU, GPU and RAM can be overclocked and lets you change clock
 limits, boost, GPU overclock offsets and power limits, plus a one-click
 laptop performance unlock (higher power/temperature limits).
EOF
cat > "$ROOT/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
command -v update-desktop-database >/dev/null && update-desktop-database -q || true
exit 0
EOF
chmod 0755 "$ROOT/DEBIAN/postinst"

mkdir -p "$SRC/dist"
dpkg-deb --root-owner-group --build "$ROOT" "$SRC/dist/haffop_${VERSION}_all.deb"
echo "Built $SRC/dist/haffop_${VERSION}_all.deb"
echo "Install with: sudo apt install $SRC/dist/haffop_${VERSION}_all.deb"
