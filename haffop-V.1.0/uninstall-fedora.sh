#!/bin/sh
set -eu

DATA_HOME=${XDG_DATA_HOME:-"$HOME/.local/share"}
BIN_HOME=${XDG_BIN_HOME:-"$HOME/.local/bin"}
rm -rf "$DATA_HOME/haffop"
rm -f "$DATA_HOME/applications/haffop.desktop" "$BIN_HOME/haffop"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DATA_HOME/applications" >/dev/null 2>&1 || true
fi
echo "Haffop has been removed from this user account."
