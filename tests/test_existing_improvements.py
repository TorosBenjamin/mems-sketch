"""Improvements to what exists: a rectangle's rotation (#214), its corner radius as
a corners modifier (#234), the selected shapes' points (#239), the MCP render's
ports of placed parts (#212) and a snap step of the editor's own (#48)."""

import pytest

from mems_sketch import Layer, PathShape, PolygonShape, Project, RectShape, RefShape
from mems_sketch.editing import EditSession

BEAM = ((0, 0),)


def session_with(*shapes) -> EditSession:
    project = Project(name="t")
    project.add_layer(Layer("device", 1))
    for shape in shapes:
        project.add(shape)
    return EditSession(project)


def bbox(session: EditSession):
    return tuple(round(v, 6) for v in session.results.geometry().bbox())


# -- #214: a rotation of its own -------------------------------------------------------


def test_a_rectangle_turns_about_its_centre():
    session = session_with(
        RectShape(name="r", layer="device", x0=0, y0=0, x1=10, y1=4, rotation=90)
    )
    assert bbox(session) == (3, -3, 7, 7)
    points = {n: (x, y) for n, x, y in session.results.node_points(BEAM)}
    assert points["right"] == (7, 2) and points["top"] == (5, 7)  # of the turned box


def test_polygons_and_paths_turn_too():
    triangle = PolygonShape(
        name="t", layer="device", points=[(0, 0), (10, 0), (0, 10)], rotation=180
    )
    assert bbox(session_with(triangle)) == (0, 0, 10, 10)  # about its box's centre
    wire = PathShape(name="w", layer="device", points=[(0, 0), (10, 0)], width=2, rotation=90)
    session = session_with(wire)
    assert bbox(session) == (4, -5, 6, 5)
    ports = session.results.node_ports(BEAM)
    assert (round(ports["start"].x, 6), round(ports["start"].y, 6)) == (5, -5)
    assert ports["start"].angle == 270


def test_the_rotate_tool_sets_the_rectangles_rotation():
    session = session_with(RectShape(name="r", layer="device", x0=0, y0=0, x1=10, y1=4))
    session.moves.rotate([BEAM], 90, (0, 0))
    turned = session.shapes[0]
    assert turned.kind == "rect" and turned.rotation == 90  # not wrapped in a transform
    assert bbox(session) == (-4, 0, 0, 10)
    session.moves.rotate([BEAM], 30, (0, 0))
    assert session.shapes[0].rotation == 120


def test_a_rectangle_with_modifiers_is_still_wrapped():
    from mems_sketch import ArrayModifier

    rect = RectShape(
        name="r", layer="device", x0=0, y0=0, x1=10, y1=4, modifiers=[ArrayModifier(columns=2)]
    )
    session = session_with(rect)
    session.moves.rotate([BEAM], 90, (0, 0))
    assert session.shapes[0].kind == "transform"


def test_a_turned_rectangles_handles_turn_and_a_drag_keeps_the_rest_put():
    session = session_with(
        RectShape(name="r", layer="device", x0=0, y0=0, x1=10, y1=4, rotation=90)
    )
    handles = {h.key: (round(x, 6), round(y, 6)) for h, x, y in session.handles.handles(BEAM)}
    assert handles["x1"] == (5, 7) and handles["x0"] == (5, -3)  # the turned sides
    session.handles.drag(BEAM, "x1", 5, 17)
    assert bbox(session) == (3, -3, 7, 17)  # x0's side stayed


# -- #234: the corner radius is a corners modifier ---------------------------------------


def test_a_corner_radius_in_a_file_draws_as_before():
    from mems_sketch.storage.component_format import component_from_data

    data = {
        "shapes": {
            "body": {
                "kind": "rect",
                "layer": "device",
                "x0": 0,
                "y0": 0,
                "x1": 10,
                "y1": 4,
                "corner_radius": 1,
            },
        },
    }
    [body] = component_from_data("pad", data)[0].shapes
    assert "corner_radius" not in body.model_dump()
    assert body.modifiers[0].kind == "corners" and len(body.modifiers[0].corners) == 4


# -- #239: with shapes selected, only their points -----------------------------------------


@pytest.fixture
def window(qtbot, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QMessageBox

    from mems_sketch.gui.app import MainWindow

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    qtbot.waitExposed(w)
    return w


def markers(window, style):
    return window.canvas._points.get(style, [])


def test_the_selected_placements_points_only(window):
    nodes = window.document.nodes
    one = nodes.add(RefShape(name="c1", component="mems.comb_drive"))
    two = nodes.add(RefShape(name="c2", component="mems.comb_drive", x=300))
    window.settings.set("canvas/always_show_points", True)
    window.tree.select_paths([one])
    labels = {m.toolTip() for m in markers(window, "selected")}
    assert "fixed" in labels and markers(window, "declared") == []
    window.tree.select_paths([one, two])  # several: each named after its shape
    labels = {m.toolTip() for m in markers(window, "selected")}
    assert {"c1.fixed", "c2.fixed"} <= labels
    assert len(window.canvas._ports.get("selected", [])) == 2  # both placements' ports
    group = next(
        window.points.tree.topLevelItem(k)
        for k in range(window.points.tree.topLevelItemCount())
        if window.points.tree.topLevelItem(k).text(0) == "c2"
    )
    assert group.font(0).bold()


# -- #212: the MCP render draws the placed parts' ports ----------------------------------


def test_render_draws_the_ports_of_placed_parts():
    pytest.importorskip("mcp")
    from mems_sketch.core.user_component import ComponentDef
    from mems_sketch.mcp_server.server import _placed_ports
    from mems_sketch.mcp_server.tools import DesignTools

    project = Project(name="t")
    project.components["top"] = ComponentDef(name="top")
    project.top = "top"
    tools = DesignTools(EditSession(project))
    tools.session.nodes.add(RefShape(name="comb", component="mems.comb_drive"))
    name, built = tools.built(None, None)
    assert set(_placed_ports(tools, name, built, "all")) == {"comb.port.drive"}
    assert _placed_ports(tools, name, built, ["comb"]) == _placed_ports(tools, name, built, "all")
    with pytest.raises(ValueError, match="no shape named 'nope'"):
        _placed_ports(tools, name, built, ["nope"])


# -- #48: a snap step of the editor's own ---------------------------------------------------


def test_a_fixed_snap_step_apart_from_the_grid_drawn(window):
    canvas = window.canvas
    auto = canvas.grid_step()
    window.settings.set("snapping/grid_step", 0.25)
    assert canvas.snap_step() == 0.25
    assert canvas.on_grid(1.13, -0.4) == (1.25, -0.5)
    grid = canvas.grid_step()
    assert round(grid / 0.25, 9) == round(grid / 0.25)  # lines only on the step
    assert window.grid_label.text() == "snap 0.25 µm"
    window.settings.set("snapping/grid_step", 0.0)
    assert canvas.snap_step() == canvas.grid_step() == auto
    assert window.grid_label.text().startswith("grid ")
