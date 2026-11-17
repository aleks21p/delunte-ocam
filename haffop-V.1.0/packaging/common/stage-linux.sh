#!/bin/sh
# Stage the Linux file layout shared by the Debian and Fedora packages.
#   usage: stage-linux.sh <source dir (haffop-V.1.0)> <destination root>
set -eu
SRC=$1
DEST=$2

install -d "$DEST/usr/share/haffop" "$DEST/usr/bin" "$DEST/usr/libexec/haffop" \
           "$DEST/usr/share/polkit-1/actions" "$DEST/usr/share/applications" \
           "$DEST/usr/share/icons/hicolor/scalable/apps" "$DEST/usr/share/doc/haffop"

# Python package (without caches)
cp -r "$SRC/haffop" "$DEST/usr/share/haffop/"
find "$DEST/usr/share/haffop" -name '__pycache__' -type d -prune -exec rm -rf {} +

install -m 0755 "$SRC/packaging/common/haffop-launcher.sh" "$DEST/usr/bin/haffop"
install -m 0755 "$SRC/haffop/tuning/helper.py" "$DEST/usr/libexec/haffop/haffop-helper"
install -m 0644 "$SRC/packaging/common/org.haffop.helper.policy" "$DEST/usr/share/polkit-1/actions/"
install -m 0644 "$SRC/packaging/common/haffop.desktop" "$DEST/usr/share/applications/"
install -m 0644 "$SRC/haffop/resources/haffop.svg" "$DEST/usr/share/icons/hicolor/scalable/apps/haffop.svg"
install -m 0644 "$SRC/README.md" "$DEST/usr/share/doc/haffop/README.md"
