#!/bin/sh
# Build every Haffop app into this folder using podman (or docker) containers:
#   ubuntu/   Haffop-ubuntu-x86_64 + haffop_<ver>_amd64.deb   (built on Ubuntu 22.04 -> runs on 22.04+ / Debian 12+)
#   fedora/   Haffop-fedora-x86_64 + haffop-<ver>-1.x86_64.rpm (built on Fedora 41  -> runs on 41+)
#   windows/  Haffop.exe                                       (cross-built with Wine + Windows Python)
# usage: ./build-apps.sh [ubuntu] [fedora] [windows]     (no arguments = all three)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
SRC=$(cd "$HERE/../haffop-V.1.0" && pwd)
ENGINE=$(command -v podman || command -v docker || true)
[ -n "$ENGINE" ] || { echo "Install podman (sudo dnf install podman / sudo apt install podman) or docker." >&2; exit 1; }

TARGETS=${*:-ubuntu fedora windows}
# label=disable: don't relabel the source tree for SELinux, just allow the container to read it
RUN="$ENGINE run --rm --security-opt label=disable -v $SRC:/src:ro -v $HERE/scripts:/scripts:ro"

for t in $TARGETS; do
    mkdir -p "$HERE/$t"
    case $t in
    ubuntu)  $RUN -v "$HERE/ubuntu:/out"  docker.io/library/ubuntu:22.04 sh /scripts/build-linux.sh ubuntu ;;
    fedora)  $RUN -v "$HERE/fedora:/out"  registry.fedoraproject.org/fedora:41 sh /scripts/build-linux.sh fedora ;;
    windows) $RUN -v "$HERE/windows:/out" docker.io/tobix/pywine:3.12 sh /scripts/build-windows-wine.sh ;;
    *) echo "unknown target: $t (use ubuntu, fedora, windows)" >&2; exit 1 ;;
    esac
done
echo "All done."
