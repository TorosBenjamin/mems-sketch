"""Handles: dragging a shape's sides, vertices, radii and angles (#222)."""

import pytest

from mems_sketch import Layer, ParamDef, Project
from mems_sketch.core.expressions import evaluate
from mems_sketch.core.shapes import (
    Align,
    ArcShape,
    CircleShape,
    PathShape,
    PolygonShape,
    RectShape,
    TransformShape,
)
from mems_sketch.editing import EditSession

SCOPE = {"length": 40.0, "w": 4.0}


def v(value):
    return float(value) if not isinstance(value, str) else float(evaluate(value, SCOPE))


def keys(shape):
    return [h.key for h in shape.edit_handles(v)]


# -- what each kind has, and what a drag changes ----------------------------------------


def test_a_rectangle_has_its_sides_and_corners():
    beam = RectShape(layer="device", x0=0, y0=0, x1="length", y1="w")
    assert keys(beam) == ["x0", "x1", "y0", "y1", "x0 y0", "x0 y1", "x1 y0", "x1 y1"]
    right = next(h for h in beam.edit_handles(v) if h.key == "x1")
    assert (right.x, right.y, right.role) == (40, 2, "edge")


def test_a_drag_keeps_expressions():
    beam = RectShape(layer="device", x0=0, y0=0, x1="length", y1="w")
    assert beam.handle_moved("x1", (43, 99), v) == {"x1": "length + 3"}  # y is not its axis
    assert beam.handle_moved("x0 y1", (-2, 6), v) == {"x0": -2, "y1": "w + 2"}
    moved_again = RectShape(layer="device", x0=0, y0=0, x1="length + 3", y1="w")
    assert moved_again.handle_moved("x1", (40, 0), v) == {"x1": "length"}  # back to plain


def test_circle_and_arc_radii_and_angles():
    circle = CircleShape(layer="device", x=10, y=0, radius="2*w")
    assert circle.handle_moved("radius", (10, 11), v) == {"radius": "2*w + 3"}
    arc = ArcShape(layer="device", inner_radius=6, outer_radius=10, end_angle=220)
    assert keys(arc) == ["outer_radius", "inner_radius", "start_angle", "end_angle"]
    assert arc.handle_moved("end_angle", (0, -5), v) == {"end_angle": 270}
    assert arc.handle_moved("start_angle", (5, 5), v) == {"start_angle": 45}
    assert arc.handle_moved("outer_radius", (0, 12), v) == {"outer_radius": 12}
    ring = ArcShape(layer="device", outer_radius=10)  # a full disc: no angles, no inner radius
    assert keys(ring) == ["outer_radius"]


def test_polygon_and_path_vertices_and_the_path_width():
    poly = PolygonShape(layer="device", points=[(0, 0), ("length", 0), (20, 30)])
    assert keys(poly) == ["point.0", "point.1", "point.2", "insert.0", "insert.1", "insert.2"]
    assert poly.handle_moved("point.1", (45, 1), v)["points"][1] == ("length + 5", 1)
    path = PathShape(layer="device", points=[(0, 0), (10, 0), (10, 10)], width="w")
    assert keys(path)[-1] == "width"
    width = path.edit_handles(v)[-1]
    assert (width.x, width.y) == (5, 2)  # on the first segment's side
    assert path.handle_moved("width", (5, -3), v) == {"width": "w + 2"}
    with pytest.raises(KeyError):
        path.handle_moved("point.7", (0, 0), v)


# -- in a session: frames, alignment, one undo step ----------------------------------------


@pytest.fixture
def session():
    project = Project(name="handles")
    project.add_layer(Layer("device", 1))
    project.components["top"].parameters.append(ParamDef(name="length", default=40))
    project.add(RectShape(name="beam", layer="device", x0=0, y0=0, x1="length", y1=4))
    project.add(
        RectShape(
            name="pad",
            layer="device",
            x0=0,
            y0=0,
            x1=14,
            y1=14,
            align=Align(point="left", to="beam.right", dx=2),
        )
    )
    return EditSession(project)


BEAM, PAD = ((0, 0),), ((0, 1),)


def box(session, path):
    names = {n: (x, y) for n, x, y in session.results.node_points(path)}
    return (*names["bottom_left"], *names["top_right"])


def test_a_drag_is_one_undoable_edit(session):
    session.handles.drag(BEAM, "x1", 43, 2)
    assert session.node(BEAM).x1 == "length + 3"
    assert box(session, PAD)[0] == 45  # the pad follows its alignment
    session.undo()
    assert session.node(BEAM).x1 == "length"


def test_the_handles_are_where_the_shape_is(session):
    found = {h.key: (x, y) for h, x, y in session.handles.handles(PAD)}
    assert found["x0"] == (42, 2) and found["x1 y1"] == (56, 9)  # placed by its alignment


def test_dragging_an_aligned_side_changes_the_offset(session):
    session.handles.drag(PAD, "x0", 45, 2)
    pad = session.node(PAD)
    assert pad.align.dx == 5
    assert box(session, PAD)[::2] == (45, 56)  # the left side moved, the right one stayed
    session.handles.drag(PAD, "x1", 60, 2)  # the other side: just its size
    assert session.node(PAD).align.dx == 5 and box(session, PAD)[::2] == (45, 60)


def test_handles_inside_a_turned_transform(session):
    turned = TransformShape(
        name="turned",
        x=100,
        rotation=90,
        children=[RectShape(name="inner", layer="device", x0=0, y0=0, x1=10, y1=2)],
    )
    session.nodes.add(turned)
    inner = ((0, 2), (0, 0))
    found = {h.key: (round(x, 6), round(y, 6)) for h, x, y in session.handles.handles(inner)}
    assert found["x1"] == (99, 10)  # its right side, turned up
    session.handles.drag(inner, "x1", 99, 15)  # dragged further up: longer
    assert session.node(inner).x1 == 15


def test_the_drag_preview_and_label(session):
    drag = session.handles.start(BEAM, "x1")
    moved = session.handles.dragged(drag, 43, 2)
    assert session.handles.preview(drag, moved).bbox() == (0, 0, 43, 4)
    assert session.handles.describe(drag, moved) == "x1  length → length + 3   (43 µm)"
    drag = session.handles.start(PAD, "x0")
    moved = session.handles.dragged(drag, 45, 2)
    assert session.handles.describe(drag, moved) == "x0  0 → 3   align dx  2 → 5"


def test_no_handle_no_drag(session):
    assert session.handles.start(BEAM, "radius") is None
    with pytest.raises(ValueError, match="no handle"):
        session.handles.drag(BEAM, "radius", 0, 0)


# -- phase 2: adding and removing vertices, typed values --------------------------------------


def test_a_sides_middle_adds_a_vertex_where_it_is_dragged():
    poly = PolygonShape(layer="device", points=[(0, 0), ("length", 0), (20, 30)])
    middle = next(h for h in poly.edit_handles(v) if h.key == "insert.0")
    assert (middle.x, middle.y, middle.role) == (20, 0, "insert")
    assert poly.handle_moved("insert.0", (20, -5), v)["points"] == [
        (0, 0),
        (20, -5),
        ("length", 0),
        (20, 30),
    ]
    assert poly.handle_moved("insert.2", (0, 15), v)["points"][-1] == (0, 15)  # closing side
    path = PathShape(layer="device", points=[(0, 0), (10, 0), (10, 10)], width=2)
    assert [k for k in keys(path) if k.startswith("insert")] == ["insert.0", "insert.1"]


def test_a_vertex_is_removed_while_enough_are_left():
    poly = PolygonShape(layer="device", points=[(0, 0), (10, 0), (10, 10), (0, 10)])
    assert poly.handle_removed("point.1") == {"points": [(0, 0), (10, 10), (0, 10)]}
    triangle = PolygonShape(layer="device", points=[(0, 0), (10, 0), (0, 10)])
    assert triangle.handle_removed("point.1") is None  # a polygon keeps three
    path = PathShape(layer="device", points=[(0, 0), (10, 0)], width=2)
    assert path.handle_removed("point.0") is None  # a path keeps two
    assert RectShape(layer="device", x0=0, y0=0, x1=1, y1=1).handle_removed("x1") is None


def test_typed_values_place_a_handle():
    from mems_sketch.core.shapes.handles import entry_size, handle_label, typed_point

    beam = RectShape(layer="device", x0=0, y0=0, x1="length", y1="w")
    right = next(h for h in beam.edit_handles(v) if h.key == "x1")
    assert entry_size(right) == 1 and typed_point(right, [45]) == (45, 2)
    corner = next(h for h in beam.edit_handles(v) if h.key == "x1 y1")
    assert entry_size(corner) == 2 and handle_label(corner.key) == "x1, y1"
    with pytest.raises(ValueError, match="x, y"):
        typed_point(corner, [3])
    arc = ArcShape(layer="device", inner_radius=6, outer_radius=10, end_angle=220)
    end = next(h for h in arc.edit_handles(v) if h.key == "end_angle")
    assert arc.handle_moved("end_angle", typed_point(end, [270]), v) == {"end_angle": 270}
    outer = next(h for h in arc.edit_handles(v) if h.key == "outer_radius")
    assert arc.handle_moved("outer_radius", typed_point(outer, [14]), v) == {"outer_radius": 14}
    path = PathShape(layer="device", points=[(0, 0), (10, 0)], width="w")
    width = next(h for h in path.edit_handles(v) if h.key == "width")
    assert path.handle_moved("width", typed_point(width, [7]), v) == {"width": "w + 3"}


def test_typing_keeps_expressions_and_is_one_edit(session):
    session.handles.type_in(BEAM, "x1", [45])
    assert session.node(BEAM).x1 == "length + 5"
    session.undo()
    assert session.node(BEAM).x1 == "length"
    with pytest.raises(ValueError):
        session.handles.type_in(BEAM, "x1 y1", [45])


def test_removing_a_vertex_is_one_edit(session):
    session.nodes.add(
        PolygonShape(name="plate", layer="device", points=[(0, 20), (10, 20), (10, 30), (0, 30)])
    )
    plate = ((0, 2),)
    session.handles.remove(plate, "point.2")
    assert session.node(plate).points == [(0, 20), (10, 20), (0, 30)]
    with pytest.raises(ValueError, match="at least 3"):
        session.handles.remove(plate, "point.0")
    session.undo()  # the one removal
    assert len(session.node(plate).points) == 4


def test_the_label_says_a_point_was_added(session):
    session.nodes.add(
        PolygonShape(name="plate", layer="device", points=[(0, 20), (10, 20), (5, 30)])
    )
    drag = session.handles.start(((0, 2),), "insert.0")
    moved = session.handles.dragged(drag, 5, 15)
    assert session.handles.describe(drag, moved) == "new point 2  (5, 15)"


# -- phase 3: rounded corners follow, guides, routes' via points ---------------------------


def corners(*items):
    from mems_sketch.core.shapes.modifiers import CornersModifier

    return [CornersModifier(corners=list(items))]


def test_a_rounded_corner_follows_the_vertex_a_drag_moves(session):
    from mems_sketch.core.shapes.modifiers import Corner

    rounded = [Corner(x=10, y=30, radius=2), Corner(at="self.bottom_left", radius=1)]
    session.nodes.add(
        RectShape(
            name="pad2", layer="device", x0=0, y0=20, x1=10, y1=30, modifiers=corners(*rounded)
        )
    )
    pad2 = ((0, 2),)
    session.handles.drag(pad2, "x1 y1", 15, 35)
    kept = session.node(pad2).modifiers[0].corners
    assert (kept[0].x, kept[0].y) == (15, 35)  # moved with its vertex
    assert kept[1].at == "self.bottom_left"  # a named point follows by itself
    session.handles.drag(pad2, "x0", -5, 32)  # another side: the corner stays
    assert (session.node(pad2).modifiers[0].corners[0].x) == 15


def test_a_corner_on_a_removed_vertex_goes_too(session):
    from mems_sketch.core.shapes.modifiers import Corner

    session.nodes.add(
        PolygonShape(
            name="plate",
            layer="device",
            points=[(0, 20), (10, 20), (10, 30), (0, 30)],
            modifiers=corners(Corner(x=10, y=30, radius=2), Corner(x=0, y=20, radius=1)),
        )
    )
    plate = ((0, 2),)
    session.handles.remove(plate, "point.2")
    left = session.node(plate).modifiers[0].corners
    assert [(c.x, c.y) for c in left] == [(0, 20)]
    session.handles.drag(plate, "insert.0", 5, 15)  # added elsewhere: the corner stays
    assert [(c.x, c.y) for c in session.node(plate).modifiers[0].corners] == [(0, 20)]


def test_a_guides_ends_are_dragged(session):
    from mems_sketch.core.shapes import GuideShape

    session.nodes.add(GuideShape(name="axis", x0=0, y0=-10, x1=0, y1="length"))
    axis = ((0, 2),)
    assert [h.key for h, _, _ in session.handles.handles(axis)] == ["x0 y0", "x1 y1"]
    drag = session.handles.start(axis, "x1 y1")
    moved = session.handles.dragged(drag, 5, 45)
    assert (moved.x1, moved.y1) == (5, "length + 5")
    assert session.handles.sketch(drag, moved) == ([(0, -10), (5, 45)], False)


def test_a_routes_via_points_are_handles():
    from mems_sketch.core.shapes import RouteShape

    wire = RouteShape(start="a.port.p", end="b.port.p", style="straight", via=[(10, 0), ("w", 5)])
    assert [h.key for h in wire.edit_handles(v)] == ["via.0", "via.1"]
    assert wire.handle_moved("via.1", (6, 5), v) == {"via": [(10, 0), ("w + 2", 5)]}
    assert wire.handle_removed("via.0") == {"via": [("w", 5)]}
    assert wire.handle_removed("point.0") is None
    assert wire.drag_sketch(v) == ([(10, 0), (4, 5)], False)


def test_a_routes_sketch_runs_from_port_to_port(session, monkeypatch):
    from mems_sketch.core.shapes import RouteShape
    from mems_sketch.core.shapes.records import Port
    from mems_sketch.core.transform import IDENTITY
    from mems_sketch.editing.handles import HandleDrag

    wire = RouteShape(name="wire", start="beam.port.a", end="pad.port.b", via=[(5, 5)])
    ends = {"start": Port(0, 0, 0, 1, "device"), "end": Port(20, 10, 180, 1, "device")}
    monkeypatch.setattr(session.results, "node_ports", lambda path, component=None: ends)
    drag = HandleDrag(((0, 9),), wire, wire.edit_handles(v)[0], IDENTITY, {}, None)
    moved = session.handles.dragged(drag, 8, 5)
    assert session.handles.sketch(drag, moved) == ([(0, 0), (8, 5), (20, 10)], False)
    assert session.handles.describe(drag, moved) == "via point 1  (5, 5) → (8, 5)"


# -- phase 4: Alt-dragging changes the parameter ------------------------------------------------


def test_alt_drag_changes_the_parameter_a_side_names(session):
    drag = session.handles.start(BEAM, "x1")
    node, tuned = session.handles.retuned(drag, 43, 2)
    assert node == drag.node and tuned == {"length": 43}  # the expression stays
    assert session.handles.preview(drag, node, tuned).bbox() == (0, 0, 43, 4)
    assert session.handles.describe(drag, node, tuned) == "length  40 → 43   also moves: pad"
    session.handles.drag_parameters(BEAM, "x1", 43, 2)
    assert session.node(BEAM).x1 == "length"
    assert session.active_definition.parameters[0].default == 43
    assert box(session, PAD)[0] == 45  # what uses it follows
    session.undo()  # one step
    assert session.active_definition.parameters[0].default == 40


def test_alt_drag_through_a_derived_parameter(session):
    session.parameters.set("reach", "2 * length + 1")
    session.nodes.add(RectShape(name="arm", layer="device", x0=0, y0=10, x1="reach", y1=12))
    arm = ((0, 2),)
    drag = session.handles.start(arm, "x1")
    assert session.handles.retuned(drag, 91, 11)[1] == {"length": 45}


def test_alt_drag_of_a_number_is_an_offset_as_before(session):
    drag = session.handles.start(PAD, "x1")
    assert session.handles.retuned(drag, 60, 2) == (session.handles.dragged(drag, 60, 2), {})
    session.handles.drag_parameters(PAD, "x1", 60, 2)
    assert session.node(PAD).x1 == 18


def test_alt_drag_keeps_the_parameter_in_its_limits(session):
    session.parameters.update("length", max=42, integer=True)
    drag = session.handles.start(BEAM, "x1")
    assert session.handles.retuned(drag, 50, 2)[1] == {"length": 42}
    assert session.handles.retuned(drag, 38.4, 2)[1] == {"length": 38}


def test_alt_drag_leaves_a_parameter_with_a_trial_value(session):
    session.set_trial("length", 41)
    drag = session.handles.start(BEAM, "x1")
    node, tuned = session.handles.retuned(drag, 43, 2)
    assert tuned == {} and node.x1 == "length + 2"  # an offset, as without Alt
