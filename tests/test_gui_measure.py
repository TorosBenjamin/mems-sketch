"""The Measure tool (#123): distances and angles as two modes of one tool, an angle
drawn with its arms and arc, snapping to placed components' corners and to edges,
and a dot where a click would snap."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGraphicsPathItem, QMessageBox

from mems_sketch.core.shapes import RectShape
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.tools import ANGLE, DISTANCE, SnapTargets

NONE = Qt.KeyboardModifier.NoModifier
CTRL = Qt.KeyboardModifier.ControlModifier


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    w.document.nodes.add(RectShape(name="bar", layer="device", x0=0, y0=0, x1=100, y1=20))
    return w


def test_distance_and_angle_are_modes_of_one_tool(window, qtbot):
    assert "angle" not in window.tools  # no separate angle tool
    window.actions_.measure_angle.trigger()  # N
    tool = window.tool
    assert tool.name == "measure" and tool.mode == ANGLE
    buttons = window.tool_status.mode_buttons
    assert buttons["angle"].isVisible() and buttons["angle"].isChecked()
    buttons["distance"].click()
    assert tool.mode == DISTANCE
    window.actions_.measure_angle.trigger()
    window.tool_actions["measure"].trigger()  # D: distances
    assert tool.mode == DISTANCE
    window.set_tool("select")
    assert not buttons["distance"].isVisible()  # only while measuring


def test_the_mode_is_remembered(window, qtbot):
    window.measure("angle")
    again = MainWindow()
    qtbot.addWidget(again)
    assert again.tools["measure"].mode == ANGLE


def test_switching_mode_drops_what_was_being_measured(window):
    window.measure("distance")
    tool = window.tool
    tool.press(0, 0, CTRL)
    assert tool.busy
    window.measure("angle")
    assert not tool.busy


def test_an_angle_is_drawn_with_its_arms_and_arc(window):
    window.measure("angle")
    tool = window.tool
    for x, y in ((0, 50), (40, 50), (30, 80)):
        tool.press(x, y, CTRL)
    (ruler,) = window.rulers["top"]
    assert ruler == pytest.approx((0, 50, 40, 50, 30, 80))
    paths = [i for i in window.canvas._ruler_items if isinstance(i, QGraphicsPathItem)]
    assert paths and paths[0].path().elementCount() > 3  # arms and an arc, not one line
    labels = [i.text() for i in window.canvas._ruler_items if hasattr(i, "text")]
    assert any(text.endswith("°") for text in labels)


def test_it_snaps_to_the_corners_of_placed_components(window):
    window.document.nodes.add_component("mems.anchor", 200, 0)
    window.measure("distance")
    tool = window.tool
    named = {(round(x, 3), round(y, 3)) for _, x, y in tool.targets().points}
    inner = [
        (float(x), float(y))
        for x, y in tool.targets().corners
        if x > 150 and (round(x, 3), round(y, 3)) not in named
    ]
    assert inner  # the anchor's opening: drawn by the placed component, not named
    cx, cy = inner[0]
    nudge = tool.reach / 3
    x, y, label = tool.locate(cx + nudge, cy - nudge, NONE)
    assert (x, y) == pytest.approx((cx, cy)) and label == "corner"


def test_it_snaps_along_an_edge_with_a_dot_that_follows(window):
    window.measure("distance")
    tool = window.tool
    near = tool.reach / 4
    tool.move(30.3, 20 + near, NONE, False)  # above the bar's top edge, away from its points
    kind, x, y = window.canvas.snap_marker
    assert kind == "edge" and (x, y) == pytest.approx((30.3, 20))
    tool.move(61.7, 20 - near, NONE, False)
    kind, x, y = window.canvas.snap_marker
    assert kind == "edge" and (x, y) == pytest.approx((61.7, 20))


def test_a_point_is_marked_with_a_ring_and_ctrl_snaps_nothing(window):
    window.measure("distance")
    tool = window.tool
    tool.move(100.2, 20.1, NONE, False)  # bar.top_right
    kind, x, y = window.canvas.snap_marker
    assert kind == "point" and (x, y) == pytest.approx((100, 20))
    tool.move(100.2, 20.1, CTRL, False)
    assert window.canvas.snap_marker is None


def test_named_points_come_before_corners_and_corners_before_edges():
    from mems_sketch.core.component import Geometry

    geometry = Geometry()
    geometry.add_rect("device", 0, 0, 10, 10)
    targets = SnapTargets([("p.mid", 5.0, 0.0)], geometry)
    assert targets.nearest(5.2, 0.1, 1)[2] == "p.mid"
    assert targets.nearest(0.2, 0.1, 1) == pytest.approx((0, 0, "corner"))
    assert targets.nearest(3, 0.3, 1)[:2] == pytest.approx((3, 0))
    assert targets.nearest(5, 5, 1) is None


def test_a_ruler_needs_a_length(window):
    """#253: two clicks on one point, or an angle's arms on one point, add nothing."""
    tool = window.tools["measure"]
    window.set_tool("measure")
    tool.set_mode(DISTANCE)
    before = len(window.rulers.get(window.document.active, []))
    for _ in range(2):
        tool.press(100, 20, Qt.KeyboardModifier.ControlModifier)
    assert len(window.rulers.get(window.document.active, [])) == before and tool.busy
