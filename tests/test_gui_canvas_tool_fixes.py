"""Canvas tool fixes (#255): no float noise when placing, snapping that follows
the settings, Add › Arc where it was asked for, the route preview, and more."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from mems_sketch import ComponentDef, PortDef, RefShape
from mems_sketch.core.shapes import ArcShape, CircleShape, GuideShape, RectShape
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.tools import _manhattan_sketch

NONE = Qt.KeyboardModifier.NoModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    pad = ComponentDef(
        name="pad",
        shapes=[RectShape(name="body", layer="device", x0=-10, y0=-10, x1=10, y1=10)],
        ports=[PortDef(name="signal", at="body.right", direction="right", width=4)],
    )
    w.document.edit("Add pad", lambda p: p.components.__setitem__("pad", pad))
    w.canvas.fit()
    return w


@pytest.fixture
def fine_grid(window, monkeypatch):
    monkeypatch.setattr(window.canvas, "grid_step", lambda: 0.2)


def placed(window) -> list[RefShape]:
    return [s for s in window.document.active_definition.shapes if isinstance(s, RefShape)]


def errors(window, monkeypatch) -> list[str]:
    said: list[str] = []
    monkeypatch.setattr(window, "report_error", said.append)
    return said


def test_placing_on_a_fine_grid_writes_round_numbers(window, fine_grid):
    window.start_placing("pad")
    window.tool.press(0.61, 0.69, NONE)  # 3 × 0.2 is 0.6000000000000001
    [ref] = placed(window)
    assert (repr(ref.x), repr(ref.y)) == ("0.6", "0.6")


def test_right_click_place_lands_on_the_grid(window, fine_grid):
    popup = window.place_popup(at=(0.61, 1.33))
    popup.chosen.emit("pad")
    [ref] = placed(window)
    assert (repr(ref.x), repr(ref.y)) == ("0.6", "1.4")


def test_right_click_place_with_grid_snapping_off_keeps_the_point(window):
    window.settings.set("snapping/grid", False)
    window.place_popup(at=(0.61, 1.33)).chosen.emit("pad")
    [ref] = placed(window)
    assert (ref.x, ref.y) == (0.61, 1.33)


def test_add_arc_from_the_right_click_menu_is_where_and_on_the_layer_asked(window, monkeypatch):
    monkeypatch.setattr(window.canvas, "grid_step", lambda: 1.0)
    window.document.project.layers["metal"] = window.document.project.layers["device"]
    window.draw_layer = "metal"
    window.add_primitive("arc", at=(10.3, 19.8))
    [arc] = [s for s in window.document.active_definition.shapes if isinstance(s, ArcShape)]
    assert (arc.x, arc.y, arc.layer) == (10, 20, "metal")


def test_circle_radius_is_not_put_on_the_grid_with_grid_snapping_off(window, monkeypatch):
    monkeypatch.setattr(window.canvas, "grid_step", lambda: 1.0)
    window.draw_layer = "device"
    tool = window.tools["circle"]
    assert tool.shape((0, 0), (3.3, 0), NONE).radius == 3  # on the grid
    window.settings.set("snapping/grid", False)
    assert tool.shape((0, 0), (3.3, 0), NONE).radius == pytest.approx(3.3)


def test_align_picks_points_with_point_snapping_off(window):
    window.settings.set("snapping/points", False)
    tool = window.tools["align"]
    tool.candidates = [("a.center", 5.0, 5.0)]
    assert tool._nearest(5.0 + tool.reach / 2, 5.0) == "a.center"


def test_the_manhattan_sketch_leaves_along_the_port_and_turns_at_each_point():
    assert _manhattan_sketch([(0, 0), (10, 5)], 0) == [(0, 0), (10, 0), (10, 5)]
    assert _manhattan_sketch([(0, 0), (10, 5)], 90) == [(0, 0), (0, 5), (10, 5)]
    assert _manhattan_sketch([(0, 0), (10, 0), (20, 5)], 0) == [
        (0, 0),
        (10, 0),
        (10, 5),
        (20, 5),
    ]


def test_hovering_the_end_port_previews_the_route_as_it_will_be_built(window):
    window.document.nodes.add(RefShape(name="a", component="pad"))  # port at (10, 0), right
    window.document.nodes.add(RefShape(name="b", component="pad", x=200, y=80, rotation=180))
    window.canvas.fit()
    window.set_tool("route")
    tool = window.tool
    tool.press(10, 0, NONE)
    tool.move(100, -40, NONE, False)  # free: a sketch along x and y
    assert window.canvas.ghost is None
    tool.move(190, 80, NONE, False)  # on b's port: the route itself
    assert window.canvas.ghost is not None
    tool.cancel()
    assert window.canvas.ghost is None


def test_with_shift_the_snap_marker_goes_when_the_point_leaves_it(window):
    window.document.nodes.add(RectShape(name="r", layer="device", x0=30, y0=10, x1=40, y1=20))
    tool = window.tools["polygon"]
    tool.placed = [(0.0, 0.0)]
    tool.locate(30.0, 10.0, NONE)
    assert tool._snap is not None  # on r's corner
    tool.locate(30.0, 10.0, SHIFT)  # put on the 0° line instead
    assert tool._snap is None


def test_a_guide_offers_its_own_points_once_and_no_box_points(window):
    window.document.nodes.add(GuideShape(name="guide1", x0=0, y0=0, x1=0, y1=100))
    names = [n for n, _, _ in window.document.results.all_points() if n.startswith("guide1.")]
    assert sorted(names) == ["guide1.center", "guide1.end", "guide1.start"]


@pytest.mark.parametrize("name", ["measure", "rect", "circle", "guide", "move", "rotate", "align"])
def test_double_click_does_not_open_a_component_mid_tool(window, name):
    assert window.tools[name].double_click(0, 0)  # used: the window opens nothing


def test_double_click_in_select_opens_a_component(window):
    assert not window.tools["select"].double_click(0, 0)


def test_corners_on_a_circle_says_it_has_no_corners_not_that_it_may_not_build(window, monkeypatch):
    path = window.document.nodes.add(CircleShape(name="c", layer="device", radius=10))
    said = errors(window, monkeypatch)
    window.set_tool("corners")
    window.tool.begin(path)
    assert said == ["c has no corners to round (it is all curves)"]
