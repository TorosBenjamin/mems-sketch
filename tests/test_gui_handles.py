"""The Select tool drags the selected shape's handles (#222)."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QMessageBox
from tests.test_gui_tools import drag, hover, mouse, two_rects

from mems_sketch.gui.app import MainWindow

BASE = ((0, 0),)


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    return w


def select_base(window):
    two_rects(window)
    window.select_click(BASE, False)


def handle(window, key):
    return next((x, y) for h, x, y in window.edit_handles if h.key == key)


def test_the_selected_shape_shows_its_handles(window):
    select_base(window)
    assert len(window.canvas.edit_handles) == 8  # 4 sides, 4 corners
    assert handle(window, "x1") == (100, 10)
    window.select_click(((0, 1),), True)  # two shapes: none
    assert window.canvas.edit_handles == []
    window.select_click(BASE, False)
    window.set_tool("move")  # only the Select tool drags them
    assert window.canvas.edit_handles == []


def test_dragging_a_side_resizes_the_shape(window):
    select_base(window)
    drag(window, (100, 10), (120, 10))
    base = window.document.node(BASE)
    assert (base.x0, base.x1) == (0, 120)
    assert handle(window, "x1") == (120, 10)  # the handles follow
    window.document.undo()
    assert window.document.node(BASE).x1 == 100


def test_dragging_a_corner_changes_both_sides(window):
    select_base(window)
    drag(window, (100, 20), (110, 40))
    base = window.document.node(BASE)
    assert (base.x1, base.y1) == (110, 40)


def test_esc_drops_a_handle_being_dragged(window):
    select_base(window)
    canvas = window.canvas
    mouse(canvas, QEvent.Type.MouseButtonPress, 100, 10)
    mouse(canvas, QEvent.Type.MouseMove, 130, 10)
    assert window.tool.busy and canvas.cursor_label.startswith("x1  100 → 130")
    window.escape()
    mouse(canvas, QEvent.Type.MouseButtonRelease, 130, 10)
    assert window.document.node(BASE).x1 == 100
    assert window.tool.name == "select" and not window.tool.busy


def test_the_cursor_shows_which_way_a_handle_drags(window, qtbot):
    select_base(window)
    viewport = window.canvas.viewport()

    def over(x, y):
        hover(window, x, y)
        qtbot.wait(40)  # (moves reach the tool at most once a frame)
        return viewport.cursor().shape()

    assert over(100, 10) == Qt.CursorShape.SizeHorCursor
    assert over(50, 20) == Qt.CursorShape.SizeVerCursor
    assert over(100, 20) == Qt.CursorShape.SizeBDiagCursor
    assert over(50, 10) == Qt.CursorShape.SizeAllCursor  # inside: moving it, as before


def test_dragging_inside_still_moves_the_shape(window):
    select_base(window)
    drag(window, (50, 10), (60, 10))
    base = window.document.node(BASE)
    assert (base.x0, base.x1) == (10, 110)
