"""Regions of grid polygons: arrays, measures, the box, hashes and hit tests."""

import numpy as np

from mems_sketch.core.region import Box, IntPolygon, Region, measures


def plate() -> Region:
    """A 1000 nm square with a 100 nm square hole."""
    return Region(Box(0, 0, 1000, 1000)) - Region(Box(100, 100, 200, 200))


def test_polygons_are_int64_arrays_and_unpack_as_hull_and_holes():
    [polygon] = plate().each_merged()
    hull, holes = polygon
    assert hull.dtype == np.int64 and hull.shape == (4, 2)
    assert len(holes) == 1 and holes[0].shape == (4, 2)
    assert IntPolygon([(0, 0), (1, 0), (1, 1)]) == IntPolygon(np.array([[0, 0], [1, 0], [1, 1]]))


def test_area_perimeter_and_measures_agree():
    region = plate() + Region(Box(2000, 0, 2003, 7))
    pieces = region.each_merged()
    areas, perimeters = measures(pieces)
    assert sorted(areas) == sorted(p.area() for p in pieces) == [21, 990_000]
    assert sorted(perimeters) == sorted(p.perimeter() for p in pieces) == [20, 4400]
    assert region.area() == 990_021


def test_the_box_is_kept_and_follows_moves():
    region = Region.from_polygons([([(0, 0), (10, 0), (10, 5)], [])], box=(0, 0, 10, 5))
    assert region.bbox() == Box(0, 0, 10, 5)
    assert region.moved(3, -1).bbox() == Box(3, -1, 13, 4)
    region.insert(Box(-5, -5, 0, 0))
    assert region.bbox() == Box(-5, -5, 10, 5)


def test_equal_geometry_has_equal_digests():
    assert plate().digest() == plate().digest()
    assert plate().digest() != plate().moved(1, 0).digest()
    a = Region(Box(0, 0, 10, 10)).insert(Box(5, 5, 20, 20))  # merged before hashing
    assert a.digest() == (Region(Box(0, 0, 10, 10)) | Region(Box(5, 5, 20, 20))).digest()


def test_near_finds_the_inside_and_the_outline_but_not_holes():
    [polygon] = plate().each_merged()
    assert polygon.near(500, 500, 1)  # inside
    assert not polygon.near(150, 150, 1)  # in the hole
    assert polygon.near(150, 150, 50)  # within reach of the hole's edge
    assert polygon.near(1000, 500, 1)  # on the outline
    assert not polygon.near(2000, 500, 1)
