#!/bin/sh
# Install the Haffop app for your user only (no sudo): adds it to the app menu.
#   ./install.sh            install / update
#   ./install.sh --remove   uninstall
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
BIN_DIR="$HOME/.local/bin"
APPS_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"

if [ "${1:-}" = "--remove" ]; then
    rm -f "$BIN_DIR/haffop" "$APPS_DIR/haffop.desktop" "$ICON_DIR/haffop.svg"
    update-desktop-database "$APPS_DIR" 2>/dev/null || true
    echo "Haffop removed."
    exit 0
fi

APP=$(ls "$HERE"/Haffop-*-"$(uname -m)" 2>/dev/null | head -n 1)
[ -n "$APP" ] || { echo "Haffop-*-$(uname -m) not found next to this script." >&2; exit 1; }

install -d "$BIN_DIR" "$APPS_DIR" "$ICON_DIR"
install -m 0755 "$APP" "$BIN_DIR/haffop"
[ -f "$HERE/haffop.svg" ] && install -m 0644 "$HERE/haffop.svg" "$ICON_DIR/haffop.svg"
cat > "$APPS_DIR/haffop.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Haffop
GenericName=Hardware Monitor & Performance Unlocker
Comment=View your hardware, live temperatures, and overclock / unlock CPU, GPU and laptop power limits
Exec=$BIN_DIR/haffop
Icon=haffop
Terminal=false
Categories=System;Monitor;HardwareSettings;Qt;
Keywords=overclock;hardware;temperature;cpu;gpu;ram;sensors;laptop;performance;
StartupWMClass=haffop
EOF
update-desktop-database "$APPS_DIR" 2>/dev/null || true
echo "Haffop installed - open it from your app menu, or run: haffop"
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "(add $BIN_DIR to PATH to start it with 'haffop' from a terminal)";; esac
