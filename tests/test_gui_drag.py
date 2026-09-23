import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from mems_sketch.core.shapes import Align, RectShape
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
    return w


def mouse(canvas, kind, x, y, modifiers=NONE):
    """Send a real mouse event at scene position (x, y) µm."""
    pos = QPointF(canvas.mapFromScene(QPointF(x, y)))
    buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else LEFT
    event = QMouseEvent(kind, pos, canvas.viewport().mapToGlobal(pos), LEFT, buttons, modifiers)
    QApplication.sendEvent(canvas.viewport(), event)


def drag(window, start, end, modifiers=NONE, steps=5):
    canvas = window.canvas
    mouse(canvas, QEvent.Type.MouseButtonPress, *start)
    for k in range(1, steps + 1):
        t = k / steps
        point = (start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t)
        mouse(canvas, QEvent.Type.MouseMove, *point, modifiers)
    mouse(canvas, QEvent.Type.MouseButtonRelease, *end, modifiers)


def rect(name, x0, y0, x1, y1, **kw):
    return RectShape(name=name, layer="device", x0=x0, y0=y0, x1=x1, y1=y1, **kw)


def setup_two(window):
    doc = window.document
    doc.nodes.add(rect("base", 0, 0, 100, 20))
    doc.nodes.add(rect("post", 150, 50, 160, 60))
    window.canvas.zoom_to(window.canvas.content_rect())


def test_dragging_moves_the_shape_snapped_to_the_grid(window):
    setup_two(window)
    step = window.canvas.grid_step()
    drag(window, (50, 10), (50 + 3.3 * step, 10 + 1.2 * step), Qt.KeyboardModifier.ControlModifier)
    base = window.document.node(((0, 0),))
    assert base.x0 == pytest.approx(3.3 * step, abs=step / 20)  # Ctrl: no snapping
    window.document.undo()
    drag(window, (50, 10), (50 + 3.3 * step, 10 + 1.2 * step))
    base = window.document.node(((0, 0),))
    assert (base.x0, base.y0) == pytest.approx((3 * step, 1 * step))
    assert window.document.undo_text().startswith("Move base")


def test_a_click_without_moving_does_not_move(window):
    setup_two(window)
    before = window.document.node(((0, 0),))
    mouse(window.canvas, QEvent.Type.MouseButtonPress, 50, 10)
    mouse(window.canvas, QEvent.Type.MouseButtonRelease, 50, 10)
    assert window.document.node(((0, 0),)) == before
    assert window.selection == [((0, 0),)]


def test_dragging_near_a_point_snaps_and_shift_aligns(window):
    setup_two(window)
    # Drag the post's bottom-left corner onto the base's top-right corner (100, 20).
    drag(window, (155, 55), (105.3, 25.2))
    post = window.document.node(((0, 1),))
    assert (post.x0, post.y0) == pytest.approx((100, 20))
    window.document.undo()
    drag(window, (155, 55), (105.3, 25.2), Qt.KeyboardModifier.ShiftModifier)
    post = window.document.node(((0, 1),))
    assert (post.align.point, post.align.to) == ("bottom_left", "base.top_right")


def test_dragging_a_shape_brings_what_is_aligned_to_it(window):
    setup_two(window)
    window.document.nodes.set_align(((0, 1),), Align(point="bottom", to="base.top"))
    drag(window, (20, 10), (20, -40), Qt.KeyboardModifier.ControlModifier)
    post = window.document.results.highlight([((0, 1),)]).layers["device"].bbox()
    assert post.bottom == pytest.approx(-30000, abs=200)


def test_drag_keeps_a_multiple_selection_and_esc_cancels(window):
    setup_two(window)
    window.tree.select_paths([((0, 0),), ((0, 1),)])
    canvas = window.canvas
    mouse(canvas, QEvent.Type.MouseButtonPress, 50, 10)
    assert len(window.selection) == 2  # pressing on the selection keeps it
    mouse(canvas, QEvent.Type.MouseMove, 60, 10)
    mouse(canvas, QEvent.Type.MouseMove, 80, 10)
    assert window.tool.busy
    window.cancel_align()  # Esc
    mouse(canvas, QEvent.Type.MouseButtonRelease, 80, 10)
    assert not window.document.can_undo() or window.document.undo_text().startswith("Add")
    drag(window, (50, 10), (70, 10), Qt.KeyboardModifier.ControlModifier)
    assert window.document.undo_text() in ("Move base, post", "Move post, base")


def test_arrow_keys_nudge_the_selection(window):
    setup_two(window)
    window.tree.select_paths([((0, 1),)])
    step = window.canvas.grid_step()
    key = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, NONE)
    QApplication.sendEvent(window.canvas, key)
    assert window.document.node(((0, 1),)).x0 == pytest.approx(150 + step)
    key = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.ShiftModifier)
    QApplication.sendEvent(window.canvas, key)
    assert window.document.node(((0, 1),)).y0 == pytest.approx(50 + step / 10)


def test_read_only_tabs_cannot_be_dragged(window, tmp_path):
    import shutil
    from pathlib import Path

    examples = Path(__file__).parent.parent / "examples"
    shutil.copytree(
        examples / "resonator",
        tmp_path / "resonator",
        ignore=shutil.ignore_patterns(".mems-sketch"),
    )
    shutil.copytree(
        examples / "libraries",
        tmp_path / "libraries",
        ignore=shutil.ignore_patterns(".mems-sketch"),
    )
    window.open_project(str(tmp_path / "resonator"))
    window.open_component("std.perforated_plate")
    window.canvas.fit()
    drag(window, (5, 5), (30, 5))
    assert "read-only" in window.statusBar().currentMessage()
    assert not window.document.can_undo()
