"""Rasterise hwmonitor.svg into the hicolor PNG set used by install.sh.

Run after editing the SVG:

    .venv/bin/python tools/render_icons.py
"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

SIZES = (16, 22, 24, 32, 48, 64, 128, 256)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "hwmonitor", "ui", "icons", "hwmonitor.svg")
DEST = os.path.join(ROOT, "icons", "hicolor")


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QGuiApplication(sys.argv)

    with open(SOURCE, "rb") as fh:
        data = QByteArray(fh.read())

    for size in SIZES:
        renderer = QSvgRenderer(data)
        if not renderer.isValid():
            print(f"SVG を読み込めません: {SOURCE}", file=sys.stderr)
            return 1
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        renderer.render(painter)
        painter.end()

        directory = os.path.join(DEST, f"{size}x{size}", "apps")
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "hwmonitor.png")
        if not image.save(path, "PNG"):
            print(f"保存に失敗しました: {path}", file=sys.stderr)
            return 1
        print(f"  {path}")

    scalable = os.path.join(DEST, "scalable", "apps")
    os.makedirs(scalable, exist_ok=True)
    with open(SOURCE, "rb") as src, open(os.path.join(scalable, "hwmonitor.svg"), "wb") as dst:
        dst.write(src.read())
    print(f"  {os.path.join(scalable, 'hwmonitor.svg')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
