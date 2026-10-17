"""Sizes and minimal boxes in expressions; arrays that start apart (#157)."""

import math

import pytest

from mems_sketch import Align, ArrayModifier, CircleShape, RectShape, TransformShape
from mems_sketch.core.region import Region
from mems_sketch.core.shapes import measure_path
from mems_sketch.core.shapes.rewrite import rename_node_references
from mems_sketch.editing import EditSession


def rect(name="bar", x0=10.0, y0=0.0, x1=12.0, y1=4.0, layer="device", **fields):
    return RectShape(name=name, layer=layer, x0=x0, y0=y0, x1=x1, y1=y1, **fields)


@pytest.fixture
def doc() -> EditSession:
    return EditSession()


def pieces(doc: EditSession, layer: str = "device") -> list[tuple[float, ...]]:
    found = doc.results.geometry().layers.get(layer, Region()).each_merged()
    return sorted(
        tuple(round(v / 1000, 3) for v in (b.left, b.bottom, b.right, b.top))
        for b in (p.bbox() for p in found)
    )


def test_an_array_is_spaced_by_the_shapes_own_width(doc):
    doc.nodes.add(rect(modifiers=[ArrayModifier(columns=3, dx="self.width + 5")]))
    assert pieces(doc) == [(10, 0, 12, 4), (17, 0, 19, 4), (24, 0, 26, 4)]


def test_a_new_array_starts_with_three_copies_apart(doc):
    doc.nodes.add(rect())
    doc.modifiers.add(((0, 0),), "array")
    array = doc.node(((0, 0),)).modifiers[0]
    assert (array.columns, array.rows, array.dx, array.dy) == (
        3,
        1,
        "self.width * 2",
        "self.height * 2",
    )
    assert pieces(doc) == [(10, 0, 12, 4), (14, 0, 16, 4), (18, 0, 20, 4)]
    assert ArrayModifier().dx == 0  # what a file without dx means stays the same
    assert doc.modifiers.self_values(((0, 0),), 0) == {"self.width": 2, "self.height": 4}


def test_other_shapes_sizes_are_known_by_name(doc):
    doc.nodes.add(rect("mass", x0=0, y0=0, x1=30, y1=8))
    doc.nodes.add(rect("cap", x0=0, y0=10, x1="mass.width", y1="10 + mass.height / 4"))
    assert pieces(doc)[1] == (0, 10, 30, 12)
    scope = doc.results.scope(((0, 1),))
    assert (scope["mass.width"], scope["mass.height"]) == (30, 8)


def slanted_beam(doc: EditSession) -> None:
    """A 10 × 2 beam turned by 30° about (3, 4)."""
    doc.nodes.add(
        TransformShape(name="beam", x=3, y=4, rotation=30, children=[rect("b", 0, 0, 10, 2)])
    )


def test_the_minimal_box_of_a_slanted_beam_has_its_corners_on_the_beam(doc):
    slanted_beam(doc)
    x, y = "beam.min.top_right.x", "beam.min.top_right.y"
    doc.nodes.add(rect("dot", f"{x} - 1", f"{y} - 1", f"{x} + 1", f"{y} + 1", layer="metal"))
    doc.nodes.add(
        rect("size", layer="metal", x0=100, y0=0, x1="100 + beam.min.width", y1="beam.min.height")
    )
    a = math.radians(30)
    corner = (3 + 10 * math.cos(a) - 2 * math.sin(a), 4 + 10 * math.sin(a) + 2 * math.cos(a))
    dot, size = pieces(doc, "metal")
    assert (dot[0] + dot[2]) / 2 == pytest.approx(corner[0], abs=2e-3)
    assert (dot[1] + dot[3]) / 2 == pytest.approx(corner[1], abs=2e-3)
    assert size == (100, 0, 110, 2)


def test_a_shape_turns_by_another_ones_angle(doc):
    slanted_beam(doc)
    doc.nodes.add(TransformShape(rotation="beam.min.angle", children=[rect("arm", 0, -1, 20, 1)]))
    record = doc.results.inspection()
    assert record[((0, 1),)].geometry.bbox()[3] == pytest.approx(
        20 * math.sin(math.radians(30)) + math.cos(math.radians(30)), abs=2e-3
    )


def test_a_circle_keeps_its_level_box(doc):
    doc.nodes.add(CircleShape(name="ring", layer="device", x=5, y=5, radius=3))
    doc.nodes.add(rect("probe", x0="ring.min.left.x", y0=20, x1="ring.min.right.x", y1=21))
    doc.nodes.add(rect("tilt", x0=0, y0=30, x1="1 + ring.min.angle", y1=31))
    found = pieces(doc)
    assert (2, 20, 8, 21) in found and (0, 30, 1, 31) in found


def test_measure_names():
    assert measure_path("beam.left.x") == ("beam", "left.x")
    assert measure_path("beam.width") == ("beam", "width")
    assert measure_path("beam.min.angle") == ("beam", "min.angle")
    assert measure_path("beam.min.top_right.y") == ("beam", "min.top_right.y")
    assert measure_path("beam.min.x") == ("beam", "min.x")  # a point named "min"
    assert measure_path("beam.angle") is None
    assert measure_path("width") is None


def test_renaming_a_shape_renames_its_sizes_and_minimal_box():
    arm = rect("arm", x0=0, y0=0, x1="beam.width + beam.min.left.x + beam.min.angle", y1=1)
    (renamed,) = rename_node_references([arm], "beam", "girder")
    assert renamed.x1 == "girder.width + girder.min.left.x + girder.min.angle"


def test_applying_an_array_spaced_by_its_width_keeps_the_copies_where_they_were(doc):
    doc.nodes.add(rect(modifiers=[ArrayModifier(columns=2, dx="self.min.width + 1")]))
    before = pieces(doc)
    doc.modifiers.apply(((0, 0),))
    assert pieces(doc) == before == [(10, 0, 12, 4), (13, 0, 15, 4)]


def test_a_shape_aligns_to_the_corner_of_a_minimal_box(doc):
    slanted_beam(doc)
    doc.nodes.add(
        rect("tag", 0, 0, 2, 2, layer="metal", align=Align(to="beam.min.top_right", point="center"))
    )
    a = math.radians(30)
    corner = (3 + 10 * math.cos(a) - 2 * math.sin(a), 4 + 10 * math.sin(a) + 2 * math.cos(a))
    (tag,) = pieces(doc, "metal")
    assert ((tag[0] + tag[2]) / 2, (tag[1] + tag[3]) / 2) == pytest.approx(corner, abs=2e-3)
