"""Tests of the Python module _geom: the bindings, not the geometry itself
(the C++ tests cover that). Run by ctest with the built module on the path."""

import math
import threading

import _geom as g
import numpy as np
import pytest


def test_version():
    assert g.__version__.count(".") == 2


def test_regions_and_booleans():
    plate = g.Region.rect(0, 0, 10, 10)
    hole = g.Region.circle((5, 5), 2)
    r = plate - hole
    assert r.area == pytest.approx(100 - math.pi * 4, rel=1e-12)
    assert r.pieces == 1
    assert (plate | g.Region.rect(10, 0, 20, 10)).area == pytest.approx(200)
    assert (plate & hole).area == pytest.approx(math.pi * 4)
    assert (plate ^ plate).empty
    assert not g.Region()
    assert plate
    assert "1 pieces" in repr(plate)


def test_points_in_any_form():
    triangle = [(0, 0), (4, 0), (0, 3)]
    assert g.Region.polygon(triangle).area == pytest.approx(6)
    assert g.Region.polygon([[0, 0], [4, 0], [0, 3]]).area == pytest.approx(6)
    assert g.Region.polygon(np.array(triangle, dtype=float)).area == pytest.approx(6)
    assert g.Region.polygon(np.array(triangle)).area == pytest.approx(6)  # integers converted
    assert g.Region.circle(np.array([1.0, 2.0]), 1).bbox.x0 == pytest.approx(0)
    with pytest.raises(TypeError):
        g.Region.circle((1, 2, 3), 1)


def test_outlines_are_arrays():
    r = g.Region.rect(0, 0, 10, 10) - g.Region.rect(2, 2, 4, 4)
    [(hull, holes)] = r.outlines(0.005)
    assert isinstance(hull, np.ndarray)
    assert hull.shape == (4, 2)
    assert hull.dtype == np.float64
    assert len(holes) == 1
    assert holes[0].shape == (4, 2)

    def signed_area(ring):
        x, y = ring[:, 0], ring[:, 1]
        return (x * np.roll(y, -1) - np.roll(x, -1) * y).sum() / 2

    assert signed_area(hull) == pytest.approx(100)  # counter-clockwise
    assert signed_area(holes[0]) == pytest.approx(-4)  # clockwise
    [(circle, _)] = g.Region.circle((0, 0), 10).outlines(0.001)
    assert len(circle) > 100
    assert np.hypot(circle[:, 0], circle[:, 1]) == pytest.approx(10, abs=1e-9)


def test_wires_chain():
    w = g.Wire((0, -1))
    assert w.line_to((10, -1)) is w
    w.arc_to((10, 1), 1).line_to((0, 1)).arc_to((0, -1), 1)
    assert len(w) == 4
    assert w.segments[1].arc
    assert w.segments[1].centre == pytest.approx((10, 0))
    assert w.end == (0, -1)
    assert g.Region.polygon(w).area == pytest.approx(20 + math.pi)

    route = g.Wire((0, 0)).line_to((10, 0)).turn(5, 90)
    assert route.end == pytest.approx((15, 5))
    assert route.end_direction == pytest.approx((0, 1))


def test_paths():
    line = g.Wire((0, 0)).line_to((100, 0))
    assert g.Region.path(line, 10).area == pytest.approx(1000)
    assert g.Region.path(line, 10, g.PathEnds.square).area == pytest.approx(1100)
    corner = g.Wire((0, 0)).line_to((100, 0)).line_to((100, 60))
    assert g.Region.path(corner, 10).area == pytest.approx(1600)
    assert g.Region.path(corner, 10, join=g.Join.bevel).area == pytest.approx(1587.5)


def test_geometry_errors_are_value_errors():
    assert issubclass(g.GeometryError, ValueError)
    with pytest.raises(g.GeometryError, match="radius"):
        g.Wire((0, 0)).arc_to((5, 0), 1)
    with pytest.raises(g.GeometryError):
        g.Region.polygon([(0, 0), (2, 2), (2, 0), (0, 2)])  # crosses itself


def test_transforms():
    t = g.Transform(dx=10, angle_deg=90)
    assert t.apply((1, 0)) == pytest.approx((10, 1))
    assert (t * t.inverted()).is_identity
    assert g.Transform.translation(1, 2).apply((0, 0)) == pytest.approx((1, 2))
    moved = g.Region.rect(0, 0, 1, 1).transformed(g.Transform(dx=5, mirror_x=True))
    b = moved.bbox
    assert (b.x0, b.y0, b.x1, b.y1) == pytest.approx((5, -1, 6, 0))


def test_offset_fillet_and_corners():
    square = g.Region.rect(0, 0, 10, 10)
    assert square.offset(1).area == pytest.approx(144)
    assert square.offset(1, g.Join.round).area == pytest.approx(140 + math.pi)
    corners = square.corners()
    assert len(corners) == 4
    assert all(c.convex for c in corners)
    assert square.filleted(1).area == pytest.approx(100 - (4 - math.pi))
    chamfered = square.rounded([g.CornerRounding((10, 10), 1, g.CornerStyle.chamfer)])
    assert chamfered.area == pytest.approx(99.5)


def test_measurements():
    r = g.Region.rect(0, 0, 4, 2)
    p = g.properties(r)
    assert p.area == pytest.approx(8)
    assert p.perimeter == pytest.approx(12)
    assert p.centroid == pytest.approx((2, 1))
    assert p.ix == pytest.approx(4 * 2**3 / 12)
    d = g.distance(r, g.Region.rect(7, 0, 8, 1))
    assert d.value == pytest.approx(3)
    assert g.overlap_area(r, g.Region.rect(3, 0, 5, 5)) == pytest.approx(2)
    assert [e.kind for e in g.edges(r)] == [g.EdgeKind.line] * 4
    m = g.mass_properties(p, 10, 2330)
    assert m.volume == pytest.approx(8 * 10 * 1e-18)


def test_cells():
    hole = g.CellBuilder("hole").add("etch", g.Region.rect(0, 0, 4, 4)).build()
    assert hole.name == "hole"
    assert hole.layers == ["etch"]
    plate = (
        g.CellBuilder("plate")
        .add("device", g.Region.rect(0, 0, 100, 100))
        .place_array(hole, 10, 10, 10, 10, g.Transform(dx=3, dy=3))
        .build()
    )
    assert plate.layers == ["device", "etch"]
    assert len(plate.placements) == 100
    first, transform = plate.placements[0]
    assert first == hole
    assert transform.dx == pytest.approx(3)
    assert plate.flat("etch").area == pytest.approx(1600)
    assert plate.flat("etch").pieces == 100
    assert plate.own("etch").empty
    ring = g.CellBuilder("ring").place_polar(hole, 8, (50, 50)).build()
    assert len(ring.placements) == 8


def test_long_operations_release_the_gil():
    # Two threads uniting large arrays at once must both finish; with the GIL
    # held they would still finish, so this mainly checks nothing deadlocks.
    squares = [g.Region.rect(i * 3, 0, i * 3 + 2, 2) for i in range(500)]
    results = []

    def work():
        results.append(g.Region.unite(squares).pieces)

    threads = [threading.Thread(target=work) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [500, 500]


def test_snapping():
    plate = g.Region.rect(0, 0, 10, 10) - g.Region.circle((5, 5), 2)
    s = g.snap(plate)  # defaults: 1 nm grid, 5 nm chord
    assert s.grid == 0.001
    [(hull, holes)] = s.polygons
    assert hull.dtype == np.int64
    assert sorted(hull.tolist()) == [[0, 0], [0, 10000], [10000, 0], [10000, 10000]]
    assert len(holes) == 1
    # The hole's circle, split at a 5 nm chord, is smaller by about 2/3 of
    # the chord times its circumference.
    expected = s.report.area_exact + 2 / 3 * 0.005 * 2 * math.pi * 2
    assert s.report.area_snapped == pytest.approx(expected, abs=2e-3)
    assert not s.report.changed_shape
    assert s.report.events == []

    gap = g.Region.rect(0, 0, 1, 1) | g.Region.rect(1.0004, 0, 2, 1)
    report = g.snap(gap, grid=0.001, chord=0.005).report
    [event] = report.events
    assert event.change == g.SnapChange.merged
    assert event.where.x1 == pytest.approx(2)
    assert "merged" in repr(event)
    assert g.snap(gap, grid=0.0001).report.events == []  # a finer grid keeps the gap

    with pytest.raises(g.GeometryError):
        g.snap(plate, grid=0)
