"""Working on the canvas: the live value of a drag by the cursor, what a shape is
when pointed at, and the move cursor over the selection."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from mems_sketch.core.shapes import RectShape
from mems_sketch.gui.app import MainWindow

NONE = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    w.document.nodes.add(RectShape(name="bar", layer="device", x0=0, y0=0, x1=100, y1=20))
    w.canvas.zoom_to(w.canvas.content_rect())
    return w


def mouse(canvas, kind, x, y, buttons=LEFT):
    pos = QPointF(canvas.mapFromScene(QPointF(x, y)))
    if kind == QEvent.Type.MouseButtonRelease:
        buttons = Qt.MouseButton.NoButton
    event = QMouseEvent(kind, pos, canvas.viewport().mapToGlobal(pos), LEFT, buttons, NONE)
    QApplication.sendEvent(canvas.viewport(), event)


def hover(canvas, x, y):
    mouse(canvas, QEvent.Type.MouseMove, x, y, Qt.MouseButton.NoButton)


def test_a_drag_shows_its_offset_by_the_cursor_until_released(window):
    canvas = window.canvas
    mouse(canvas, QEvent.Type.MouseButtonPress, 50, 10)
    for x in (55, 60, 70):
        mouse(canvas, QEvent.Type.MouseMove, x, 10)
    assert canvas.cursor_label and canvas.cursor_label.startswith("Δx")
    assert "µm" in canvas.cursor_label and "(" not in canvas.cursor_label  # no help text
    mouse(canvas, QEvent.Type.MouseButtonRelease, 70, 10)
    assert canvas.cursor_label is None


def test_drawing_a_rectangle_shows_its_size_by_the_cursor(window):
    window.set_tool("rect")
    canvas = window.canvas
    mouse(canvas, QEvent.Type.MouseButtonPress, 200, 0)
    mouse(canvas, QEvent.Type.MouseButtonRelease, 200, 0)
    hover(canvas, 240, 30)
    assert canvas.cursor_label and "µm" in canvas.cursor_label
    window.tool.cancel()
    assert canvas.cursor_label is None


def test_drawing_a_polygon_shows_the_edge_being_drawn(window):
    window.set_tool("polygon")
    canvas = window.canvas
    mouse(canvas, QEvent.Type.MouseButtonPress, 200, 0)
    mouse(canvas, QEvent.Type.MouseButtonRelease, 200, 0)
    hover(canvas, 230, 0)
    assert canvas.cursor_label is not None and "∠" in canvas.cursor_label
    window.set_tool("select")  # another tool: the label goes
    assert canvas.cursor_label is None


def test_pointing_at_a_shape_says_what_it_is_after_a_moment(window, qtbot):
    canvas = window.canvas
    hover(canvas, 50, 10)
    assert canvas.hover_label is None  # not at once
    qtbot.waitUntil(lambda: canvas.hover_label is not None, timeout=2000)
    assert canvas.hover_label == "bar · rect · device"
    hover(canvas, 50, 60)  # off the shape
    qtbot.waitUntil(lambda: canvas.hover_label is None)


def test_a_placed_component_is_described_by_the_component_it_places(window):
    window.add_component("mems.anchor")
    path = window.selection[0]
    assert window.describe_node(path).endswith("· mems.anchor")


def test_the_move_cursor_shows_over_the_selection(window, qtbot):
    canvas = window.canvas
    window.tree.select_paths([min(window.document.results.inspection())])
    hover(canvas, 50, 10)
    qtbot.waitUntil(lambda: canvas.cursor().shape() == Qt.CursorShape.SizeAllCursor)
    hover(canvas, 50, 60)  # beside it (mouse moves are handled at the next frame)
    qtbot.waitUntil(lambda: canvas.cursor().shape() != Qt.CursorShape.SizeAllCursor)
