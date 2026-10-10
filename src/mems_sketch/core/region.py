"""Polygons on one layer, on the 1 nm database grid, as the engine gives them.

A :class:`Region` holds polygons as integer rings (nm): an outline and its
holes, each an ``(n, 2)`` int64 NumPy array, which the engine and the
geometry library hand over without copying point by point. Merging,
booleans and offsets go through the geometry library's grid operations
(``mems_sketch._geom.grid_*``, exact on integers); everything else (area,
bounding box, transforms) is array arithmetic. The integer coordinates make
results exact and comparable: what the engine snapped is what is measured,
drawn, checked and exported.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator
from typing import NamedTuple

import numpy as np

from mems_sketch.core.transform import Transform

DBU_UM = 0.001  # database unit: 1 nm, in micrometres
CHORD_UM = 0.005  # how far curves may stray from the exact geometry, µm

IntRing = np.ndarray  # (n, 2) int64: a ring's points, not closed


def ring(points) -> IntRing:
    """Points as an (n, 2) int64 array (no copy when they already are one)."""
    array = np.asarray(points)
    if array.size == 0:
        return np.zeros((0, 2), np.int64)
    if array.dtype != np.int64:
        array = array.astype(np.int64)
    return np.ascontiguousarray(array.reshape(-1, 2))


class IntPolygon:
    """A polygon in database units: its outline and its holes, not closed.
    Unpacks as ``hull, holes``."""

    __slots__ = ("_edges", "holes", "hull")

    def __init__(self, hull, holes: Iterable = ()) -> None:
        self.hull = ring(hull)
        self.holes = [ring(h) for h in holes]
        self._edges = None

    def __iter__(self) -> Iterator:
        yield self.hull
        yield self.holes

    def __getstate__(self) -> tuple:
        return self.hull, self.holes  # not the edges: worked out again when needed

    def __setstate__(self, state: tuple) -> None:
        self.hull, self.holes = state
        self._edges = None

    def __getitem__(self, index: int):
        return (self.hull, self.holes)[index]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, IntPolygon):
            return NotImplemented
        return (
            np.array_equal(self.hull, other.hull)
            and len(self.holes) == len(other.holes)
            and all(np.array_equal(a, b) for a, b in zip(self.holes, other.holes, strict=True))
        )

    __hash__ = None  # the arrays can change

    def __repr__(self) -> str:
        return f"IntPolygon({self.hull.tolist()}, {[h.tolist() for h in self.holes]})"

    def bbox(self) -> Box:
        if not len(self.hull):
            return Box()
        (left, bottom), (right, top) = self.hull.min(axis=0), self.hull.max(axis=0)
        return Box(int(left), int(bottom), int(right), int(top))

    def area(self) -> int | float:
        return _ring_area(self.hull) - sum(_ring_area(h) for h in self.holes)

    def perimeter(self) -> float:
        return _ring_length(self.hull) + sum(_ring_length(h) for h in self.holes)

    def num_points(self) -> int:
        return len(self.hull) + sum(len(h) for h in self.holes)

    def edges(self) -> np.ndarray:
        """Every edge of the outline and the holes, as rows ``x0, y0, x1, y1``
        (float; computed once)."""
        if self._edges is None:
            rings = [r for r in (self.hull, *self.holes) if len(r)]
            starts = np.concatenate(rings).astype(float)
            ends = np.concatenate([np.roll(r, -1, axis=0) for r in rings]).astype(float)
            self._edges = np.hstack([starts, ends])
        return self._edges

    def near(self, x: int, y: int, reach: int) -> bool:
        """The point is inside the polygon, or within ``reach`` of its outline."""
        e = self.edges()
        x0, y0, x1, y1 = e[:, 0], e[:, 1], e[:, 2], e[:, 3]
        dx, dy = x1 - x0, y1 - y0
        length2 = dx * dx + dy * dy
        with np.errstate(divide="ignore", invalid="ignore"):
            t = np.where(length2 > 0, ((x - x0) * dx + (y - y0) * dy) / length2, 0.0)
            at = x0 + (y - y0) * dx / dy
        t = np.clip(t, 0.0, 1.0)
        px, py = x0 + t * dx - x, y0 + t * dy - y
        if np.any(px * px + py * py <= reach * reach):
            return True
        crosses = (y0 > y) != (y1 > y)  # never true for a horizontal edge (dy = 0)
        return bool(np.count_nonzero(crosses & (x < at)) % 2)


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
        self._box: Box | None = Box()  # None: not known yet
        self._digest: str | None = None
        if shape is not None:
            self.insert(shape)

    @classmethod
    def from_polygons(cls, polygons: Iterable, merged: bool = False, box=None) -> Region:
        """From (hull, [holes]) rings of integer points; ``merged`` when they are
        known not to overlap (as the engine's are); ``box`` (left, bottom, right,
        top) when it is known (the engine gives it)."""
        region = cls()
        region._polygons = [
            p if isinstance(p, IntPolygon) else IntPolygon(p[0], p[1]) for p in polygons
        ]
        region._merged = merged or not region._polygons
        region._box = Box(*box) if box is not None else None if region._polygons else Box()
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
            self._digest = None
            self._box = None if self._box is None or shape._box is None else self._box + shape._box
            return self
        if isinstance(shape, Box):
            if shape.empty():
                return self
            l, b, r, t = shape.left, shape.bottom, shape.right, shape.top
            polygon = IntPolygon([(l, b), (r, b), (r, t), (l, t)])
        elif isinstance(shape, IntPolygon):
            polygon = shape
        else:
            polygon = IntPolygon(shape[0], shape[1])
        self._merged = not self._polygons
        self._polygons.append(polygon)
        self._digest = None
        self._box = None if self._box is None else self._box + polygon.bbox()
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

        self._polygons = _wrap(_geom.grid_merged(_pairs(self._polygons)))
        self._merged = True  # keep the work (merging keeps the box)
        return self

    def is_empty(self) -> bool:
        return not self._polygons

    def digest(self) -> str:
        """A hash of the merged polygons' points: equal for equal geometry (worked
        out once)."""
        if self._digest is None:
            h = hashlib.blake2b(digest_size=16)
            for p in self.each_merged():
                h.update(p.hull.tobytes())
                for hole in p.holes:
                    h.update(b"|")
                    h.update(hole.tobytes())
                h.update(b";")
            self._digest = h.hexdigest()
        return self._digest

    def count(self) -> int:
        """How many separate polygons, merged."""
        return len(self.each_merged())

    def area(self) -> int | float:
        """In nm²."""
        return sum(p.area() for p in self.each_merged())

    def perimeter(self) -> float:
        return sum(p.perimeter() for p in self.each_merged())

    def bbox(self) -> Box:
        """The box around the polygons (worked out once; the engine's regions come
        with it)."""
        if self._box is None:
            hulls = [p.hull for p in self._polygons if len(p.hull)]
            if hulls:
                points = np.concatenate(hulls)
                (left, bottom), (right, top) = points.min(axis=0), points.max(axis=0)
                self._box = Box(int(left), int(bottom), int(right), int(top))
            else:
                self._box = Box()
        box = self._box
        return Box(box.left, box.bottom, box.right, box.top) if not box.empty() else Box()

    def points_um(self) -> list[tuple[np.ndarray, list[np.ndarray]]]:
        """The merged polygons in µm: (hull, [holes]) as (n, 2) float arrays."""
        return [(p.hull * DBU_UM, [h * DBU_UM for h in p.holes]) for p in self.each_merged()]

    # -- operations ----------------------------------------------------------------

    def transformed(self, transform: Transform) -> Region:
        """Moved, rotated, mirrored and scaled (``transform`` in µm), back on the grid."""
        if transform.is_identity:
            return self
        exact = transform.angle % 90 == 0 and transform.mag == 1
        (a, d), (b, e) = transform.apply_vector(1.0, 0.0), transform.apply_vector(0.0, 1.0)
        c, f = transform.dx / DBU_UM, transform.dy / DBU_UM

        def place(points: IntRing) -> IntRing:
            x, y = points[:, 0], points[:, 1]
            out = np.empty_like(points)
            out[:, 0] = np.rint(a * x + b * y + c)
            out[:, 1] = np.rint(d * x + e * y + f)
            return out[::-1].copy() if transform.mirror else out  # keep the hull counter-clockwise

        result = Region()
        result._polygons = [
            IntPolygon(place(p.hull), [place(h) for h in p.holes]) for p in self._polygons
        ]
        result._merged = self._merged and exact
        result._box = None
        return result

    def moved(self, dx: int, dy: int) -> Region:
        result = Region()
        shift = np.array([dx, dy], dtype=np.int64)
        result._polygons = [
            IntPolygon(p.hull + shift, [h + shift for h in p.holes]) for p in self._polygons
        ]
        result._merged = self._merged
        box = self.bbox()
        result._box = (
            Box()
            if box.empty()
            else Box(box.left + dx, box.bottom + dy, box.right + dx, box.top + dy)
        )
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
        return _from_list(_geom.grid_offset(_pairs(self._polygons), d, joins[join]))

    def __len__(self) -> int:
        return len(self._polygons)

    def __repr__(self) -> str:
        return f"<Region: {len(self._polygons)} polygons>"


# -- the geometry library ---------------------------------------------------------


def _pairs(polygons: list[IntPolygon]) -> list[tuple]:
    return [(p.hull, p.holes) for p in polygons]


def _wrap(polygons: list) -> list[IntPolygon]:
    return [IntPolygon(hull, holes) for hull, holes in polygons]


def _from_list(polygons: list) -> Region:
    region = Region()
    region._polygons = _wrap(polygons)
    region._box = None if region._polygons else Box()
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
    return _from_list(_geom.grid_boolean(_pairs(a._polygons), _pairs(b._polygons), ops[op]))


# -- rings -----------------------------------------------------------------------------


def _dbu_ring(points: Iterable) -> IntRing:
    array = np.asarray([tuple(p)[:2] for p in points], dtype=float).reshape(-1, 2)
    return ring(np.rint(array / DBU_UM))


def measures(polygons: list[IntPolygon]) -> tuple[np.ndarray, np.ndarray]:
    """The area (nm², holes taken off) and perimeter (nm, holes included) of
    each polygon, all worked out together (fast for many small polygons)."""
    rings, owner, sign = [], [], []
    for k, p in enumerate(polygons):
        for j, r in enumerate((p.hull, *p.holes)):
            if len(r):
                rings.append(r)
                owner.append(k)
                sign.append(1 if j == 0 else -1)
    areas, perimeters = np.zeros(len(polygons)), np.zeros(len(polygons))
    if not rings:
        return areas, perimeters
    points = np.concatenate(rings).astype(float)
    sizes = np.array([len(r) for r in rings])
    starts = np.concatenate([[0], np.cumsum(sizes)[:-1]])
    following = np.arange(len(points)) + 1
    following[starts + sizes - 1] = starts  # each ring's last point goes back to its first
    nxt = points[following]
    cross = points[:, 0] * nxt[:, 1] - nxt[:, 0] * points[:, 1]
    length = np.hypot(nxt[:, 0] - points[:, 0], nxt[:, 1] - points[:, 1])
    ring_area = np.abs(np.add.reduceat(cross, starts)) / 2
    ring_length = np.add.reduceat(length, starts)
    np.add.at(areas, owner, ring_area * np.array(sign))
    np.add.at(perimeters, owner, ring_length)
    return areas, perimeters


def _ring_area(points: IntRing) -> int | float:
    """The area a ring encloses (the shoelace sum halved; always ≥ 0)."""
    if len(points) < 3:
        return 0
    x, y = points[:, 0], points[:, 1]
    twice = abs(int(np.dot(x, np.roll(y, -1))) - int(np.dot(np.roll(x, -1), y)))
    return twice // 2 if twice % 2 == 0 else twice / 2


def _ring_length(points: IntRing) -> float:
    if len(points) < 2:
        return 0.0
    step = np.roll(points, -1, axis=0) - points
    return float(np.hypot(step[:, 0], step[:, 1]).sum())
