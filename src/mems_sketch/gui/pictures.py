"""Pictures of built components, drawn offscreen with no window (for the MCP server).

Each layer is filled in its colour on the canvas (by its place in the process),
bottom of the stack first, over a white background with a scale bar. Points can
be marked and labelled, violations boxed in red, and several builds put side by
side with a caption each (a parameter sweep). Needs PySide6 (the ``gui`` extra).
"""

from __future__ import annotations

import math
import os
from collections.abc import Sequence
from dataclasses import dataclass, field

Box = tuple[float, float, float, float]

MARGIN = 24  # px around a picture
CAPTION = 18  # px under a picture in a grid, for its caption
MAX_SIDE = 2400  # px: the largest picture side asked for


@dataclass
class Picture:
    """One build to draw: its geometry, and what to mark on it."""

    geometry: object  # mems_sketch.core.component.Geometry
    points: dict[str, tuple[float, float]] = field(default_factory=dict)
    ports: dict[str, tuple[float, float, float, float]] = field(
        default_factory=dict
    )  # x, y, angle, width
    marks: list[tuple[Box, str]] = field(default_factory=list)  # (box, label)
    caption: str = ""


def application():
    """The Qt application painting needs (offscreen unless one exists)."""
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as error:  # pragma: no cover - the gui extra is missing
        raise ValueError(
            "rendering needs PySide6: install mems-sketch with the gui extra"
        ) from error
    app = QApplication.instance()
    if app is None:  # a widgets one, as the editor and its tests would make
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication([])
    return app


def render_png(
    pictures: Sequence[Picture],
    layers: Sequence[str],
    stack: Sequence[str],
    width: int = 800,
    height: int = 600,
    box: Box | None = None,
    columns: int | None = None,
) -> bytes:
    """A PNG of the pictures, in a grid of ``columns`` (default: about square);
    each cell ``width`` × ``height`` px, all at one scale so they compare.
    ``layers`` are the process's layers (their colours), ``stack`` the order to
    draw them in. ``box`` (x0, y0, x1, y1 in µm) zooms in."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPolygonF

    from mems_sketch.gui import theme

    application()
    canvas = theme.get("light").canvas  # a picture is on white, whatever the editor's theme
    palette = canvas["layers"]
    ink = QColor(canvas["overlay"])
    if not pictures:
        raise ValueError("nothing to draw")
    width, height = (max(64, min(int(v), MAX_SIDE)) for v in (width, height))
    count = len(pictures)
    columns = max(1, min(columns or math.ceil(math.sqrt(count)), count))
    rows = math.ceil(count / columns)
    caption = CAPTION if any(p.caption for p in pictures) else 0
    view = box or _union([p.geometry.bbox() for p in pictures])
    if view is None:
        raise ValueError("there is nothing to draw: the geometry is empty")
    x0, y0, x1, y1 = view
    span_x, span_y = max(x1 - x0, 1e-3), max(y1 - y0, 1e-3)
    scale = min((width - 2 * MARGIN) / span_x, (height - 2 * MARGIN) / span_y)
    colours = {name: QColor(palette[i % len(palette)]) for i, name in enumerate(layers)}
    order = [n for n in stack if n in colours] + [n for n in layers if n not in stack]

    image = QImage(width * columns, (height + caption) * rows, QImage.Format.Format_ARGB32)
    image.fill(QColor(canvas["background"]))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    font = QFont()
    font.setPixelSize(11)
    painter.setFont(font)
    for k, picture in enumerate(pictures):
        left, top = (k % columns) * width, (k // columns) * (height + caption)
        # centred in its cell; y up
        ox = left + (width - span_x * scale) / 2 - x0 * scale
        oy = top + (height + span_y * scale) / 2 + y0 * scale

        def at(x: float, y: float, ox=ox, oy=oy) -> QPointF:
            return QPointF(ox + x * scale, oy - y * scale)

        painter.save()
        cell = QRectF(left, top, width, height)
        if count > 1:  # a frame, so a cut-off edge does not read as a notch
            painter.setPen(QPen(QColor(canvas["overlay_muted"]), 1))
            painter.drawRect(cell.adjusted(2, 2, -2, -2))
        painter.setClipRect(cell.adjusted(2, 2, -2, -2) if count > 1 else cell)
        geometry = picture.geometry
        drawn = geometry.layer_names()
        for layer in [n for n in order if n in drawn] + [n for n in drawn if n not in order]:
            colour = colours.get(layer, QColor(canvas["unknown_layer"]))
            fill = QColor(colour)
            fill.setAlpha(110)
            painter.setPen(QPen(colour, 1))
            painter.setBrush(fill)
            outline = QPainterPath()
            outline.setFillRule(Qt.FillRule.OddEvenFill)
            for polygon in geometry.polygons(layer):
                for ring in [polygon.hull, *polygon.holes]:
                    outline.addPolygon(QPolygonF([at(x, y) for x, y in ring]))
                    outline.closeSubpath()
            painter.drawPath(outline)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(canvas["violation"]), 2))
        for (bx0, by0, bx1, by1), label in picture.marks:
            corner, far = at(bx0, by1), at(bx1, by0)
            rect = QRectF(corner, far).adjusted(-3, -3, 3, 3)
            painter.drawRect(rect)
            if label:
                painter.drawText(rect.bottomLeft() + QPointF(0, 12), label)
        painter.setPen(QPen(ink, 1))
        for name, (px, py) in picture.points.items():
            p = at(px, py)
            painter.drawLine(p + QPointF(-4, 0), p + QPointF(4, 0))
            painter.drawLine(p + QPointF(0, -4), p + QPointF(0, 4))
            painter.drawText(p + QPointF(5, -4), name)
        port_pen = QPen(QColor(canvas["selected"]), 2)
        for name, (px, py, angle, port_width) in picture.ports.items():
            _arrow(painter, at(px, py), angle, port_width * scale, port_pen)
            painter.setPen(QPen(ink, 1))
            painter.drawText(at(px, py) + QPointF(6, 14), f"{name} {angle:g}°")
        _scale_bar(painter, left, top, height, scale, ink)
        painter.restore()
        if picture.caption:
            painter.setPen(QPen(ink, 1))
            painter.drawText(
                QRectF(left, top + height, width, caption),
                Qt.AlignmentFlag.AlignCenter,
                picture.caption,
            )
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data.data())


def _arrow(painter, at, angle_deg: float, width_px: float, pen) -> None:
    """A port: a bar across it as wide as its wire, and an arrow the way a wire leaves."""
    from PySide6.QtCore import QPointF

    a = math.radians(angle_deg)
    out = QPointF(math.cos(a), -math.sin(a))  # the picture's y points down
    side = QPointF(-out.y(), out.x())
    half = max(width_px, 8) / 2
    tip = at + out * 24
    painter.setPen(pen)
    painter.drawLine(at - side * half, at + side * half)
    painter.drawLine(at, tip)
    painter.drawLine(tip, tip - out * 7 + side * 4)
    painter.drawLine(tip, tip - out * 7 - side * 4)


def _scale_bar(painter, left: float, top: float, height: float, scale: float, ink) -> None:
    """A bar of a round length, about 80 px, at the bottom left."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPen

    target = 80 / scale  # µm
    step = 10 ** math.floor(math.log10(target))
    length = next(m * step for m in (5, 2, 1) if m * step <= target)
    x, y = left + 10, top + height - 10
    painter.setPen(QPen(ink, 2))
    painter.drawLine(QPointF(x, y), QPointF(x + length * scale, y))
    painter.drawText(QPointF(x, y - 5), f"{length:g} µm")


def _union(boxes: Sequence[Box | None]) -> Box | None:
    found = [b for b in boxes if b is not None]
    if not found:
        return None
    return (
        min(b[0] for b in found),
        min(b[1] for b in found),
        max(b[2] for b in found),
        max(b[3] for b in found),
    )
