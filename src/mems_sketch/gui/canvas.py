"""The layout canvas: layers as filled outlines, selection highlight, rule markers,
alignment points, rulers and the viewport overlays (axis indicator, scale bar,
the caption with the view mode, the canvas modes, zoom buttons and the
move/rotate gizmos).

The canvas itself only zooms (wheel) and pans (middle drag, right drag, left
drag while Space is held or in hand mode). A right click without dragging asks
for the context menu. Left-button presses, moves and releases are passed on as
signals; the active tool (see :mod:`mems_sketch.gui.tools`) decides what they do.

Scene units are micrometres with y pointing up (the view flips Qt's y axis).
Wheel zooms around the cursor, F fits the view.
"""

from __future__ import annotations

import math

import klayout.db as kdb
from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QTransform,
)
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.core.component import DBU_UM, Geometry
from mems_sketch.gui import icons

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
        "grid_alpha": (16, 34, 90),
        "highlight": "#f07800",  # selection: orange, as in Blender and Unity
        "hover": "#f07800",
        "violation": "#d7002a",
        "declared": "#008a3e",
        "selected": "#f07800",
        "pick": "#0a6fd6",
        "snap": "#e0007a",
        "anchor": "#e0007a",
        "ruler": "#b35c00",
        "guide": "#1b8a96",
        "axis_x": "#e0443e",
        "axis_y": "#3f9b3f",
        "gizmo_free": "#6c707e",
        "gizmo_ring": "#3574f0",
        "overlay": "#1e1f22",  # overlay text
        "overlay_muted": "#818594",
    },
    "dark": {
        "background": "#1e1f22",
        "grid": (255, 255, 255),
        "grid_alpha": (12, 28, 70),
        "highlight": "#ffa033",
        "hover": "#ffa033",
        "violation": "#ff2d55",
        "declared": "#3ddc84",
        "selected": "#ffa033",
        "pick": "#00c8ff",
        "snap": "#ff5fb0",
        "anchor": "#ff5fb0",
        "ruler": "#ffb000",
        "guide": "#4cc2cf",
        "axis_x": "#f0584f",
        "axis_y": "#6cc36c",
        "gizmo_free": "#dfe1e5",
        "gizmo_ring": "#548af7",
        "overlay": "#dfe1e5",
        "overlay_muted": "#868a91",
    },
}
DEFAULT_THEME = "light"
# Canvas options (the window fills them from the settings; see gui/settings.py).
DEFAULT_OPTIONS = {
    "fill_opacity": 45,  # %
    "outline_width": 1.0,  # px
    "show_grid": True,
    "grid_spacing_px": 12,
    "show_axes": True,
    "show_axis_gizmo": True,
    "show_scale_bar": True,
    "gizmo_size_px": 70,
    "zoom_step": 1.25,
}
GIZMO_GRAB_PX = 7  # how close to a gizmo handle counts as on it
RIGHT_CLICK_SLOP_PX = 4  # a right press that moves further is a pan, not a click
MENU_CARET = "▾"  # after the text of a button that opens a menu
COMPONENT_MIME = "application/x-mems-sketch-component"  # a component dragged from the explorer
# The world the user can pan over, in µm: ±1 m, inside the ±2.1 m that 32-bit
# database units (nm) can hold. Cursor positions are kept inside it.
WORLD = QRectF(-1e6, -1e6, 2e6, 2e6)


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
    context_requested = Signal(float, float, QPoint)  # right click: µm, global position
    mode_chosen = Signal(str)  # a view mode picked in the caption
    component_dropped = Signal(str, float, float)  # a component dragged in: name, x, y (µm)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Wheel zoom keeps the point under the cursor itself (see wheelEvent):
        # AnchorUnderMouse relies on QGraphicsView's own mouse tracking, which
        # the mouse handlers here bypass.
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
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
        self._right_from: QPointF | None = None  # right press: a click, until it drags
        self.left_pans = False  # hand mode: the left button pans
        self._space = False  # Space held: the left button pans
        self._left_down = False
        self._drag_items: list = []
        self._ruler_items: list = []
        self._box_item: QGraphicsRectItem | None = None
        self._sketch_item: QGraphicsPathItem | None = None
        self._hover_item: QGraphicsPathItem | None = None
        self.options = dict(DEFAULT_OPTIONS)
        self._shown: tuple | None = None  # the last geometry shown, to redraw with new options
        self._caption: tuple[str, str] = ("", "")
        # The move/rotate gizmo: kind ("move" or "rotate"), centre in µm, the
        # part under the cursor or being dragged, and the drag's offset/rotation.
        self._gizmo: tuple[str, float, float] | None = None
        self._gizmo_hover: str | None = None
        self._gizmo_active: str | None = None
        self._gizmo_offset = (0.0, 0.0)
        self._gizmo_sweep: tuple[float, float] | None = None  # start angle, angle
        self._overlay_buttons = self._build_overlay_buttons()
        self.mode_palette = self._overlay_box(Qt.Orientation.Vertical)
        self._build_caption()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self._has_content = False
        self._wheel_anchor: QPointF | None = None  # scene point held under the cursor
        # A large scene rect lets the user pan freely beyond the geometry.
        self.scene().setSceneRect(WORLD)

    # -- content -----------------------------------------------------------

    def set_theme(self, name: str) -> None:
        """Switch between the ``light`` and ``dark`` canvas colours."""
        self.theme = THEMES[name]
        self.setBackgroundBrush(QColor(self.theme["background"]))
        self.viewport().update()

    def configure(self, **options) -> None:
        """Change drawing options (see ``DEFAULT_OPTIONS``) and redraw."""
        self.options.update(options)
        if self._shown is not None:
            self.show_geometry(*self._shown)
        self.viewport().update()

    def set_caption(self, title: str, subtitle: str = "") -> None:
        """The caption in the top-left corner: the component and details about it."""
        if (title, subtitle) != self._caption:
            self._caption = (title, subtitle)
            self.caption_title.setText(title)
            self.caption_details.setText(subtitle)
            self.caption_details.setVisible(bool(subtitle))
            self.caption.adjustSize()

    def set_view_modes(self, modes: dict[str, str], current: str) -> None:
        """The view modes offered by the caption's button (mode: label), and the current one."""
        self.mode_button.setText(f"{modes.get(current, current)} {MENU_CARET}")
        menu = self.mode_button.menu()
        menu.clear()
        for mode, label in modes.items():
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(mode == current)
            action.triggered.connect(lambda _=False, m=mode: self.mode_chosen.emit(m))
        self.caption.adjustSize()

    def set_mode_actions(self, actions: list[QAction]) -> None:
        """The canvas modes (select, move, ...) in the top-right corner, one button each."""
        layout = self.mode_palette.layout()
        while layout.count():
            layout.takeAt(0).widget().deleteLater()
        for action in actions:
            button = QToolButton(self.mode_palette)
            button.setDefaultAction(action)
            button.setIconSize(QSize(18, 18))
            button.setAutoRaise(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            layout.addWidget(button)
            button.show()  # the canvas may be visible already: count it in the size now
        self.mode_palette.adjustSize()
        self._place_overlays()

    def _overlay_box(self, orientation: Qt.Orientation) -> QWidget:
        """A floating group of buttons on the canvas."""
        box = QWidget(self)
        box.setObjectName("canvas-buttons")
        box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(box) if orientation == Qt.Orientation.Horizontal else QVBoxLayout(box)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(1)
        return box

    def _build_caption(self) -> None:
        """Top left: the component, the view mode (a menu) and details."""
        box = self.caption = self._overlay_box(Qt.Orientation.Horizontal)
        box.layout().setContentsMargins(8, 2, 8, 2)
        box.layout().setSpacing(6)
        self.caption_title = QLabel(box)
        self.caption_title.setObjectName("heading")
        self.mode_button = QToolButton(box)
        self.mode_button.setToolTip("What the canvas shows: the drawn layout or a process view")
        self.mode_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.mode_button.setAutoRaise(True)
        self.mode_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mode_button.setMenu(QMenu(self.mode_button))
        self.caption_details = QLabel(box)
        self.caption_details.setObjectName("muted")
        for widget in (self.caption_title, self.mode_button, self.caption_details):
            box.layout().addWidget(widget)
        box.move(8, 8)

    def _build_overlay_buttons(self) -> QWidget:
        """Zoom in, zoom out and fit, floating in the bottom-right corner."""
        box = self._overlay_box(Qt.Orientation.Horizontal)
        layout = box.layout()
        for name, tip, slot in (
            ("zoom_in", "Zoom in", lambda: self.zoom_by(self.options["zoom_step"])),
            ("zoom_out", "Zoom out", lambda: self.zoom_by(1 / self.options["zoom_step"])),
            ("fit", "Fit (F)", self.fit),
        ):
            button = QToolButton(box)
            icons.bind(button, name)
            button.setIconSize(QSize(16, 16))
            button.setToolTip(tip)
            button.setAutoRaise(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(slot)
            layout.addWidget(button)
        box.adjustSize()
        return box

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_overlays()

    def _place_overlays(self) -> None:
        width, height = self.viewport().width(), self.viewport().height()
        zoom, modes = self._overlay_buttons, self.mode_palette
        zoom.move(width - zoom.width() - 8, height - zoom.height() - 8)
        modes.move(width - modes.width() - 8, 8)

    def zoom_by(self, factor: float) -> None:
        """Zoom about the centre of the view."""
        scale = self.pixels_per_um() * factor
        if 1e-4 < scale < 1e5:
            anchor = self.transformationAnchor()
            self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
            self.scale(factor, factor)
            self.setTransformationAnchor(anchor)
            self.view_changed.emit()

    def show_geometry(
        self, geometry: Geometry, colors: dict[str, QColor], visible: dict[str, bool]
    ) -> None:
        self._shown = (geometry, colors, visible)
        for item in self._layer_items.values():
            self.scene().removeItem(item)
        self._layer_items.clear()
        alpha = round(255 * self.options["fill_opacity"] / 100)
        width = self.options["outline_width"]
        for z, (layer, region) in enumerate(sorted(geometry.layers.items())):
            color = colors.get(layer, QColor("#888888"))
            item = QGraphicsPathItem(region_to_path(region))
            fill = QColor(color)
            fill.setAlpha(alpha)
            pen = QPen(color, width)
            pen.setCosmetic(True)  # the same width in pixels at any zoom
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
                tint = QColor(self.theme["highlight"])
                tint.setAlpha(40)
                item.setBrush(QBrush(tint))
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
        if self._gizmo is not None:
            self._gizmo_offset = (dx, dy)
            self.viewport().update()

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
        if self._gizmo_offset != (0.0, 0.0):
            self._gizmo_offset = (0.0, 0.0)
            self.viewport().update()

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

    def show_hover(self, region: kdb.Region | None) -> None:
        """Outline the shape under the cursor (pre-selection), or nothing."""
        if self._hover_item is not None:
            self.scene().removeItem(self._hover_item)
            self._hover_item = None
        if region is None or region.is_empty():
            return
        pen = QPen(QColor(self.theme["hover"]), 1.2)
        pen.setCosmetic(True)
        pen.setStyle(Qt.PenStyle.DashLine)
        self._hover_item = QGraphicsPathItem(region_to_path(region))
        self._hover_item.setPen(pen)
        self._hover_item.setZValue(990)
        self.scene().addItem(self._hover_item)

    # -- gizmos --------------------------------------------------------------

    def set_gizmo(self, kind: str | None, center: tuple[float, float] | None = None) -> None:
        """Show the ``move`` or ``rotate`` gizmo at ``center`` (µm), or none."""
        gizmo = (kind, *center) if kind is not None and center is not None else None
        if gizmo != self._gizmo:
            self._gizmo = gizmo
            self._gizmo_hover = self._gizmo_active = None
            self._gizmo_offset, self._gizmo_sweep = (0.0, 0.0), None
            self.viewport().update()

    @property
    def gizmo(self) -> tuple[str, float, float] | None:
        return self._gizmo

    def gizmo_hit(self, x: float, y: float) -> str | None:
        """The gizmo part at ``(x, y)`` µm: ``x``, ``y``, ``free`` or ``ring``, else None."""
        if self._gizmo is None:
            return None
        p = self.mapFromScene(QPointF(x, y))
        return self._gizmo_part(p.x(), p.y())

    def _gizmo_screen(self) -> tuple[float, float]:
        _, gx, gy = self._gizmo
        c = self.mapFromScene(QPointF(gx + self._gizmo_offset[0], gy + self._gizmo_offset[1]))
        return c.x(), c.y()

    def _gizmo_part(self, px: float, py: float) -> str | None:
        kind = self._gizmo[0]
        cx, cy = self._gizmo_screen()
        size, grab = self.options["gizmo_size_px"], GIZMO_GRAB_PX
        dx, dy = px - cx, py - cy
        if kind == "rotate":
            return "ring" if abs(math.hypot(dx, dy) - size * 0.8) <= grab else None
        if math.hypot(dx, dy) <= 9:
            return "free"
        if abs(dy) <= grab and 12 <= dx <= size + 4:
            return "x"
        if abs(dx) <= grab and 12 <= -dy <= size + 4:
            return "y"
        return None

    def set_gizmo_active(self, part: str | None) -> None:
        """The part being dragged (drawn highlighted), or None when the drag ends."""
        self._gizmo_active = part
        if part is None:
            self._gizmo_offset, self._gizmo_sweep = (0.0, 0.0), None
        self.viewport().update()

    def set_gizmo_sweep(self, start: float, angle: float) -> None:
        """While rotating with the ring: the angle swept from ``start`` (degrees)."""
        self._gizmo_sweep = (start, angle)
        self.viewport().update()

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

    def show_guides(self, guides: list, selected: set) -> None:
        """Guide lines, dashed, with their names; those in ``selected`` stand out.

        ``guides`` are ``(path, name, start, end)`` in µm.
        """
        for item in getattr(self, "_guide_items", []):
            self.scene().removeItem(item)
        self._guide_items = []
        for path, name, (x0, y0), (x1, y1) in guides:
            chosen = path in selected
            color = QColor(self.theme["highlight" if chosen else "guide"])
            pen = QPen(color, 2.0 if chosen else 1.3)
            pen.setCosmetic(True)
            pen.setStyle(Qt.PenStyle.DashLine)
            line = self.scene().addLine(x0, y0, x1, y1, pen)
            line.setZValue(1040)
            for x, y in ((x0, y0), (x1, y1)):
                end = _PointMarker(color, 6)
                end.setPos(x, y)
                self.scene().addItem(end)
                self._guide_items.append(end)
            label = QGraphicsSimpleTextItem(name)
            label.setBrush(color)
            label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            label.setPos(x1, y1)
            label.setZValue(1040)
            self.scene().addItem(label)
            self._guide_items += [line, label]

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
        step = self.options["zoom_step"]
        factor = step if event.angleDelta().y() > 0 else 1 / step
        scale = abs(self.transform().m11()) * factor
        if 1e-4 < scale < 1e5:
            self._zoom_about(event.position(), factor)
            self.view_changed.emit()

    def _zoom_about(self, pos: QPointF, factor: float) -> None:
        """Zoom keeping the scene point under ``pos`` (viewport pixels) in place."""
        # Scrolling is in whole pixels; reusing the anchor of the previous step
        # (while it is still under the cursor) keeps that rounding from adding up.
        anchor = self._wheel_anchor
        if anchor is None or (self.viewportTransform().map(anchor) - pos).manhattanLength() > 1:
            anchor = self.viewportTransform().inverted()[0].map(pos)
        self._wheel_anchor = anchor
        self.scale(factor, factor)
        shift = self.viewportTransform().map(anchor) - pos
        for bar, delta in (
            (self.horizontalScrollBar(), shift.x()),
            (self.verticalScrollBar(), shift.y()),
        ):
            bar.setValue(bar.value() + round(delta))

    def _scene(self, event) -> QPointF:
        """The cursor in µm, kept inside ``WORLD`` (zoomed far out, the view shows more)."""
        p = self.mapToScene(event.position().toPoint())
        return QPointF(
            min(max(p.x(), WORLD.left()), WORLD.right()),
            min(max(p.y(), WORLD.top()), WORLD.bottom()),
        )

    def _pans(self, event) -> bool:
        button = event.button()
        if button == Qt.MouseButton.MiddleButton:
            return True
        return button == Qt.MouseButton.LeftButton and (self.left_pans or self._space)

    def mousePressEvent(self, event) -> None:
        self.setFocus()
        if event.button() == Qt.MouseButton.RightButton:
            self._right_from = event.position()  # a click opens the menu; a drag pans
            return
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
        if self._right_from is not None:
            if (event.position() - self._right_from).manhattanLength() <= RIGHT_CLICK_SLOP_PX:
                return
            self._pan_from, self._right_from = self._right_from, None  # it is a drag: pan
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        if self._pan_from is not None:
            delta = event.position() - self._pan_from
            self._pan_from = event.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - delta.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - delta.y()))
            return
        p = self._scene(event)
        if self._gizmo is not None and not self._left_down:
            part = self._gizmo_part(event.position().x(), event.position().y())
            if part != self._gizmo_hover:
                self._gizmo_hover = part
                self._update_cursor()
                self.viewport().update()
        self.cursor_moved.emit(p.x(), p.y())
        left = bool(event.buttons() & Qt.MouseButton.LeftButton) and self._left_down
        self.moved.emit(p.x(), p.y(), event.modifiers(), left)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton and self._right_from is not None:
            self._right_from = None
            p = self._scene(event)
            self.context_requested.emit(p.x(), p.y(), event.globalPosition().toPoint())
            return
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

    def contextMenuEvent(self, event) -> None:
        """The menu opens on releasing the right button (see mouseReleaseEvent); the
        keyboard's menu key opens it at the centre of the view."""
        if event.reason() == event.Reason.Keyboard:
            center = self.viewport().rect().center()
            p = self.mapToScene(center)
            self.context_requested.emit(p.x(), p.y(), self.viewport().mapToGlobal(center))
        event.accept()

    # -- dropping components from the explorer -------------------------------

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(COMPONENT_MIME):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(COMPONENT_MIME):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        data = event.mimeData()
        if not data.hasFormat(COMPONENT_MIME):
            super().dropEvent(event)
            return
        name = bytes(data.data(COMPONENT_MIME)).decode()
        p = self.mapToScene(event.position().toPoint())
        step = self.grid_step()
        event.acceptProposedAction()
        self.component_dropped.emit(name, round(p.x() / step) * step, round(p.y() / step) * step)

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
        part = self._gizmo_hover if self._gizmo is not None else None
        if self.left_pans or self._space:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        elif part is not None:
            self.setCursor(
                {
                    "x": Qt.CursorShape.SizeHorCursor,
                    "y": Qt.CursorShape.SizeVerCursor,
                    "free": Qt.CursorShape.SizeAllCursor,
                }.get(part, Qt.CursorShape.PointingHandCursor)
            )
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
        if self.options["show_grid"] and (right - left) * (bottom - top) <= 400_000:
            for i in range(left, right + 1):
                painter.setPen(axis if i == 0 else major if i % 5 == 0 else minor)
                painter.drawLine(QPointF(i * step, rect.top()), QPointF(i * step, rect.bottom()))
            for j in range(top, bottom + 1):
                painter.setPen(axis if j == 0 else major if j % 5 == 0 else minor)
                painter.drawLine(QPointF(rect.left(), j * step), QPointF(rect.right(), j * step))
        if self.options["show_axes"]:
            for key, line in (
                ("axis_x", (QPointF(rect.left(), 0), QPointF(rect.right(), 0))),
                ("axis_y", (QPointF(0, rect.top()), QPointF(0, rect.bottom()))),
            ):
                color = QColor(self.theme[key])
                color.setAlpha(150)
                pen = QPen(color, 1.5)
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.drawLine(*line)

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        """The overlays, drawn in pixels on top of everything."""
        painter.save()
        painter.resetTransform()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        height = self.viewport().height()
        if self.options["show_axis_gizmo"]:
            self._draw_axes(painter, 30, height - 30)
        if self.options["show_scale_bar"]:
            self._draw_scale_bar(painter, 70 if self.options["show_axis_gizmo"] else 16, height)
        if self._gizmo is not None:
            self._draw_gizmo(painter)
        painter.restore()

    def _draw_axes(self, painter: QPainter, x: float, y: float) -> None:
        """The axis indicator: x to the right in red, y up in green."""
        length = 22
        font = QFont(self.font())
        font.setBold(True)
        font.setPixelSize(10)
        painter.setFont(font)
        for key, (dx, dy), label in (("axis_x", (1, 0), "x"), ("axis_y", (0, -1), "y")):
            color = QColor(self.theme[key])
            painter.setPen(_round_pen(color, 2))
            tip = QPointF(x + dx * length, y + dy * length)
            painter.drawLine(QPointF(x, y), tip)
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(tip + QPointF(dx * 6, dy * 6), 6.5, 6.5)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(
                QRectF(tip.x() + dx * 6 - 6.5, tip.y() + dy * 6 - 6.5, 13, 13),
                Qt.AlignmentFlag.AlignCenter,
                label,
            )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.theme["overlay_muted"]))
        painter.drawEllipse(QPointF(x, y), 2.5, 2.5)

    def _draw_scale_bar(self, painter: QPainter, x: float, height: float) -> None:
        pixels = self.pixels_per_um()
        length = _nice(90 / pixels)
        width = length * pixels
        y = height - 18
        color = QColor(self.theme["overlay"])
        painter.setPen(QPen(color, 1.5))
        painter.drawLine(QPointF(x, y), QPointF(x + width, y))
        painter.drawLine(QPointF(x, y - 4), QPointF(x, y + 4))
        painter.drawLine(QPointF(x + width, y - 4), QPointF(x + width, y + 4))
        font = QFont(self.font())
        font.setPixelSize(11)
        painter.setFont(font)
        painter.drawText(
            QRectF(x, y - 18, width, 14), Qt.AlignmentFlag.AlignCenter, _length_text(length)
        )

    def _draw_gizmo(self, painter: QPainter) -> None:
        kind = self._gizmo[0]
        cx, cy = self._gizmo_screen()
        size = self.options["gizmo_size_px"]
        focus = self._gizmo_active or self._gizmo_hover

        def pen(key: str, part: str, width: float = 2.5) -> QPen:
            color = QColor(self.theme[key])
            if focus is not None and focus != part:
                color.setAlpha(110)
            wide = width + (1.5 if focus == part else 0)
            return _round_pen(color, wide)

        if kind == "rotate":
            radius = size * 0.8
            ring = pen("gizmo_ring", "ring", 3)
            halo = QColor(self.theme["background"])
            halo.setAlpha(200)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(_round_pen(halo, ring.widthF() + 3))
            painter.drawEllipse(QPointF(cx, cy), radius, radius)
            painter.setPen(ring)
            painter.drawEllipse(QPointF(cx, cy), radius, radius)
            if self._gizmo_sweep is not None:
                start, angle = self._gizmo_sweep
                color = QColor(self.theme["gizmo_ring"])
                fill = QColor(color)
                fill.setAlpha(90)
                painter.setBrush(fill)
                painter.setPen(Qt.PenStyle.NoPen)
                box = QRectF(cx - radius, cy - radius, 2 * radius, 2 * radius)
                painter.drawPie(box, round(start * 16), round(angle * 16))
                painter.setPen(_round_pen(color, 2))
                for a in (start, start + angle):  # screen y points down
                    end = QPointF(
                        cx + radius * math.cos(math.radians(a)),
                        cy - radius * math.sin(math.radians(a)),
                    )
                    painter.drawLine(QPointF(cx, cy), end)
                self._draw_chip(painter, QPointF(cx + radius + 10, cy - radius), f"{angle:+g}°")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self.theme["gizmo_ring"]))
            painter.drawEllipse(QPointF(cx, cy), 3, 3)
            return
        halo = QColor(self.theme["background"])
        halo.setAlpha(200)
        for key, part, (dx, dy) in (("axis_x", "x", (1, 0)), ("axis_y", "y", (0, -1))):
            p = pen(key, part, 3)
            start = QPointF(cx + dx * 12, cy + dy * 12)
            tip = QPointF(cx + dx * size, cy + dy * size)
            nx, ny = -dy, dx  # normal
            head = QPolygonF(
                [
                    tip + QPointF(dx * 13, dy * 13),
                    tip + QPointF(nx * 6.5, ny * 6.5),
                    tip - QPointF(nx * 6.5, ny * 6.5),
                ]
            )
            # a halo in the background colour keeps the arrow visible on any drawing
            painter.setPen(_round_pen(halo, p.widthF() + 3))
            painter.setBrush(halo)
            painter.drawLine(start, tip)
            painter.drawPolygon(head)
            painter.setPen(p)
            painter.drawLine(start, tip)
            painter.setBrush(p.color())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPolygon(head)
        free = pen("gizmo_free", "free", 2)
        painter.setPen(free)
        fill = QColor(self.theme["background"])
        fill.setAlpha(160)
        painter.setBrush(fill)
        painter.drawEllipse(QPointF(cx, cy), 7, 7)

    def _draw_chip(self, painter: QPainter, at: QPointF, text: str) -> None:
        """A small label on a rounded background, readable on any drawing."""
        font = QFont(self.font())
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        box = QRectF(
            at.x(),
            at.y() - metrics.height(),
            metrics.horizontalAdvance(text) + 12,
            metrics.height() + 6,
        )
        background = QColor(self.theme["background"])
        background.setAlpha(220)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(QColor(self.theme["overlay"]))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def grid_step(self) -> float:
        """Grid spacing in µm: the smallest 1-2-5 step with lines at least
        ``grid_spacing_px`` apart."""
        pixels_per_um = abs(self.transform().m11())
        target = self.options["grid_spacing_px"] / pixels_per_um
        exponent = math.floor(math.log10(target))
        return next(m * 10**exponent for m in (1, 2, 5, 10) if m * 10**exponent >= target)


def _round_pen(color: QColor, width: float) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    return pen


def _nice(value: float) -> float:
    """The largest 1-2-5 number not above ``value``."""
    exponent = math.floor(math.log10(value))
    return max(m * 10**exponent for m in (1, 2, 5) if m * 10**exponent <= value)


def _length_text(um: float) -> str:
    if um >= 1000:
        return f"{um / 1000:g} mm"
    if um < 1:
        return f"{um * 1000:g} nm"
    return f"{um:g} µm"


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
