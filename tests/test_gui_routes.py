"""The Route tool (#182): click a port, any via points, then another port; drag a
selected route's via points to move them."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from mems_sketch import ComponentDef, PortDef, RefShape
from mems_sketch.core.shapes import RectShape, RouteShape
from mems_sketch.gui.app import MainWindow

NONE = Qt.KeyboardModifier.NoModifier


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
    w.document.nodes.add(RefShape(name="a", component="pad"))  # port at (10, 0), right
    w.document.nodes.add(RefShape(name="b", component="pad", x=200, y=80, rotation=180))
    w.canvas.fit()
    w.set_tool("route")
    return w


def routes(window):
    return [s for s in window.document.active_definition.shapes if isinstance(s, RouteShape)]


def test_click_a_port_a_via_point_and_another_port(window):
    tool = window.tool
    assert {"a.port.signal", "b.port.signal"} <= {m[0] for m in tool.markers()["pick"]}
    tool.press(10, 0, NONE)
    assert tool.start_port == "a.port.signal"
    assert "a.port.signal" not in {m[0] for m in tool.markers()["pick"]}  # not twice
    tool.press(100, -40, NONE)  # a via point
    tool.key(Qt.Key.Key_Backspace)  # taken back
    tool.press(100, -40, NONE)
    tool.press(190, 80, NONE)
    [made] = routes(window)
    assert (made.start, made.end, made.style) == ("a.port.signal", "b.port.signal", "manhattan")
    assert made.via == [(100, -40)]
    assert made.width == 0  # the ports'
    assert window.document.results.geometry().layers["device"].count() == 1
    assert not tool.busy  # ready for the next one


def test_drag_a_selected_routes_via_point(window):
    tool = window.tool
    for x, y in ((10, 0), (100, -40), (190, 80)):
        tool.press(x, y, NONE)
    [path] = window.selection  # the new route is selected
    assert [m[1:] for m in tool.markers()["anchor"]] == [(100, -40)]
    tool.press(100, -40, NONE)
    tool.move(100, 120, NONE, True)
    tool.release(100, 120, NONE)
    assert window.document.node(path).via == [(100, 120)]
    assert window.document.results.geometry().bbox()[3] == pytest.approx(122)  # up there now


def test_escape_cancels_a_route_being_drawn(window):
    tool = window.tool
    tool.press(10, 0, NONE)
    assert tool.cancel()
    assert not tool.busy and routes(window) == []


def test_properties_edit_its_ports_style_and_via_points(window):
    tool = window.tool
    for x, y in ((10, 0), (190, 80)):
        tool.press(x, y, NONE)
    [path] = window.selection
    editors = window.properties._editors
    assert editors["start"]() == "a.port.signal"
    assert editors["layer"]() is None  # on its ports' layer, not set
    editors["style"] = lambda: "straight"
    editors["via"] = lambda: [(100, 40)]
    window.properties.apply()
    made = window.document.node(path)
    assert (made.style, made.via, made.layer) == ("straight", [(100, 40)], None)


def test_a_start_port_undone_away_ends_the_route(window):
    tool = window.tool
    tool.press(190, 80, NONE)  # b's
    window.undo()  # b is gone
    tool.move(100, 0, NONE, False)
    assert not tool.busy
