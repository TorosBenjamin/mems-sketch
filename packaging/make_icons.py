"""The app's icon files, drawn from the editor's own icon (the one its
windows show): mems-sketch.png for Linux, .ico for Windows, .icns for macOS,
in packaging/icons/. Run before PyInstaller; needs PySide6 and Pillow."""

import io
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

from mems_sketch.gui import icons

OUT = Path(__file__).parent / "icons"
NAME = "component"  # the main window's icon


def render(size: int) -> Image.Image:
    renderer = QSvgRenderer(QByteArray(icons.svg(NAME).encode()))
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return Image.open(io.BytesIO(bytes(buffer.data())))


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 - Qt needs one to draw
    OUT.mkdir(exist_ok=True)
    big = render(1024)
    big.resize((256, 256), Image.LANCZOS).save(OUT / "mems-sketch.png")
    big.save(OUT / "mems-sketch.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    big.save(OUT / "mems-sketch.icns")
    print(f"icons in {OUT}")


if __name__ == "__main__":
    main()
