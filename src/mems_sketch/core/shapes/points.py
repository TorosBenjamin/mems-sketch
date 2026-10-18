"""Alignment points of evaluated nodes, and the points expressions use."""

from __future__ import annotations

import functools
import math
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any

import numpy as np
from pydantic import BaseModel

from mems_sketch.core.component import Geometry
from mems_sketch.core.expressions import ExpressionError, names_in
from mems_sketch.core.shapes.base import Point
from mems_sketch.core.shapes.tree import walk
from mems_sketch.core.transform import Transform

if TYPE_CHECKING:
    from mems_sketch.core.shapes.registry import Shape

# Points every node has, from the bounding box of its geometry.
BBOX_POINTS = (
    "center",
    "left",
    "right",
    "top",
    "bottom",
    "top_left",
    "top_right",
    "bottom_left",
    "bottom_right",
)


# Sizes every node has: of its bounding box, and (after ``min.``) of its minimal box.
SIZES = ("width", "height")
MIN_SIZES = ("width", "height", "angle")


PORT_MEASURES = ("angle", "width")  # of a placed component's port: port.drive.angle


def _is_coordinate(path: str) -> bool:
    point, dot, axis = path.rpartition(".")
    return dot == "." and axis in ("x", "y") and _is_point(point)


def _is_point(name: str) -> bool:  # one word, or a placed component's port (port.drive)
    word = name.removeprefix("port.")
    return bool(word) and "." not in word


def _is_port_measure(path: str) -> bool:
    point, dot, measure = path.rpartition(".")
    return (
        dot == "." and measure in PORT_MEASURES and point.startswith("port.") and _is_point(point)
    )


def measure_path(name: str) -> tuple[str, str] | None:
    """``(node, path)`` when ``name`` is a node's measure in an expression:
    ``beam.left.x``, ``beam.width``, ``beam.min.angle``, ``beam.min.top_right.y``;
    a placed component's port: ``comb1.port.drive.x``, ``comb1.port.drive.angle``."""
    node, dot, path = name.partition(".")
    if not node or not dot:
        return None
    rest = path[4:] if path.startswith("min.") else None
    if path in SIZES or _is_coordinate(path) or _is_port_measure(path):
        return node, path
    if rest is not None and (rest in MIN_SIZES or _is_coordinate(rest)):
        return node, path
    return None


class NodePoints:
    """The alignment points of one evaluated node, in the frame of the list holding it.

    ``declared`` are the points of a component reference (already placed);
    bounding-box points come from the geometry and are computed on demand.
    ``transform`` maps them into another frame (e.g. inside a transform node).
    ``min.<point>`` are those of its minimal box: the smallest rectangle around
    it at any angle (see :meth:`measure`).
    """

    def __init__(
        self,
        name: str,
        geometry: Geometry,
        declared: Mapping[str, Point],
        transform: Transform | None = None,
    ) -> None:
        self.name = name
        self.geometry = geometry
        self.declared = dict(declared)
        self.transform = transform

    def names(self) -> list[str]:
        return [*self.declared, *BBOX_POINTS]

    def point(self, point: str) -> Point:
        if point.startswith("min.") and point[4:] in BBOX_POINTS:
            return self._min_point(point[4:])
        if point in self.declared:
            x, y = self.declared[point]
        elif point in BBOX_POINTS:
            x, y = _bbox_point(self.geometry, point, self.name, self.declared)
        else:
            raise ValueError(f"shape '{self.name}' has no point '{point}'")
        if self.transform is None:
            return x, y
        return self.transform.apply(x, y)

    def measure(self, path: str) -> float:
        """A measure (see :func:`measure_path`): a point's coordinate, the size of
        the bounding box (``width``: from ``left`` to ``right``) or of the minimal
        box, or the minimal box's angle (in (-45°, 45°], 0 for ties: a circle or a
        square keeps its level box)."""
        point, _, axis = path.rpartition(".")
        if axis in ("x", "y") and point:
            return self.point(point)[0 if axis == "x" else 1]
        if path == "width":
            return math.dist(self.point("left"), self.point("right"))
        if path == "height":
            return math.dist(self.point("bottom"), self.point("top"))
        if path.startswith("min.") and path[4:] in MIN_SIZES:
            angle, (left, bottom, right, top) = self._minimal()
            return {"angle": angle, "width": right - left, "height": top - bottom}[path[4:]]
        raise ValueError(f"shape '{self.name}' has no measure '{path}'")

    def _minimal(self) -> tuple[float, tuple[float, float, float, float]]:
        """The minimal box in this frame: its angle, and its box in the frame turned by it."""
        from mems_sketch import _geom

        points = _outline_points(self.geometry)
        if not len(points):
            points = np.array([tuple(p) for p in self.declared.values()], dtype=float).reshape(
                -1, 2
            )
        if not len(points):
            raise ValueError(f"shape '{self.name}' has no geometry to measure")
        scale = 1.0
        if self.transform is not None:
            points = np.array([self.transform.apply(x, y) for x, y in points], dtype=float)
            scale = abs(self.transform.mag)
        angle, box = _geom.minimal_box(np.ascontiguousarray(points), _GRID_UM * scale)
        return angle, (box.x0, box.y0, box.x1, box.y1)

    def _min_point(self, point: str) -> Point:
        angle, (left, bottom, right, top) = self._minimal()
        u = left if "left" in point else right if "right" in point else (left + right) / 2
        v = bottom if "bottom" in point else top if "top" in point else (bottom + top) / 2
        a = math.radians(angle)
        return u * math.cos(a) - v * math.sin(a), u * math.sin(a) + v * math.cos(a)

    def seen_through(self, transform: Transform) -> NodePoints:
        """The same points, mapped by ``transform`` (applied after any existing one)."""
        combined = transform if self.transform is None else transform * self.transform
        return NodePoints(self.name, self.geometry, self.declared, combined)


_GRID_UM = 0.001  # the polygons are on the 1 nm grid


def _outline_points(geometry: Geometry) -> np.ndarray:
    """The vertices of the outer boundaries on every layer, in µm, as (n, 2)."""
    rings = [polygon.hull for region in geometry.layers.values() for polygon in region.each()]
    if not rings:
        return np.zeros((0, 2))
    return np.concatenate([np.asarray(r, dtype=float) for r in rings]) * _GRID_UM


def _bbox_point(
    geometry: Geometry, point: str, name: str, declared: Mapping[str, Point] | None = None
) -> Point:
    box = geometry.bbox()
    if box is None and declared:  # nothing drawn (a guide): the box of its own points
        xs_, ys_ = [p[0] for p in declared.values()], [p[1] for p in declared.values()]
        x0, y0, x1, y1 = min(xs_), min(ys_), max(xs_), max(ys_)
    elif box is None:
        raise ValueError(f"shape '{name}' has no geometry to align to")
    else:
        x0, y0, x1, y1 = box
    xs = {"left": x0, "right": x1}
    ys = {"bottom": y0, "top": y1}
    vertical, _, horizontal = point.partition("_")
    if point == "center":
        return (x0 + x1) / 2, (y0 + y1) / 2
    if point in xs:
        return xs[point], (y0 + y1) / 2
    if point in ys:
        return (x0 + x1) / 2, ys[point]
    return xs[horizontal], ys[vertical]


@functools.lru_cache(maxsize=65536)
def point_names(expression: str) -> frozenset[str]:
    """Nodes' measures used by an expression: point coordinates (``node.point.x``),
    sizes (``node.width``) and those of minimal boxes (``node.min.angle``)."""
    if "." not in expression:
        return frozenset()
    try:
        names = names_in(expression)
    except ExpressionError:
        return frozenset()
    return frozenset(n for n in names if measure_path(n) is not None)


def point_values(expressions: list[str], scope: Mapping[str, NodePoints]) -> dict[str, float]:
    """Values of the measures used by ``expressions`` that ``scope`` provides."""
    values = {}
    for expression in expressions:
        for name in point_names(expression):
            node, path = measure_path(name)
            if node in scope:
                values[name] = scope[node].measure(path)
    return values


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, BaseModel):
        for field in type(value).model_fields:
            yield from _strings(getattr(value, field))
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def own_strings(shape: Shape) -> list[str]:
    """Every string in a node's own fields (its children excluded), plus the point
    coordinates its modifiers need (e.g. a mirror's ``about``)."""
    texts = [
        text
        for field in type(shape).model_fields
        if field not in type(shape).child_fields
        for text in _strings(getattr(shape, field))
    ]
    for modifier in shape.modifiers:
        texts += [n for n in modifier.point_references() if not n.startswith("self.")]
    return texts


def point_dependencies(shape: Shape) -> set[str]:
    """Names of the nodes whose points ``shape`` or its subtree uses."""
    found = set()
    for node in walk([shape]):
        if not node.enabled:
            continue
        if node.align is not None:
            found.add(node.align.to.partition(".")[0])
        for text in own_strings(node):
            found.update(name.partition(".")[0] for name in point_names(text))
    return found
