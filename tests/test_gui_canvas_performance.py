"""The canvas stays fast on big designs: cached rendering, a light hover outline,
draft quality while zooming, and a frame-rate limit for mouse moves."""

import klayout.db as kdb
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPixmapCache, QWheelEvent
from PySide6.QtWidgets import QApplication, QGraphicsItem

from mems_sketch.core.component import Geometry
from mems_sketch.gui.canvas import LayoutCanvas

CACHED = QGraphicsItem.CacheMode.DeviceCoordinateCache


@pytest.fixture
def canvas(qtbot):
    c = LayoutCanvas()
    qtbot.addWidget(c)
    c.resize(800, 600)
    c.show()
    qtbot.waitExposed(c)
    return c


def plate_with_holes() -> kdb.Region:
    """A 100 µm square with a 10 × 10 grid of square holes."""
    region = kdb.Region(kdb.Box(0, 0, 100_000, 100_000))
    for i in range(10):
        for j in range(10):
            x, y = 5_000 + i * 10_000, 5_000 + j * 10_000
            region -= kdb.Region(kdb.Box(x, y, x + 2_000, y + 2_000))
    return region


def subpaths(path: QPainterPath) -> int:
    return sum(path.elementAt(i).isMoveTo() for i in range(path.elementCount()))


def test_the_geometry_and_overlays_are_drawn_from_a_cache(canvas):
    assert QPixmapCache.cacheLimit() >= 128 * 1024  # KB: room for a few full-view pictures
    geometry = Geometry()
    geometry.layers["device"] = plate_with_holes()
    canvas.show_geometry(geometry, {"device": QColor("#4c78a8")}, {})
    canvas.show_overlay(geometry, [])
    canvas.show_hover(geometry.layers["device"])
    canvas.show_drag_preview(geometry, {"device": QColor("#4c78a8")})
    items = [
        *canvas._layer_items.values(),
        *canvas._overlay,
        canvas._hover_item,
        *canvas._drag_items,
    ]
    assert items and all(item.cacheMode() == CACHED for item in items)


def test_the_hover_outline_is_the_outer_outline_only(canvas):
    canvas.show_hover(plate_with_holes())
    assert subpaths(canvas._hover_item.path()) == 1  # the 100 holes are left out


def wheel(canvas, up=True):
    pos = QPointF(canvas.viewport().rect().center())
    event = QWheelEvent(
        pos,
        canvas.viewport().mapToGlobal(pos),
        QPoint(0, 0),
        QPoint(0, 120 if up else -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(canvas.viewport(), event)


def antialiased(canvas) -> bool:
    return bool(canvas.renderHints() & QPainter.RenderHint.Antialiasing)


def test_zooming_draws_in_draft_quality_until_it_settles(canvas, qtbot):
    canvas.configure(draft_quality=True)
    qtbot.waitUntil(lambda: antialiased(canvas), timeout=2000)  # showing it was a resize
    wheel(canvas)
    assert not antialiased(canvas)
    wheel(canvas)
    assert not antialiased(canvas)
    qtbot.waitUntil(lambda: antialiased(canvas), timeout=2000)


def test_draft_quality_can_be_switched_off(canvas, qtbot):
    canvas.configure(draft_quality=False)
    wheel(canvas)
    assert antialiased(canvas)


def move(canvas, pos: QPointF, buttons=Qt.MouseButton.NoButton, kind=QEvent.Type.MouseMove):
    button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
    event = QMouseEvent(
        kind,
        pos,
        canvas.viewport().mapToGlobal(pos),
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(canvas.viewport(), event)


def test_the_frame_rate_limit_merges_fast_mouse_moves(canvas, qtbot):
    seen = []
    canvas.cursor_moved.connect(lambda x, y: seen.append((x, y)))
    canvas.configure(max_fps=20)  # at most one move every 50 ms
    for i in range(6):
        move(canvas, QPointF(100 + i * 10, 100))
    assert len(seen) == 1  # the first at once, the rest wait for the next frame
    last = canvas.mapToScene(QPoint(150, 100))
    qtbot.waitUntil(lambda: len(seen) == 2, timeout=1000)
    assert seen[-1] == pytest.approx((last.x(), last.y()))  # only the latest position


def test_without_a_limit_every_move_is_handled(canvas):
    seen = []
    canvas.cursor_moved.connect(lambda x, y: seen.append((x, y)))
    canvas.configure(max_fps=0)
    for i in range(6):
        move(canvas, QPointF(100 + i * 10, 100))
    assert len(seen) == 6


def test_a_press_handles_the_waiting_move_first(canvas):
    events = []
    canvas.moved.connect(lambda x, y, m, left: events.append(("moved", round(x, 3))))
    canvas.pressed.connect(lambda x, y, m: events.append(("pressed", round(x, 3))))
    canvas.configure(max_fps=20)
    move(canvas, QPointF(100, 100))
    move(canvas, QPointF(140, 100))  # waits for the next frame
    move(canvas, QPointF(140, 100), Qt.MouseButton.LeftButton, QEvent.Type.MouseButtonPress)
    x = round(canvas.mapToScene(QPoint(140, 100)).x(), 3)
    assert events[-2:] == [("moved", x), ("pressed", x)]


def test_the_frame_rate_and_draft_quality_are_settings(qapp):
    from mems_sketch.gui.settings import SETTINGS

    settings = {s.key: s for s in SETTINGS}
    assert settings["canvas/max_fps"].default == "display"
    assert {"display", "30", "60", "120", "144", "240", "0"} == set(
        settings["canvas/max_fps"].choices
    )
    assert settings["canvas/draft_quality"].default is True


def test_the_window_applies_the_frame_rate_setting(qtbot):
    from mems_sketch.gui.app import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    display = round(window.screen().refreshRate())
    assert window.canvas.options["max_fps"] == display  # the default follows the display
    window.settings.set("canvas/max_fps", "30")
    assert window.canvas.options["max_fps"] == 30
    window.settings.set("canvas/max_fps", "0")
    assert window.canvas.options["max_fps"] == 0
    window.settings.set("canvas/draft_quality", False)
    assert window.canvas.options["draft_quality"] is False
