"""The layout canvas: layers as filled outlines, selection highlight, rule markers
and alignment points.

Scene units are micrometres with y pointing up (the view flips Qt's y axis).
Wheel zooms around the cursor, middle or right drag pans, F fits the view.
"""

from __future__ import annotations

import math

import klayout.db as kdb
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

from mems_sketch.core.component import DBU_UM, Geometry

PALETTE = ["#4c78a8", "#f58518", "#54a24b", "#e45756", "#72b7b2", "#b279a2", "#eeca3b", "#9d755d"]
HIGHLIGHT = QColor("#ffd400")
VIOLATION = QColor("#ff2d55")
POINT_STYLES = {  # colour and marker size in pixels
    "declared": (QColor("#3ddc84"), 9),  # the edited component's own points
    "selected": (QColor("#ffd400"), 7),  # points of the selected shape
    "pick": (QColor("#00c8ff"), 11),  # candidates while aligning
}


def layer_color(index: int) -> QColor:
    return QColor(PALETTE[index % len(PALETTE)])


def region_to_path(region: kdb.Region) -> QPainterPath:
    """Merged polygons (with holes) as one odd-even filled painter path, in µm."""
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.OddEvenFill)
    for polygon in region.each_merged():
        _add_loop(path, polygon.each_point_hull())
        for hole in range(polygon.holes()):
            _add_loop(path, polygon.each_point_hole(hole))
    return path


def _add_loop(path: QPainterPath, points) -> None:
    first = True
    for p in points:
        point = QPointF(p.x * DBU_UM, p.y * DBU_UM)
        if first:
            path.moveTo(point)
            first = False
        else:
            path.lineTo(point)
    path.closeSubpath()


class LayoutCanvas(QGraphicsView):
    clicked = Signal(float, float, bool)  # x, y in µm; True when Ctrl/Shift is held
    cursor_moved = Signal(float, float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.setBackgroundBrush(QColor("#1e1f22"))
        self.setTransform(QTransform.fromScale(2, -2))
        self._layer_items: dict[str, QGraphicsPathItem] = {}
        self._overlay: list = []
        self._points: dict[str, list] = {}
        self._pan_from: QPointF | None = None
        self._has_content = False
        # A large scene rect lets the user pan freely beyond the geometry.
        self.scene().setSceneRect(QRectF(-1e6, -1e6, 2e6, 2e6))

    # -- content -----------------------------------------------------------

    def show_geometry(
        self, geometry: Geometry, colors: dict[str, QColor], visible: dict[str, bool]
    ) -> None:
        for item in self._layer_items.values():
            self.scene().removeItem(item)
        self._layer_items.clear()
        for z, (layer, region) in enumerate(sorted(geometry.layers.items())):
            color = colors.get(layer, QColor("#888888"))
            item = QGraphicsPathItem(region_to_path(region))
            fill = QColor(color)
            fill.setAlpha(110)
            pen = QPen(color, 0)  # cosmetic: one pixel wide at any zoom
            item.setPen(pen)
            item.setBrush(QBrush(fill))
            item.setZValue(z)
            item.setVisible(visible.get(layer, True))
            self.scene().addItem(item)
            self._layer_items[layer] = item
        if not self._has_content and geometry.layers:
            self._has_content = True
            self.fit()

    def set_layer_visible(self, layer: str, visible: bool) -> None:
        if layer in self._layer_items:
            self._layer_items[layer].setVisible(visible)

    def show_overlay(
        self, highlight: Geometry | None, markers: list[tuple[float, float, float, float]]
    ) -> None:
        for item in self._overlay:
            self.scene().removeItem(item)
        self._overlay.clear()
        if highlight is not None:
            for region in highlight.layers.values():
                item = QGraphicsPathItem(region_to_path(region))
                pen = QPen(HIGHLIGHT, 2)
                pen.setCosmetic(True)
                item.setPen(pen)
                item.setZValue(1000)
                self.scene().addItem(item)
                self._overlay.append(item)
        for x0, y0, x1, y1 in markers:
            pad = 0.5
            item = QGraphicsRectItem(
                QRectF(x0 - pad, y0 - pad, x1 - x0 + 2 * pad, y1 - y0 + 2 * pad)
            )
            pen = QPen(VIOLATION, 2)
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setZValue(1001)
            self.scene().addItem(item)
            self._overlay.append(item)

    def show_points(
        self, style: str, points: list[tuple[str, float, float]], labels: bool = False
    ) -> None:
        """Mark points (name, x, y) with crosses of a style; replaces that style's markers."""
        for item in self._points.pop(style, []):
            self.scene().removeItem(item)
        color, size = POINT_STYLES[style]
        items = []
        for name, x, y in points:
            marker = _PointMarker(color, size)
            marker.setPos(x, y)
            marker.setToolTip(name)
            items.append(marker)
            if labels:
                text = QGraphicsSimpleTextItem(name)
                text.setBrush(color)
                text.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
                text.setPos(x, y)
                text.setZValue(1100)
                items.append(text)
        for item in items:
            self.scene().addItem(item)
        self._points[style] = items

    def pixels_per_um(self) -> float:
        return abs(self.transform().m11())

    def content_rect(self) -> QRectF:
        rect = QRectF()
        for item in self._layer_items.values():
            if item.isVisible():
                rect = rect.united(item.boundingRect())
        return rect

    def fit(self) -> None:
        rect = self.content_rect()
        if rect.isEmpty():
            rect = QRectF(-100, -100, 200, 200)
        self.zoom_to(rect)

    def zoom_to(self, rect: QRectF) -> None:
        margin = max(rect.width(), rect.height()) * 0.08 + 1
        self.fitInView(
            rect.adjusted(-margin, -margin, margin, margin), Qt.AspectRatioMode.KeepAspectRatio
        )
        # fitInView resets the y flip; restore it while keeping the scale.
        scale = abs(self.transform().m11())
        center = rect.center()
        self.setTransform(QTransform.fromScale(scale, -scale))
        self.centerOn(center)

    # -- interaction -------------------------------------------------------

    def wheelEvent(self, event) -> None:  # noqa: N802
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        scale = abs(self.transform().m11()) * factor
        if 1e-4 < scale < 1e5:
            self.scale(factor, factor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self._pan_from = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            p = self.mapToScene(event.position().toPoint())
            additive = bool(
                event.modifiers()
                & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
            )
            self.clicked.emit(p.x(), p.y(), additive)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._pan_from is not None:
            delta = event.position() - self._pan_from
            self._pan_from = event.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - delta.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - delta.y()))
            return
        p = self.mapToScene(event.position().toPoint())
        self.cursor_moved.emit(p.x(), p.y())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._pan_from is not None:
            self._pan_from = None
            self.unsetCursor()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_F:
            self.fit()
            return
        super().keyPressEvent(event)

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802
        super().drawBackground(painter, rect)
        step = self.grid_step()
        minor = QPen(QColor(255, 255, 255, 14), 0)
        major = QPen(QColor(255, 255, 255, 32), 0)
        axis = QPen(QColor(255, 255, 255, 70), 0)
        left, right = math.floor(rect.left() / step), math.ceil(rect.right() / step)
        top, bottom = math.floor(rect.top() / step), math.ceil(rect.bottom() / step)
        if (right - left) * (bottom - top) > 400_000:
            return
        for i in range(left, right + 1):
            painter.setPen(axis if i == 0 else major if i % 5 == 0 else minor)
            painter.drawLine(QPointF(i * step, rect.top()), QPointF(i * step, rect.bottom()))
        for j in range(top, bottom + 1):
            painter.setPen(axis if j == 0 else major if j % 5 == 0 else minor)
            painter.drawLine(QPointF(rect.left(), j * step), QPointF(rect.right(), j * step))

    def grid_step(self) -> float:
        """Grid spacing in µm: a 1-2-5 step giving at least ~12 px between lines."""
        pixels_per_um = abs(self.transform().m11())
        target = 12 / pixels_per_um
        exponent = math.floor(math.log10(target))
        return next(m * 10**exponent for m in (1, 2, 5, 10) if m * 10**exponent >= target)


class _PointMarker(QGraphicsEllipseItem):
    """A circle with a cross, the same size in pixels at any zoom."""

    def __init__(self, color: QColor, size: int) -> None:
        super().__init__(-size / 2, -size / 2, size, size)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        pen = QPen(color, 1.5)
        self.setPen(pen)
        self.setZValue(1100)
        self._size = size

    def paint(self, painter, option, widget=None) -> None:
        super().paint(painter, option, widget)
        half = self._size / 2
        painter.drawLine(QPointF(-half, 0), QPointF(half, 0))
        painter.drawLine(QPointF(0, -half), QPointF(0, half))
