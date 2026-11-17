#!/bin/sh
# Build the Fedora edition: dist/haffop-<version>-1.<dist>.noarch.rpm
#   usage: packaging/fedora/build-rpm.sh        (needs: sudo dnf install rpm-build)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
SRC=$(cd "$HERE/../.." && pwd)
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC/haffop/__init__.py")
TOP=$(mktemp -d)
trap 'rm -rf "$TOP"' EXIT
mkdir -p "$TOP/SOURCES" "$TOP/stage/haffop-$VERSION"

cp -r "$SRC/haffop" "$SRC/packaging" "$SRC/README.md" "$TOP/stage/haffop-$VERSION/"
find "$TOP/stage" -name '__pycache__' -type d -prune -exec rm -rf {} +
tar -C "$TOP/stage" -czf "$TOP/SOURCES/haffop-$VERSION.tar.gz" "haffop-$VERSION"

rpmbuild --define "_topdir $TOP" --define "haffop_version $VERSION" -bb "$HERE/haffop.spec"
mkdir -p "$SRC/dist"
cp "$TOP"/RPMS/noarch/*.rpm "$SRC/dist/"
echo "Built: $(ls "$SRC"/dist/*.rpm)"
echo "Install with: sudo dnf install $SRC/dist/haffop-$VERSION-*.noarch.rpm"
