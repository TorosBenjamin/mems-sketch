import pytest

from mems_sketch import (
    Align,
    BooleanShape,
    CircleShape,
    Instance,
    PolygonShape,
    Project,
    RectShape,
    TransformShape,
)
from mems_sketch.core.shapes import offset_value, translated


@pytest.mark.parametrize(
    ("value", "delta", "expected"),
    [
        (10, 2.5, 12.5),
        (10.0, -10, 0),
        ("plate/2 + 39", 11, "plate/2 + 50"),
        ("plate/2 + 39", -39, "plate/2"),
        ("plate/2 - 5", 2, "plate/2 - 3"),
        ("plate/2", 1.25, "plate/2 + 1.25"),
        ("-plate/2", -3, "-plate/2 - 3"),
        ("2 * (a + 1)", 0.001, "2 * (a + 1) + 0.001"),
        ("a", 0, "a"),
        ("a + 0.1", 0.2, "a + 0.3"),  # no float noise
    ],
)
def test_offset_value_keeps_expressions(value, delta, expected):
    assert offset_value(value, delta) == expected


def test_translate_primitives_references_and_transforms():
    r = translated(RectShape(layer="d", x0=0, y0=0, x1="w", y1=5), 10, -2)
    assert (r.x0, r.x1, r.y0, r.y1) == (10, "w + 10", -2, 3)
    poly = translated(PolygonShape(layer="d", points=[(0, 0), (1, 0), (0, 1)]), 1, 1)
    assert poly.points == [(1, 1), (2, 1), (1, 2)]
    ref = translated(Instance("c", "anchor", x="p + 1"), 4, 0)
    assert (ref.x, ref.y) == ("p + 5", 0)
    t = translated(TransformShape(children=[RectShape(layer="d", x0=0, y0=0, x1=1, y1=1)]), 3, 3)
    assert (t.x, t.y) == (3, 3) and t.children[0].x0 == 0  # content stays local


def test_translate_aligned_node_changes_its_offset():
    node = RectShape(layer="d", x0=0, y0=0, x1=1, y1=1, align=Align(to="a.top", dy=-1))
    moved = translated(node, 2, 3)
    assert (moved.align.dx, moved.align.dy, moved.x0) == (2, 2, 0)


def test_translate_boolean_keeps_inner_relations():
    plate = BooleanShape(
        op="subtract",
        a=[RectShape(name="outline", layer="d", x0=0, y0=0, x1=40, y1=40)],
        b=[
            CircleShape(name="hole", layer="d", radius=5, align=Align(to="outline.center")),
            RectShape(
                name="slot", layer="d", x0="outline.left.x", y0=1, x1="outline.left.x + 3", y1=2
            ),
        ],
    )
    moved = translated(plate, 10, 20)
    outline, (hole, slot) = moved.a[0], moved.b
    assert (outline.x0, outline.y0) == (10, 20)
    assert hole.align.dx == 0  # follows the outline by itself
    assert (slot.x0, slot.y0) == ("outline.left.x", 21)  # x follows, y is moved


def test_moving_a_project_node_moves_what_is_aligned_to_it():
    project = Project()
    project.add(RectShape(name="base", layer="device", x0=0, y0=0, x1=10, y1=10))
    project.add(
        RectShape(name="post", layer="metal", x0=0, y0=0, x1=2, y1=2, align=Align(to="base.top"))
    )
    project.replace("base", translated(project.find("base"), 100, 0))
    box = project.render().layers["metal"].bbox()
    assert (box.left / 1000, box.bottom / 1000) == (104, 9)
