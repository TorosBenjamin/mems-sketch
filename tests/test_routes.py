"""Routes (#182): a wire from port to port. It is drawn along x and y, with one
bend, or straight through via points; its bends are sharp, exact arcs or
chamfers; it follows the parts it joins when they move, renames follow into
it, and each way it cannot be drawn is an error that says why."""

import math

import pytest

from mems_sketch import ComponentDef, PortDef, RefShape
from mems_sketch.core.project import new_project
from mems_sketch.core.shapes import RectShape, RouteShape
from mems_sketch.core.shapes.modifiers import ArrayModifier
from mems_sketch.editing import EditSession
from mems_sketch.engine import Engine
from mems_sketch.storage.component_format import component_data, component_from_data

PADS = 2 * 20 * 20  # µm²


def pad(**port) -> ComponentDef:
    """A 20 µm pad with a port in the middle of its right edge, pointing right."""
    fields = {"name": "signal", "at": "body.right", "direction": "right", "width": 4}
    return ComponentDef(
        name="pad",
        shapes=[RectShape(name="body", layer="device", x0=-10, y0=-10, x1=10, y1=10)],
        ports=[PortDef(**{**fields, **port})],
    )


A = RefShape(name="a", component="pad")  # its port at (10, 0), pointing right
# Turned to face a: its port at (190, 80), pointing left.
B = RefShape(name="b", component="pad", x=200, y=80, rotation=180)
# Below and to the right, turned to point down at a's line: its port at (100, 50).
DOWN = RefShape(name="b", component="pad", x=100, y=60, rotation=270)


def route(**fields) -> RouteShape:
    return RouteShape(name="w", **{"start": "a.port.signal", "end": "b.port.signal", **fields})


def project(*shapes, component=None):
    p = new_project("t")
    p.components["pad"] = component or pad()
    p.components["top"].shapes = list(shapes)
    return p


def build(*shapes, component=None):
    return Engine().load(project(*shapes, component=component)).build("top")


def device(built):
    """The device layer: how many pieces, and the area in µm²."""
    region = built.geometry.layers["device"]
    return region.count(), region.area() / 1e6


def error(*shapes, component=None) -> str:
    with pytest.raises(ValueError) as caught:
        build(*shapes, component=component).geometry  # noqa: B018 - building it
    return str(caught.value)


def test_a_manhattan_route_runs_from_port_to_port():
    """A Z, bending halfway: 90 + 80 + 90 long, 4 wide (sharp 90° bends add and
    take away the same), touching both pads."""
    pieces, area = device(build(A, B, route()))
    assert pieces == 1
    assert area == pytest.approx(PADS + 260 * 4)


def test_round_bends_are_exact_arcs():
    """One bend of radius 10: the straight runs and a quarter of a ring (exact in
    the engine; here, as polygons, within the arc tolerance)."""
    pieces, area = device(build(A, DOWN, route(style="l", bend="round", radius=10)))
    straight = (90 - 10) + (50 - 10)
    quarter_ring = math.pi / 4 * ((10 + 2) ** 2 - (10 - 2) ** 2)
    assert pieces == 1
    assert area == pytest.approx(PADS + straight * 4 + quarter_ring, abs=0.05)


def test_a_chamfer_cuts_the_corner():
    _, sharp = device(build(A, DOWN, route(style="l")))
    _, chamfered = device(build(A, DOWN, route(style="l", bend="chamfer", radius=6)))
    assert chamfered < sharp


def test_the_routes_own_ports_point_back_into_what_it_joins():
    ports = build(A, B, route()).records()[((0, 2),)].ports
    assert tuple(ports["start"][:4]) == pytest.approx((10, 0, 180, 4))
    assert tuple(ports["end"][:4]) == pytest.approx((190, 80, 0, 4))
    assert ports["end"].layer == "device"


def test_it_follows_a_moved_part():
    doc = EditSession()
    doc.edit("Add pad", lambda p: p.components.__setitem__("pad", pad()))
    for shape in (A, B, route()):
        doc.nodes.add(shape)
    doc.nodes.replace(((0, 1),), B.model_copy(update={"y": 140}))
    end = doc.results.node_ports(((0, 2),))["end"]
    assert (end.x, end.y) == pytest.approx((190, 140))
    assert doc.results.geometry().layers["device"].count() == 1  # still joined


def test_through_via_points():
    """Straight lines through each: a dog-leg up to 40 and back down."""
    shapes = A, B.model_copy(update={"y": 0}), route(style="straight", via=[(50, 40), (150, 40)])
    pieces, area = device(build(*shapes))
    assert pieces == 1
    assert area > PADS + 180 * 4  # longer than straight across


def test_manhattan_via_points_are_passed_along_x_and_y():
    built = build(A, B, route(via=[(100, -40)]))
    y0 = built.records()[((0, 2),)].geometry.bbox()[1]
    assert y0 == pytest.approx(-42)  # down to the via point (and half the width)


def test_a_route_to_an_array_copy():
    row = RefShape(
        name="b",
        component="pad",
        x=200,
        y=80,
        rotation=180,
        modifiers=[ArrayModifier(rows=3, dy=40)],
    )
    end = build(A, row, route(end="b.port.signal[2]")).records()[((0, 2),)].ports["end"]
    assert (end.x, end.y) == pytest.approx((190, 160))


def test_width_and_layer_can_be_given():
    built = build(A, B, route(width=6, layer="metal"))
    assert built.records()[((0, 2),)].ports["start"].width == 6
    assert built.geometry.layers["metal"].area() / 1e6 == pytest.approx(260 * 6)


@pytest.mark.parametrize(
    "fields, component, message",
    [
        ({}, pad(width=None), "its ports have no width: give the route one"),
        ({"end": "nothing.port.signal"}, None, "there is no shape 'nothing'"),
        ({"end": "b.port.other"}, None, "other"),
        ({"end": "a.port.signal"}, None, "the same port 'a.port.signal'"),
        ({"bend": "round", "radius": 1}, None, "more than half the width (2 µm)"),
        ({"style": "l"}, None, "these are parallel: use manhattan"),
        ({"style": "l", "via": [(50, 50)]}, None, "one bend and no via points"),
        (
            {"style": "straight", "via": [(50, 0), (50, 10)], "bend": "round", "radius": 8},
            None,
            "do not fit between (50, 0) and (50, 10): the largest that fits is",
        ),
    ],
)
def test_what_it_cannot_do(fields, component, message):
    assert message in error(A, B, route(**fields), component=component)


def test_ports_of_different_widths_or_layers_need_one_given():
    wide = RefShape(name="b", component="wide", x=200, y=80, rotation=180)
    p = project(A, wide, route())
    p.components["wide"] = pad(width=8, layer="metal").model_copy(update={"name": "wide"})
    with pytest.raises(ValueError, match=r"different widths \(4, 8\): give the route a width"):
        Engine().load(p).build("top").geometry  # noqa: B018
    p.components["top"].shapes[2] = route(width=4)
    with pytest.raises(ValueError, match="different layers .*give the route a layer"):
        Engine().load(p).build("top").geometry  # noqa: B018
    p.components["top"].shapes[2] = route(width=4, layer="device")
    assert Engine().load(p).build("top").geometry.layers["device"].count() == 1


def test_a_manhattan_route_needs_ports_along_x_and_y():
    tilted = B.model_copy(update={"rotation": 150})
    assert "its end faces 150°: use straight or l" in error(A, tilted, route())
    assert device(build(A, tilted, route(style="straight")))[0] >= 1


def test_what_is_not_a_port():
    with pytest.raises(ValueError, match="is not a port"):
        route(end="b.right")


def test_renames_follow_into_it():
    p = project(A, B, route())
    p.components["top"].rename_shape("a", "source")
    p.rename_port("pad", "signal", "out")
    made = p.components["top"].shapes[2]
    assert (made.start, made.end) == ("source.port.out", "b.port.out")
    assert Engine().load(p).build("top").geometry.layers["device"].count() == 1


def test_moving_it_moves_its_via_points():
    doc = EditSession()
    doc.edit("Add pad", lambda p: p.components.__setitem__("pad", pad()))
    for shape in (A, B, route(via=[(100, -40)])):
        doc.nodes.add(shape)
    doc.moves.move([((0, 2),)], 10, -5)
    assert doc.node(((0, 2),)).via == [(110, -45)]


def test_a_route_round_trips_through_the_file_format():
    definition = ComponentDef(
        name="top", shapes=[A, B, route(bend="round", radius=10, via=[(100, "a.y - 40")])]
    )
    data = component_data(definition)
    assert data["shapes"]["w"] == {
        "kind": "route",
        "start": "a.port.signal",
        "end": "b.port.signal",
        "via": [[100.0, "a.y - 40"]],
        "bend": "round",
        "radius": 10.0,
    }
    assert component_from_data("top", data)[0] == definition
