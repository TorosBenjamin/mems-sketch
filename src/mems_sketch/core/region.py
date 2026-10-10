"""Polygons on one layer, on the 1 nm database grid, as the engine gives them.

A :class:`Region` holds polygons as integer rings (nm): an outline and its
holes. Merging, booleans and offsets go through the geometry library's grid
operations (``mems_sketch._geom.grid_*``, exact on integers); everything else (area, bounding box, transforms) is
plain arithmetic on the rings. The integer coordinates make results exact
and comparable: what the engine snapped is what is measured, drawn, checked
and exported.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from typing import NamedTuple

from mems_sketch.core.transform import Transform

DBU_UM = 0.001  # database unit: 1 nm, in micrometres
CHORD_UM = 0.005  # how far curves may stray from the exact geometry, µm

IntRing = list[tuple[int, int]]


class IntPolygon(NamedTuple):
    """A polygon in database units: its outline and its holes, not closed."""

    hull: IntRing
    holes: list[IntRing]

    def bbox(self) -> Box:
        xs = [x for x, _ in self.hull]
        ys = [y for _, y in self.hull]
        return Box(min(xs), min(ys), max(xs), max(ys))

    def area(self) -> int | float:
        return _ring_area(self.hull) - sum(_ring_area(h) for h in self.holes)

    def num_points(self) -> int:
        return len(self.hull) + sum(len(h) for h in self.holes)


class Point(NamedTuple):
    x: float
    y: float


class Box:
    """An axis-aligned box in database units; ``Box()`` is empty."""

    __slots__ = ("bottom", "left", "right", "top")

    def __init__(
        self,
        left: int | None = None,
        bottom: int | None = None,
        right: int | None = None,
        top: int | None = None,
    ) -> None:
        if left is None:
            self.left = self.bottom = 1
            self.right = self.top = -1
        else:
            self.left, self.right = min(left, right), max(left, right)
            self.bottom, self.top = min(bottom, top), max(bottom, top)

    def empty(self) -> bool:
        return self.left > self.right

    def center(self) -> Point:
        return Point((self.left + self.right) / 2, (self.bottom + self.top) / 2)

    def width(self) -> int:
        return 0 if self.empty() else self.right - self.left

    def height(self) -> int:
        return 0 if self.empty() else self.top - self.bottom

    def __add__(self, other: Box) -> Box:
        if self.empty():
            return (
                Box(other.left, other.bottom, other.right, other.top)
                if not other.empty()
                else Box()
            )
        if other.empty():
            return Box(self.left, self.bottom, self.right, self.top)
        return Box(
            min(self.left, other.left),
            min(self.bottom, other.bottom),
            max(self.right, other.right),
            max(self.top, other.top),
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Box):
            return NotImplemented
        if self.empty() or other.empty():
            return self.empty() and other.empty()
        return (self.left, self.bottom, self.right, self.top) == (
            other.left,
            other.bottom,
            other.right,
            other.top,
        )

    def __hash__(self) -> int:
        return hash((self.left, self.bottom, self.right, self.top))

    def __repr__(self) -> str:
        if self.empty():
            return "Box()"
        return f"Box({self.left}, {self.bottom}, {self.right}, {self.top})"


class Region:
    """Polygons on one layer, in database units (see the module docstring).

    ``Region()`` is empty; ``Region(box)``, ``Region(polygon)`` and
    ``Region(region)`` hold that shape. Polygons inserted may overlap until
    the region is merged (:meth:`merged`, or whatever measures it).
    """

    def __init__(self, shape: Box | IntPolygon | Region | None = None) -> None:
        self._polygons: list[IntPolygon] = []
        self._merged = True
        if shape is not None:
            self.insert(shape)

    @classmethod
    def from_polygons(cls, polygons: Iterable, merged: bool = False) -> Region:
        """From (hull, [holes]) rings of integer points; ``merged`` when they are
        known not to overlap (as the engine's are)."""
        region = cls()
        region._polygons = [
            IntPolygon(_int_ring(hull), [_int_ring(h) for h in holes]) for hull, holes in polygons
        ]
        region._merged = merged or not region._polygons
        return region

    @classmethod
    def from_um(cls, hull: Iterable, holes: Iterable[Iterable] = ()) -> Region:
        """One polygon given in µm, rounded to the grid."""
        return cls(IntPolygon(_dbu_ring(hull), [_dbu_ring(h) for h in holes]))

    # -- building ----------------------------------------------------------------

    def insert(self, shape: Box | IntPolygon | Region) -> Region:
        if isinstance(shape, Region):
            if not shape._polygons:
                return self
            self._merged = not self._polygons and shape._merged
            self._polygons.extend(shape._polygons)
            return self
        if isinstance(shape, Box):
            if shape.empty():
                return self
            l, b, r, t = shape.left, shape.bottom, shape.right, shape.top
            polygon = IntPolygon([(l, b), (r, b), (r, t), (l, t)], [])
        else:
            polygon = IntPolygon(_int_ring(shape[0]), [_int_ring(h) for h in shape[1]])
        self._merged = not self._polygons
        self._polygons.append(polygon)
        return self

    # -- measuring ---------------------------------------------------------------

    def each_merged(self) -> list[IntPolygon]:
        return self.merged()._polygons

    def each(self) -> list[IntPolygon]:
        """The polygons as inserted (they may overlap)."""
        return list(self._polygons)

    def merged(self) -> Region:
        if self._merged:
            return self
        from mems_sketch import _geom

        self._polygons = _wrap(_geom.grid_merged(self._polygons))
        self._merged = True  # keep the work
        return self

    def is_empty(self) -> bool:
        return not self._polygons

    def count(self) -> int:
        """How many separate polygons, merged."""
        return len(self.each_merged())

    def area(self) -> int:
        """In nm²."""
        return sum(
            _ring_area(p.hull) - sum(_ring_area(h) for h in p.holes) for p in self.each_merged()
        )

    def perimeter(self) -> int | float:
        return sum(
            _ring_length(p.hull) + sum(_ring_length(h) for h in p.holes) for p in self.each_merged()
        )

    def bbox(self) -> Box:
        box = Box()
        for polygon in self._polygons:
            box = box + polygon.bbox()
        return box

    def points_um(self) -> list[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]]:
        """The merged polygons in µm: (hull, [holes])."""
        return [
            ([(x * DBU_UM, y * DBU_UM) for x, y in p.hull], [_um_ring(h) for h in p.holes])
            for p in self.each_merged()
        ]

    # -- operations ----------------------------------------------------------------

    def transformed(self, transform: Transform) -> Region:
        """Moved, rotated, mirrored and scaled (``transform`` in µm), back on the grid."""
        if transform.is_identity:
            return self
        exact = transform.angle % 90 == 0 and transform.mag == 1

        def place(ring: IntRing) -> IntRing:
            out = []
            for x, y in ring:
                px, py = transform.apply(x * DBU_UM, y * DBU_UM)
                out.append((round(px / DBU_UM), round(py / DBU_UM)))
            if transform.mirror:  # keep the hull counter-clockwise
                out.reverse()
            return out

        result = Region()
        result._polygons = [
            IntPolygon(place(p.hull), [place(h) for h in p.holes]) for p in self._polygons
        ]
        result._merged = self._merged and exact
        return result

    def moved(self, dx: int, dy: int) -> Region:
        result = Region()
        result._polygons = [
            IntPolygon(
                [(x + dx, y + dy) for x, y in p.hull],
                [[(x + dx, y + dy) for x, y in h] for h in p.holes],
            )
            for p in self._polygons
        ]
        result._merged = self._merged
        return result

    def __add__(self, other: Region) -> Region:
        """Both regions' polygons, not merged."""
        return Region(self).insert(other)

    def __or__(self, other: Region) -> Region:
        return Region(self).insert(other).merged()

    def __and__(self, other: Region) -> Region:
        return _boolean(self, other, "and")

    def __sub__(self, other: Region) -> Region:
        return _boolean(self, other, "sub")

    def __xor__(self, other: Region) -> Region:
        return _boolean(self, other, "xor")

    def sized(self, d: float, join: str = "miter") -> Region:
        """Grown (d > 0) or shrunk by ``d`` nm."""
        if self.is_empty() or d == 0:
            return self.merged()
        from mems_sketch import _geom

        joins = {"miter": _geom.Join.miter, "round": _geom.Join.round, "bevel": _geom.Join.bevel}
        return _from_list(_geom.grid_offset(self._polygons, d, joins[join]))

    def __len__(self) -> int:
        return len(self._polygons)

    def __repr__(self) -> str:
        return f"<Region: {len(self._polygons)} polygons>"


# -- the geometry library ---------------------------------------------------------


def _wrap(polygons: list) -> list[IntPolygon]:
    return [IntPolygon(hull, holes) for hull, holes in polygons]


def _from_list(polygons: list) -> Region:
    region = Region()
    region._polygons = _wrap(polygons)
    return region


def _boolean(a: Region, b: Region, op: str) -> Region:
    if a.is_empty():
        return Region() if op in ("and", "sub") else b.merged()
    if b.is_empty():
        return Region() if op == "and" else a.merged()
    from mems_sketch import _geom

    ops = {
        "and": _geom.GridOp.intersect,
        "sub": _geom.GridOp.subtract,
        "xor": _geom.GridOp.exclusive,
    }
    return _from_list(_geom.grid_boolean(a._polygons, b._polygons, ops[op]))


# -- rings -----------------------------------------------------------------------------


def _int_ring(points: Iterable) -> IntRing:
    return [(int(p[0]), int(p[1])) for p in points]


def _dbu_ring(points: Iterable) -> IntRing:
    return [(round(p[0] / DBU_UM), round(p[1] / DBU_UM)) for p in points]


def _um_ring(ring: IntRing) -> list[tuple[float, float]]:
    return [(x * DBU_UM, y * DBU_UM) for x, y in ring]


def _ring_area(ring: Sequence[tuple[int, int]]) -> int:
    """The area a ring encloses (twice the shoelace sum halved; always ≥ 0)."""
    twice = 0
    n = len(ring)
    for k in range(n):
        x0, y0 = ring[k]
        x1, y1 = ring[(k + 1) % n]
        twice += x0 * y1 - x1 * y0
    return abs(twice) // 2 if twice % 2 == 0 else abs(twice) / 2


def _ring_length(ring: Sequence[tuple[int, int]]) -> float:
    n = len(ring)
    return sum(
        ((ring[(k + 1) % n][0] - ring[k][0]) ** 2 + (ring[(k + 1) % n][1] - ring[k][1]) ** 2) ** 0.5
        for k in range(n)
    )


def iter_points(region: Region) -> Iterator[tuple[int, int]]:
    for polygon in region._polygons:
        yield from polygon.hull
