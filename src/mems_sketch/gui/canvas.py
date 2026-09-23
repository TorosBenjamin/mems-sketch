"""The layout canvas: layers as filled outlines, selection highlight, rule markers,
alignment points and rulers.

The canvas itself only zooms (wheel) and pans (middle or right drag, left drag
while Space is held or in hand mode). Left-button presses, moves and releases
are passed on as signals; the active tool (see :mod:`mems_sketch.gui.tools`)
decides what they do.

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
POINT_SIZES = {  # marker size in pixels
    "declared": 9,  # the edited component's own points
    "selected": 7,  # points of the selected shape
    "pick": 11,  # candidates while aligning
    "snap": 13,  # the point a drag snaps to
    "anchor": 9,  # a point a tool has fixed (move base, rotation pivot, ruler start)
}
# Colours per canvas theme. Grid lines are drawn with the ``grid`` colour at
# increasing opacity for minor lines, every fifth line and the axes.
THEMES = {
    "light": {
        "background": "#ffffff",
        "grid": (0, 0, 0),
        "grid_alpha": (18, 40, 90),
        "highlight": "#e0007a",
        "violation": "#d7002a",
        "declared": "#008a3e",
        "selected": "#e0007a",
        "pick": "#0072d6",
        "snap": "#e0007a",
        "anchor": "#e0007a",
        "ruler": "#b35c00",
    },
    "dark": {
        "background": "#1e1f22",
        "grid": (255, 255, 255),
        "grid_alpha": (14, 32, 70),
        "highlight": "#ffd400",
        "violation": "#ff2d55",
        "declared": "#3ddc84",
        "selected": "#ffd400",
        "pick": "#00c8ff",
        "snap": "#ffd400",
        "anchor": "#ffd400",
        "ruler": "#ffb000",
    },
}
DEFAULT_THEME = "light"


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
    # Left button, in µm, with the keyboard modifiers (for the active tool).
    pressed = Signal(float, float, object)
    moved = Signal(float, float, object, bool)  # True while the left button is down
    released = Signal(float, float, object)
    double_clicked = Signal(float, float)
    nudged = Signal(int, int, bool)  # arrow keys: steps in x and y; True for fine steps
    key_pressed = Signal(object)  # Enter or Backspace, for the active tool
    cursor_moved = Signal(float, float)
    view_changed = Signal()  # zoomed or panned

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
        self.theme = THEMES[DEFAULT_THEME]
        self.setBackgroundBrush(QColor(self.theme["background"]))
        self.setTransform(QTransform.fromScale(2, -2))
        self._layer_items: dict[str, QGraphicsPathItem] = {}
        self._overlay: list = []
        self._points: dict[str, list] = {}
        self._pan_from: QPointF | None = None
        self.left_pans = False  # hand mode: the left button pans
        self._space = False  # Space held: the left button pans
        self._left_down = False
        self._drag_items: list = []
        self._ruler_items: list = []
        self._box_item: QGraphicsRectItem | None = None
        self._sketch_item: QGraphicsPathItem | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._has_content = False
        # A large scene rect lets the user pan freely beyond the geometry.
        self.scene().setSceneRect(QRectF(-1e6, -1e6, 2e6, 2e6))

    # -- content -----------------------------------------------------------

    def set_theme(self, name: str) -> None:
        """Switch between the ``light`` and ``dark`` canvas colours."""
        self.theme = THEMES[name]
        self.setBackgroundBrush(QColor(self.theme["background"]))
        self.viewport().update()

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
                pen = QPen(QColor(self.theme["highlight"]), 2)
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
            pen = QPen(QColor(self.theme["violation"]), 2)
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
        color, size = QColor(self.theme[style]), POINT_SIZES[style]
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

    def show_drag_preview(self, geometry: Geometry, colors: dict[str, QColor]) -> None:
        """Draw what is being dragged on top; move it with :meth:`move_drag_preview`."""
        self.clear_drag_preview()
        for layer, region in geometry.layers.items():
            color = colors.get(layer, QColor("#888888"))
            item = QGraphicsPathItem(region_to_path(region))
            fill = QColor(color)
            fill.setAlpha(150)
            pen = QPen(QColor(self.theme["highlight"]), 1.5)
            pen.setCosmetic(True)
            pen.setStyle(Qt.PenStyle.DashLine)
            item.setPen(pen)
            item.setBrush(QBrush(fill))
            item.setZValue(1050)
            self.scene().addItem(item)
            self._drag_items.append(item)

    def move_drag_preview(self, dx: float, dy: float) -> None:
        for item in self._drag_items:
            item.setTransform(QTransform())
            item.setPos(dx, dy)

    def rotate_drag_preview(self, angle: float, pivot: tuple[float, float]) -> None:
        """Show the preview rotated by ``angle`` degrees (counter-clockwise) about ``pivot``."""
        px, py = pivot
        transform = QTransform().translate(px, py).rotate(angle).translate(-px, -py)
        for item in self._drag_items:
            item.setPos(0, 0)
            item.setTransform(transform)

    def clear_drag_preview(self) -> None:
        for item in self._drag_items:
            self.scene().removeItem(item)
        self._drag_items.clear()

    def show_box(self, corners: tuple[float, float, float, float] | None) -> None:
        """The selection box being dragged out, or None to hide it."""
        if corners is None:
            if self._box_item is not None:
                self.scene().removeItem(self._box_item)
                self._box_item = None
            return
        x0, y0, x1, y1 = corners
        if self._box_item is None:
            self._box_item = QGraphicsRectItem()
            pen = QPen(QColor(self.theme["pick"]), 1)
            pen.setCosmetic(True)
            pen.setStyle(Qt.PenStyle.DashLine)
            self._box_item.setPen(pen)
            fill = QColor(self.theme["pick"])
            fill.setAlpha(30)
            self._box_item.setBrush(QBrush(fill))
            self._box_item.setZValue(1200)
            self.scene().addItem(self._box_item)
        self._box_item.setRect(QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized())

    def show_sketch(self, points: list[tuple[float, float]], closed: bool) -> None:
        """The outline of a shape being drawn (no points hides it)."""
        if self._sketch_item is not None:
            self.scene().removeItem(self._sketch_item)
            self._sketch_item = None
        if len(points) < 2:
            return
        path = QPainterPath(QPointF(*points[0]))
        for point in points[1:]:
            path.lineTo(QPointF(*point))
        if closed:
            path.closeSubpath()
        pen = QPen(QColor(self.theme["pick"]), 1.5)
        pen.setCosmetic(True)
        pen.setStyle(Qt.PenStyle.DashLine)
        self._sketch_item = QGraphicsPathItem(path)
        self._sketch_item.setPen(pen)
        self._sketch_item.setZValue(1100)
        self.scene().addItem(self._sketch_item)

    def show_rulers(self, rulers: list[tuple[float, float, float, float]]) -> None:
        """Measurement lines with their length, dx and dy."""
        for item in self._ruler_items:
            self.scene().removeItem(item)
        self._ruler_items.clear()
        color = QColor(self.theme["ruler"])
        for x0, y0, x1, y1 in rulers:
            pen = QPen(color, 1.5)
            pen.setCosmetic(True)
            line = self.scene().addLine(x0, y0, x1, y1, pen)
            line.setZValue(1150)
            length = math.hypot(x1 - x0, y1 - y0)
            text = QGraphicsSimpleTextItem(f"{length:.3f} µm  (dx {x1 - x0:.3f}, dy {y1 - y0:.3f})")
            text.setBrush(color)
            text.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            text.setPos((x0 + x1) / 2, (y0 + y1) / 2)
            text.setZValue(1150)
            self.scene().addItem(text)
            self._ruler_items += [line, text]

    def view_state(self) -> tuple[float, float, float]:
        """Zoom (pixels per µm) and the centre of the view, to restore it later."""
        center = self.mapToScene(self.viewport().rect().center())
        return self.pixels_per_um(), center.x(), center.y()

    def set_view_state(self, zoom: float, x: float, y: float) -> None:
        if 1e-4 < zoom < 1e5:
            self.setTransform(QTransform.fromScale(zoom, -zoom))
            self.centerOn(QPointF(x, y))
            self._has_content = True  # do not fit over it

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
        self.view_changed.emit()

    # -- interaction -------------------------------------------------------

    def wheelEvent(self, event) -> None:
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        scale = abs(self.transform().m11()) * factor
        if 1e-4 < scale < 1e5:
            self.scale(factor, factor)
            self.view_changed.emit()

    def _scene(self, event) -> QPointF:
        return self.mapToScene(event.position().toPoint())

    def _pans(self, event) -> bool:
        button = event.button()
        if button in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            return True
        return button == Qt.MouseButton.LeftButton and (self.left_pans or self._space)

    def mousePressEvent(self, event) -> None:
        self.setFocus()
        if self._pans(event):
            self._pan_from = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._left_down = True
            p = self._scene(event)
            self.pressed.emit(p.x(), p.y(), event.modifiers())
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not (self.left_pans or self._space):
            p = self._scene(event)
            self.double_clicked.emit(p.x(), p.y())
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._pan_from is not None:
            delta = event.position() - self._pan_from
            self._pan_from = event.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - delta.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - delta.y()))
            return
        p = self._scene(event)
        self.cursor_moved.emit(p.x(), p.y())
        left = bool(event.buttons() & Qt.MouseButton.LeftButton) and self._left_down
        self.moved.emit(p.x(), p.y(), event.modifiers(), left)

    def mouseReleaseEvent(self, event) -> None:
        if self._pan_from is not None:
            self._pan_from = None
            self._update_cursor()
            self.view_changed.emit()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._left_down:
            self._left_down = False
            p = self._scene(event)
            self.released.emit(p.x(), p.y(), event.modifiers())
            return
        super().mouseReleaseEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space = False
            self._update_cursor()
            return
        super().keyReleaseEvent(event)

    def set_tool_cursor(self, cursor: Qt.CursorShape) -> None:
        self._tool_cursor = cursor
        self._update_cursor()

    def _update_cursor(self) -> None:
        if self.left_pans or self._space:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(getattr(self, "_tool_cursor", Qt.CursorShape.ArrowCursor))

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_F:
            self.fit()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Backspace):
            self.key_pressed.emit(event.key())
            return
        if event.key() == Qt.Key.Key_Space:
            if not event.isAutoRepeat():
                self._space = True
                self._update_cursor()
            return
        steps = {
            Qt.Key.Key_Left: (-1, 0),
            Qt.Key.Key_Right: (1, 0),
            Qt.Key.Key_Up: (0, 1),
            Qt.Key.Key_Down: (0, -1),
        }.get(event.key())
        if steps is not None:
            fine = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.nudged.emit(*steps, fine)
            return
        super().keyPressEvent(event)

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        super().drawBackground(painter, rect)
        step = self.grid_step()
        minor, major, axis = (
            QPen(QColor(*self.theme["grid"], alpha), 0) for alpha in self.theme["grid_alpha"]
        )
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
