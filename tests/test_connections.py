"""Connections (#192): the area that joins beam ends, made from their ports. They
join placed components and paths; each way an end can be wrong is an error that
names it (a port off its shape, facing the wrong way, on a rounded corner,
wider than its beam, ...); they follow renames and moves."""

import math

import pytest

from mems_sketch import ComponentDef, PortDef, RefShape
from mems_sketch.core.project import new_project
from mems_sketch.core.shapes import (
    CircleShape,
    ConnectionShape,
    PathShape,
    RectShape,
    TransformShape,
)
from mems_sketch.core.shapes.modifiers import ArrayModifier
from mems_sketch.editing import EditSession
from mems_sketch.engine import Engine
from mems_sketch.storage.component_format import component_data, component_from_data

REACH = 0.5  # how far a connection reaches into each beam (engine: REACH_UM)


def beam(port=None, corner_radius=0.0, layer="device") -> ComponentDef:
    """A beam 40 long and 10 wide along +x from the origin; its port at the
    origin end, pointing out of it (-x)."""
    fields = {"name": "tip", "at": "body.left", "direction": "left", "width": 10}
    return ComponentDef(
        name="beam",
        shapes=[
            RectShape(
                name="body", layer=layer, x0=0, y0=-5, x1=40, y1=5, corner_radius=corner_radius
            )
        ],
        ports=[PortDef(**{**fields, **(port or {})})],
    )


def around(count: int, distance: float = 20, start: float = 0) -> list[RefShape]:
    """Beams around the origin, each turned so its tip faces the centre."""
    shapes = []
    for k in range(count):
        a = start + 360 * k / count
        x, y = distance * math.cos(math.radians(a)), distance * math.sin(math.radians(a))
        shapes.append(RefShape(name=f"b{k}", component="beam", x=x, y=y, rotation=a))
    return shapes


def project(*shapes, component=None):
    p = new_project("t")
    p.components["beam"] = component or beam()
    p.components["top"].shapes = list(shapes)
    return p


def build(*shapes, component=None):
    return Engine().load(project(*shapes, component=component)).build("top")


def connection(ends, **fields):
    return ConnectionShape(name="hub", ends=ends, **fields)


class Device:
    """A build's device layer: how many pieces, and the area in µm²."""

    def __init__(self, region):
        self.region = region

    def polygons(self):
        return range(self.region.count())

    def area(self):
        return self.region.area() / 1e6  # nm² to µm²


def device(built):
    return Device(built.geometry.layers["device"])


def error(*shapes, component=None) -> str:
    with pytest.raises(ValueError) as caught:
        build(*shapes, component=component).geometry  # noqa: B018 - building it
    return str(caught.value)


T = [
    PathShape(name="left", layer="device", points=[(-60, 0), (-20, 0)], width=10),
    PathShape(name="right", layer="device", points=[(60, 0), (20, 0)], width=10),
    PathShape(name="up", layer="device", points=[(0, 60), (0, 20)], width=10),
]
T_ENDS = ["left.port.end", "right.port.end", "up.port.end"]


# -- what it joins -------------------------------------------------------------


def test_paths_have_ports_at_their_ends_pointing_out():
    ports = build(*T).records()[((0, 0),)].ports
    assert tuple(ports["start"][:4]) == pytest.approx((-60, 0, 180, 10))
    assert tuple(ports["end"][:4]) == pytest.approx((-20, 0, 0, 10))
    square = PathShape(name="p", layer="device", points=[(0, 0), (10, 0)], width=4, ends="square")
    assert build(square).records()[((0, 0),)].ports["end"].x == pytest.approx(
        12
    )  # past the extension


def test_a_t_of_paths_is_filled_in_and_rounded():
    sharp = device(build(*T, connection(T_ENDS)))
    assert len(sharp.polygons()) == 1
    beams = 3 * 40 * 10
    assert sharp.area() == pytest.approx(
        beams + 40 * 10 + 10 * 15
    )  # the bar and the stem between them
    rounded = device(build(*T, connection(T_ENDS, inner_radius=3)))
    # (arcs come out as chords within 5 nm: a few hundredths of a µm² short at most)
    assert rounded.area() == pytest.approx(sharp.area() + 2 * 9 * (1 - math.pi / 4), abs=0.1)


def test_placed_components_turned_around_a_centre():
    joined = device(
        build(*around(3), connection([f"b{k}.port.tip" for k in range(3)], inner_radius=4))
    )
    assert len(joined.polygons()) == 1


def test_a_mirrored_placement_joins_by_its_turned_port():
    shapes = around(2, start=0)
    shapes[1] = shapes[1].model_copy(update={"mirror_x": True})
    joined = device(build(*shapes, connection(["b0.port.tip", "b1.port.tip"])))
    assert len(joined.polygons()) == 1
    assert joined.area() == pytest.approx(2 * 40 * 10 + 40 * 10)  # a straight bridge


def test_an_array_copys_port_by_index():
    # Beams down from y = -40, their tips pointing up at the bar.
    combs = RefShape(
        name="row",
        component="beam",
        x=30,
        y=-40,
        rotation=-90,
        modifiers=[ArrayModifier(columns=2, dx=60)],
    )
    bar = PathShape(name="bar", layer="device", points=[(0, 40), (0, 20)], width=10)
    joined = device(build(combs, bar, connection(["row.port.tip[1]", "bar.port.end"])))
    assert len(joined.polygons()) == 2  # the first copy stays apart; the second is joined


def test_inside_a_transform_it_joins_its_siblings_there():
    group = TransformShape(
        name="g", rotation=45, x=100, children=[*T, connection(T_ENDS, inner_radius=2)]
    )
    assert len(device(build(group)).polygons()) == 1


def test_it_follows_its_beams():
    doc = EditSession()
    for shape in T:
        doc.nodes.add(shape)
    doc.nodes.add(connection(T_ENDS))
    before = doc.results.geometry().bbox()
    doc.nodes.replace(((0, 2),), T[2].model_copy(update={"points": [(0, 80), (0, 40)]}))
    after = doc.results.geometry().bbox()
    assert after[3] == pytest.approx(before[3] + 20)
    assert doc.results.geometry().layers["device"].count() == 1  # the stem grew with it


def test_a_round_ended_path_is_joined_over_its_cap():
    round_end = PathShape(
        name="up", layer="device", points=[(0, 60), (0, 20)], width=10, ends="round"
    )
    assert len(device(build(T[0], T[1], round_end, connection(T_ENDS))).polygons()) == 1


def test_a_port_without_a_width_takes_the_connections():
    component = beam(port={"width": None})
    shapes = [*around(2), connection(["b0.port.tip", "b1.port.tip"], width=10)]
    assert len(device(build(*shapes, component=component)).polygons()) == 1
    assert "has no width" in error(
        *around(2), connection(["b0.port.tip", "b1.port.tip"]), component=component
    )


def test_its_layer_is_its_ends_or_its_own():
    other = PathShape(name="up", layer="metal", points=[(0, 60), (0, 20)], width=10)
    assert "different layers" in error(T[0], T[1], other, connection(T_ENDS))
    built = build(T[0], T[1], other, connection(T_ENDS, layer="metal"))
    assert built.geometry.layers["metal"].area() / 1e6 > 40 * 10  # the path and the connection


# -- every way an end can be wrong ----------------------------------------------


def test_a_port_off_its_shape():
    off = beam(port={"at": "body.left", "x": -15})  # 15 µm out in the open
    message = error(*around(3), connection([f"b{k}.port.tip" for k in range(3)]), component=off)
    assert "end 'b0.port.tip' is not on its shape" in message


def test_a_port_facing_into_its_beam():
    inward = beam(port={"direction": "right"})
    message = error(*around(3), connection([f"b{k}.port.tip" for k in range(3)]), component=inward)
    assert "is not on its shape" in message and "faces the wrong way" in message


def test_a_port_on_a_rounded_corner():
    rounded = beam(
        corner_radius=3
    )  # the end's corners are rounded: the port's edge is not all on it
    message = error(*around(3), connection([f"b{k}.port.tip" for k in range(3)]), component=rounded)
    assert "is not square across the end there (a rounded corner" in message


def test_a_port_wider_than_its_beam():
    wide = beam(port={"width": 16})
    message = error(*around(3), connection([f"b{k}.port.tip" for k in range(3)]), component=wide)
    assert "narrower than the port's width" in message


def test_a_port_on_a_curve():
    disc = ComponentDef(
        name="beam",
        shapes=[CircleShape(name="body", layer="device", x=20, y=0, radius=20)],
        ports=[PortDef(name="tip", at="body.left", direction="left", width=10)],
    )
    message = error(*around(3), connection([f"b{k}.port.tip" for k in range(3)]), component=disc)
    assert "is not square across the end there" in message


def test_an_end_pointing_away_from_the_others():
    assert "end 'right.port.start' points away from the others" in error(
        *T, connection(["left.port.end", "right.port.start", "up.port.end"])
    )


def test_a_radius_that_does_not_fit_says_how_large_one_may_be():
    message = error(*T, connection(T_ENDS, inner_radius=20))
    assert "inner radius 20 does not fit between ends" in message and "at most 15 there" in message


def test_ends_that_overlap():
    crowded = [
        PathShape(name="a", layer="device", points=[(-40, 0), (-1, 0)], width=10),
        PathShape(name="b", layer="device", points=[(40, 0), (1, 0)], width=10),
        PathShape(name="c", layer="device", points=[(0, -40), (0, -1)], width=10),
    ]
    assert "crosses itself" in error(
        *crowded, connection(["a.port.end", "b.port.end", "c.port.end"])
    )


@pytest.mark.parametrize(
    "ends, message",
    [
        (["left.port.end"], "at least two ends"),
        (["left.port.end", "left.port.end", "up.port.end"], "named twice"),
        (["left.port.end", "nothing.port.end"], "there is no shape 'nothing'"),
        (
            ["left.port.end", "up.port.middle"],
            "end 'up.port.middle': shape 'up' has no port 'middle'",
        ),
    ],
)
def test_ends_that_are_not_there(ends, message):
    assert message in error(*T, connection(ends))


def test_ends_are_ports_by_their_spelling():
    with pytest.raises(ValueError, match="not a port"):
        ConnectionShape(ends=["left.end"])
    with pytest.raises(ValueError, match="not a point reference"):
        ConnectionShape(ends=["left.port.end.x"])


# -- names, files and the editor ---------------------------------------------------


def test_renaming_a_beam_or_its_port_follows_into_the_ends():
    p = project(*around(2), connection(["b0.port.tip", "b1.port.tip"]))
    p.components["top"].rename_shape("b0", "first")
    p.rename_port("beam", "tip", "end")
    assert p.components["top"].shapes[2].ends == ["first.port.end", "b1.port.end"]
    assert Engine().load(p).build("top").geometry.layers["device"].count() == 1


def test_deleting_a_beam_leaves_an_error_naming_it():
    assert "there is no shape 'b1'" in error(
        around(2)[0], connection(["b0.port.tip", "b1.port.tip"])
    )


def test_a_connection_round_trips_through_the_file_format():
    definition = ComponentDef(
        name="top", shapes=[*T, connection(T_ENDS, inner_radius=3, layer="device")]
    )
    data = component_data(definition)
    assert data["shapes"]["hub"] == {
        "kind": "connection",
        "ends": T_ENDS,
        "inner_radius": 3.0,
        "layer": "device",
    }
    assert component_from_data("top", data)[0] == definition


def test_connect_joins_the_selected_shapes_by_their_facing_ports():
    doc = EditSession()
    for shape in T:
        doc.nodes.add(shape)
    path = doc.nodes.connect([((0, 0),), ((0, 1),), ((0, 2),)], inner_radius=2)
    made = doc.node(path)
    assert made.ends == T_ENDS  # the ends at the junction, not the far ones
    assert made.inner_radius == 2
    assert doc.results.geometry().layers["device"].count() == 1


def test_what_connect_refuses():
    doc = EditSession()
    for shape in T:
        doc.nodes.add(shape)
    doc.nodes.add(RectShape(name="plain", layer="device", x0=100, y0=0, x1=110, y1=10))
    with pytest.raises(ValueError, match="two or more"):
        doc.nodes.connect([((0, 0),)])
    with pytest.raises(ValueError, match="plain has no ports"):
        doc.nodes.connect([((0, 0),), ((0, 3),)])


def test_a_switched_off_beam_still_holds_its_connection():
    """Hidden, not removed (as for alignment): its end is still there to join."""
    hidden = [T[0], T[1], T[2].model_copy(update={"enabled": False})]
    assert len(device(build(*hidden, connection(T_ENDS))).polygons()) == 1
