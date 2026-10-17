"""Perforation as a modifier (#83); rounded rectangles, fully rounded ones as
circles and stadiums (#107)."""

import math

import pytest

from mems_sketch import CircleShape, PerforateModifier, PolygonShape, RectShape
from mems_sketch.core.region import Region
from mems_sketch.core.shapes.rewrite import rename_node_references
from mems_sketch.editing import EditSession


def rect(name="plate", x0=0.0, y0=0.0, x1=100.0, y1=100.0, **fields):
    return RectShape(name=name, layer="device", x0=x0, y0=y0, x1=x1, y1=y1, **fields)


@pytest.fixture
def doc() -> EditSession:
    return EditSession()


def device(doc: EditSession) -> Region:
    return doc.results.geometry().layers.get("device", Region())


def holes(doc: EditSession) -> list[tuple[float, float]]:
    """The holes' centres (µm), sorted."""
    centres = []
    for _hull, inner in device(doc).points_um():
        for ring in inner:
            centres.append((round(float(ring[:, 0].mean()), 3), round(float(ring[:, 1].mean()), 3)))
    return sorted(centres)


def area_um2(doc: EditSession) -> float:
    return device(doc).area() / 1e6


# Curves come out as chords within 5 nm: an area within 5 nm of the perimeter.
def near(area: float, perimeter: float):
    return pytest.approx(area, abs=0.005 * perimeter)


# -- rounded rectangles ----------------------------------------------------------


def test_a_rectangle_takes_a_corner_radius(doc):
    doc.nodes.add(rect(x1=10, y1=4, corner_radius=1))
    assert area_um2(doc) == near(40 - (4 - math.pi), 28)


def test_the_radius_is_clamped_to_half_the_shorter_side(doc):
    doc.nodes.add(rect(x1=10, y1=4, corner_radius=50))  # a stadium
    assert area_um2(doc) == near(6 * 4 + math.pi * 4, 28)


def test_a_fully_rounded_square_is_the_circle(doc):
    doc.nodes.add(rect(x0=-3, y0=-3, x1=3, y1=3, corner_radius=3))
    doc.nodes.add(CircleShape(name="disc", layer="other", x=0, y=0, radius=3))
    geometry = doc.results.geometry().layers
    assert geometry["device"].digest() == geometry["other"].digest()


def test_a_negative_corner_radius_is_an_error(doc):
    with pytest.raises(ValueError, match="corner radius must not be negative"):
        doc.nodes.add(rect(corner_radius=-1))


# -- perforation -----------------------------------------------------------------


def test_holes_fill_a_rectangle_centred_with_no_half_holes(doc):
    # 100 wide, a margin of 5: centres from 7 to 93 fit (holes 4 wide), at a pitch of 10.
    doc.nodes.add(rect(modifiers=[PerforateModifier(pitch=10, hole_size=4, margin=5)]))
    centres = holes(doc)
    xs = sorted({x for x, _ in centres})
    assert xs == pytest.approx(
        [10 + 10 * k for k in range(9)], abs=1e-3
    )  # 9 columns, centred on 50
    assert len(centres) == 81
    assert area_um2(doc) == near(100 * 100 - 81 * math.pi * 4, 81 * 4 * math.pi)


def test_an_even_number_of_holes_sits_half_a_pitch_off_the_centre(doc):
    doc.nodes.add(
        rect(x1=80, y1=80, modifiers=[PerforateModifier(pitch=10, hole_size=4, margin=3)])
    )
    xs = sorted({x for x, _ in holes(doc)})
    assert xs == pytest.approx([5 + 10 * k for k in range(8)], abs=1e-3)  # symmetric about 40


def test_holes_follow_any_outline(doc):
    triangle = PolygonShape(name="plate", layer="device", points=[(0, 0), (100, 0), (0, 100)])
    triangle.modifiers = [PerforateModifier(pitch=10, hole_size=4, margin=2)]
    doc.nodes.add(triangle)
    for x, y in holes(doc):
        # Each hole (radius 2) is 2 inside: its centre 4 from every edge.
        assert x >= 4 - 0.01 and y >= 4 - 0.01
        assert (100 - x - y) / math.sqrt(2) >= 4 - 0.01
    assert len(holes(doc)) > 20


def test_square_and_rounded_holes(doc):
    doc.nodes.add(
        rect(modifiers=[PerforateModifier(pitch=20, hole_size=6, corner_radius=0, margin=4)])
    )
    count = len(holes(doc))
    assert count == 25
    assert area_um2(doc) == pytest.approx(100 * 100 - count * 36, abs=1e-3)
    doc.nodes.replace(
        ((0, 0),),
        rect(modifiers=[PerforateModifier(pitch=20, hole_size=6, corner_radius=1, margin=4)]),
    )
    assert area_um2(doc) == near(100 * 100 - 25 * (36 - (4 - math.pi)), 25 * 24)


def test_a_hex_grid_staggers_every_other_row(doc):
    doc.nodes.add(rect(modifiers=[PerforateModifier(grid="hex", pitch=10, hole_size=4, margin=2)]))
    rows: dict[float, list[float]] = {}
    for x, y in holes(doc):
        rows.setdefault(round(y, 2), []).append(x)
    ys = sorted(rows)
    assert ys[1] - ys[0] == pytest.approx(10 * math.sqrt(3) / 2, abs=1e-3)
    first, second = sorted(rows[ys[0]]), sorted(rows[ys[1]])
    assert abs(first[0] - second[0]) == pytest.approx(5, abs=1e-3)


def test_holes_keep_clear_of_other_shapes(doc):
    doc.nodes.add(CircleShape(name="anchor", layer="anchor", x=50, y=50, radius=10))
    doc.nodes.add(
        rect(modifiers=[PerforateModifier(pitch=10, hole_size=4, margin=2, keep_out=["anchor"])])
    )
    for x, y in holes(doc):
        assert math.hypot(x - 50, y - 50) >= 10 + 2 + 2 - 0.01  # anchor, margin, hole radius
    assert (50, 50) not in holes(doc)


def test_holes_refill_when_the_shape_changes(doc):
    doc.nodes.add(rect(modifiers=[PerforateModifier(pitch=10, hole_size=4, margin=5)]))
    before = len(holes(doc))
    doc.nodes.replace(
        ((0, 0),), rect(x1=200, modifiers=[PerforateModifier(pitch=10, hole_size=4, margin=5)])
    )
    assert len(holes(doc)) == pytest.approx(2 * before, abs=9)


def test_a_hole_at_a_chosen_place(doc):
    doc.nodes.add(rect(modifiers=[PerforateModifier(pitch=10, hole_size=4, margin=5, x=13, y=13)]))
    assert holes(doc)[0] == pytest.approx((13, 13), abs=1e-3)


def test_renaming_a_keep_out_shape_follows(doc):
    plate = rect(modifiers=[PerforateModifier(keep_out=["anchor"])])
    [renamed] = rename_node_references([plate], "anchor", "pad")
    assert renamed.modifiers[0].keep_out == ["pad"]


def test_a_keep_out_that_is_not_there_is_an_error(doc):
    with pytest.raises(ValueError, match="no shape named 'nothing'"):
        doc.nodes.add(rect(modifiers=[PerforateModifier(keep_out=["nothing"])]))


def test_too_many_holes_is_an_error_not_a_hang(doc):
    tiny = PerforateModifier(pitch=0.01, hole_size=0.004, margin=0)
    with pytest.raises(ValueError, match="more than 2000000 holes"):
        doc.nodes.add(rect(x1=1e5, y1=1e5, modifiers=[tiny]))


def test_the_standard_plate_has_round_holes_by_default():
    from pathlib import Path

    doc = EditSession.open_project(Path(__file__).parent.parent / "examples" / "resonator")
    doc.set_active("std.perforated_plate")
    assert len(holes(doc)) == 64  # 8 × 8 at a pitch of 20 on 160
    hole = 20 / 3
    assert area_um2(doc) == near(160 * 160 - 64 * math.pi * (hole / 2) ** 2, 64 * math.pi * hole)
