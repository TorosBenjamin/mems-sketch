"""Rounding corners on the canvas (the Corners tool) and in Properties."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QMessageBox, QToolButton

from mems_sketch.core.shapes import CornersModifier, RectShape
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.value_edit import ValueEdit

NONE = Qt.KeyboardModifier.NoModifier


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    w.document.nodes.add(RectShape(name="plate", layer="device", x0=0, y0=0, x1=40, y1=20))
    w.canvas.set_view_state(10, 20, 10)  # 10 px per µm
    w.tree.select_paths([((0, 0),)])
    return w


def corners(window):
    node = window.document.node(((0, 0),))
    return [c for m in node.modifiers if isinstance(m, CornersModifier) for c in m.corners]


def click(tool, x, y):
    tool.press(x, y, NONE)
    tool.release(x, y, NONE)


def test_the_tool_marks_the_corners_and_rounds_a_clicked_one(window):
    window.set_tool("corners")
    tool = window.tool
    assert len(tool.markers()["pick"]) == 4
    click(tool, 40.05, 19.9)  # near enough
    assert [c.at for c in corners(window)] == ["self.top_right"]
    assert len(tool.markers()["pick"]) == 3 and len(tool.markers()["anchor"]) == 1
    click(tool, 40, 20)  # again: sharp
    assert corners(window) == []


def test_dragging_from_a_corner_sets_its_radius_in_one_step(window):
    window.set_tool("corners")
    tool = window.tool
    steps = len(window.document._undo)
    tool.press(0, 0, NONE)
    for d in (0.5, 1.5, 3.0):
        tool.move(d, 0, NONE, True)
    assert corners(window) == []  # only previewed so far
    tool.release(3, 0, NONE)
    assert [c.radius for c in corners(window)] == [3.0]
    assert len(window.document._undo) == steps + 1
    click(tool, 40, 0)  # the next corner gets the last radius
    assert [c.radius for c in corners(window)] == [3.0, 3.0]


def test_the_properties_card_edits_and_removes_corners(window):
    window.document.corners.add(((0, 0),), 40, 20, radius=1)
    window.tree.select_paths([((0, 0),)])
    card = window.properties
    radius = next(e for e in card.findChildren(ValueEdit) if e.prefix == "r")
    radius.setText("2")
    radius.returnPressed.emit()
    assert corners(window)[0].radius == 2
    style = next(c for c in card.findChildren(QComboBox) if c.findText("chamfer") >= 0)
    style.setCurrentText("chamfer")
    style.activated.emit(1)
    assert corners(window)[0].style == "chamfer"
    pick = next(b for b in card.findChildren(QToolButton) if b.text() == "Pick on the canvas")
    pick.click()
    assert window.tool.name == "corners"
    remove = next(
        b for b in card.findChildren(QToolButton) if b.toolTip() == "Make this corner sharp again"
    )
    remove.click()
    assert corners(window) == []


def test_a_placed_part_can_be_rounded_where_it_is_placed(window):
    path = window.document.nodes.add_component("anchor", 100, 0)
    window.tree.select_paths([path])
    window.set_tool("corners")
    tool = window.tool
    (x, y) = tool.candidates[0]
    click(tool, x, y)
    node = window.document.node(path)
    assert isinstance(node.modifiers[0], CornersModifier)  # on the placement: the part is as it was
