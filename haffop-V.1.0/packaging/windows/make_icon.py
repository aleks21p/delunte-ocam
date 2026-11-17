"""Render an SVG to a multi-resolution .ico using Qt.  usage: make_icon.py in.svg out.ico"""

import os
import sys

from PyQt6.QtCore import QBuffer, QIODevice, QSize
from PyQt6.QtGui import QGuiApplication, QIcon


def main() -> int:
    src, dst = sys.argv[1], sys.argv[2]
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    app = QGuiApplication(sys.argv[:1] + ["-platform", "offscreen"])  # must stay alive while rendering
    app.processEvents()
    icon = QIcon(src)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = []
    for s in sizes:
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        icon.pixmap(QSize(s, s)).toImage().save(buf, "PNG")
        images.append(bytes(buf.data()))
    # ICO container with embedded PNGs (supported since Windows Vista)
    header = bytearray(b"\x00\x00\x01\x00") + len(images).to_bytes(2, "little")
    offset = 6 + 16 * len(images)
    entries, data = bytearray(), bytearray()
    for s, png in zip(sizes, images):
        dim = 0 if s >= 256 else s
        entries += bytes([dim, dim, 0, 0]) + (1).to_bytes(2, "little") + (32).to_bytes(2, "little")
        entries += len(png).to_bytes(4, "little") + (offset + len(data)).to_bytes(4, "little")
        data += png
    with open(dst, "wb") as fh:
        fh.write(header + entries + data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
