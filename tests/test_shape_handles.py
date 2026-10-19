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
    assert keys(poly) == ["point.0", "point.1", "point.2"]
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
    names = dict((n, (x, y)) for n, x, y in session.results.node_points(path))
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
