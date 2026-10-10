"""Placements as plain values (Transform), and the Geometry methods code outside the
backend uses instead of the backend's own types."""

import math
import random

import pytest

from mems_sketch.core.component import Geometry
from mems_sketch.core.transform import IDENTITY, Transform


def close(a, b, tol=1e-9):
    return all(abs(p - q) <= tol for p, q in zip(a, b, strict=True))


def test_mirror_then_rotate_then_scale_then_move():
    t = Transform(10, 20, 90, mirror=True, mag=2)
    # (1, 2) mirrored -> (1, -2), turned 90° -> (2, 1), doubled -> (4, 2), moved.
    assert close(t.apply(1, 2), (14, 22))
    assert close(t.apply_vector(1, 2), (4, 2))


def test_quarter_turns_are_exact():
    assert Transform(angle=90).apply(1, 0) == (0.0, 1.0)
    assert Transform(angle=-90).apply(3, 0) == (0.0, -3.0)
    assert Transform(angle=270).angle == 270


def test_composition_and_inverse():
    random.seed(1)
    for _ in range(200):
        a, b = (
            Transform(
                random.uniform(-9, 9),
                random.uniform(-9, 9),
                random.choice([0, 90, 180, random.uniform(-400, 400)]),
                random.random() < 0.5,
                random.choice([0.5, 1, 2]),
            )
            for _ in range(2)
        )
        p = (random.uniform(-5, 5), random.uniform(-5, 5))
        assert close((a * b).apply(*p), a.apply(*b.apply(*p)))
        assert close(a.inverted().apply(*a.apply(*p)), p)
    assert IDENTITY.is_identity and (Transform(1, 2) * Transform(-1, -2)).is_identity


def test_rotating_and_reflecting_about_a_point():
    assert close(Transform.rotating(90, (1, 1)).apply(2, 1), (1, 2))
    assert close(Transform.reflecting(90, (3, 0)).apply(1, 5), (5, 5))  # across x = 3
    assert close(Transform.reflecting(0, (0, 2)).apply(1, 5), (1, -1))  # across y = 2
    diagonal = Transform.reflecting(45)
    assert close(diagonal.apply(1, 0), (0, 1)) and diagonal.mirror


def test_angles_are_kept_in_one_turn():
    assert Transform(angle=-90).angle == 270
    assert Transform(angle=720).angle == 0
    assert math.isclose(Transform(angle=-0.5).angle, 359.5)


@pytest.fixture
def plate() -> Geometry:
    g = Geometry()
    g.add_polygon(
        "device", [(0, 0), (10, 0), (10, 10), (0, 10)], holes=[[(4, 4), (6, 4), (6, 6), (4, 6)]]
    )
    g.add_rect("metal", 20, 0, 22, 2)
    return g


def test_geometry_polygons_and_box(plate):
    [polygon] = plate.polygons("device")
    assert sorted(map(tuple, polygon.hull.tolist())) == [(0, 0), (0, 10), (10, 0), (10, 10)]
    assert [sorted(map(tuple, h.tolist())) for h in polygon.holes] == [
        [(4, 4), (4, 6), (6, 4), (6, 6)]
    ]
    assert plate.bbox() == (0, 0, 22, 10)
    assert plate.pieces() == 2 and len(plate.polygons()) == 2
    assert sorted(plate.layer_names()) == ["device", "metal"]
    assert plate.only(["metal"]).bbox() == (20, 0, 22, 2)
    assert Geometry().bbox() is None and Geometry().is_empty()


def test_geometry_touches(plate):
    assert plate.touches(1, 1)
    assert not plate.touches(5, 5)  # in the hole
    assert plate.touches(5, 5, reach=1.5)  # the hole's edge is 1 µm away
    assert not plate.touches(15, 5) and plate.touches(15, 5, reach=5.5)


def test_geometry_placed_and_compared(plate):
    moved = plate.transformed(Transform(100, 0, 90))
    assert moved.bbox() == pytest.approx((90, 0, 100, 22))
    added = moved.difference(plate)
    assert added.bbox() == moved.bbox() and plate.difference(plate).is_empty()
