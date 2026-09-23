"""Alignment points of evaluated nodes, and the points expressions use."""

from __future__ import annotations

import functools
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any

import klayout.db as kdb
from pydantic import BaseModel

from mems_sketch.core.component import DBU_UM, Geometry
from mems_sketch.core.expressions import ExpressionError, names_in
from mems_sketch.core.shapes.base import Point
from mems_sketch.core.shapes.tree import walk

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


class NodePoints:
    """The alignment points of one evaluated node, in the frame of the list holding it.

    ``declared`` are the points of a component reference (already placed);
    bounding-box points come from the geometry and are computed on demand.
    ``transform`` maps them into another frame (e.g. inside a transform node).
    """

    def __init__(
        self,
        name: str,
        geometry: Geometry,
        declared: Mapping[str, Point],
        transform: kdb.DCplxTrans | None = None,
    ) -> None:
        self.name = name
        self.geometry = geometry
        self.declared = dict(declared)
        self.transform = transform

    def names(self) -> list[str]:
        return [*self.declared, *BBOX_POINTS]

    def point(self, point: str) -> Point:
        if point in self.declared:
            x, y = self.declared[point]
        elif point in BBOX_POINTS:
            x, y = _bbox_point(self.geometry, point, self.name)
        else:
            raise ValueError(f"shape '{self.name}' has no point '{point}'")
        if self.transform is None:
            return x, y
        p = self.transform * kdb.DPoint(x, y)
        return p.x, p.y

    def seen_through(self, transform: kdb.DCplxTrans) -> NodePoints:
        """The same points, mapped by ``transform`` (applied after any existing one)."""
        combined = transform if self.transform is None else transform * self.transform
        return NodePoints(self.name, self.geometry, self.declared, combined)


def _bbox_point(geometry: Geometry, point: str, name: str) -> Point:
    box = kdb.Box()
    for region in geometry.layers.values():
        box += region.bbox()
    if box.empty():
        raise ValueError(f"shape '{name}' has no geometry to align to")
    x0, y0, x1, y1 = (v * DBU_UM for v in (box.left, box.bottom, box.right, box.top))
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
    """Point coordinates (``node.point.x`` / ``.y``) used by an expression."""
    if expression.count(".") < 2:
        return frozenset()
    try:
        names = names_in(expression)
    except ExpressionError:
        return frozenset()
    return frozenset(n for n in names if n.count(".") == 2 and n.rsplit(".", 1)[1] in "xy")


def point_values(expressions: list[str], scope: Mapping[str, NodePoints]) -> dict[str, float]:
    """Values of the point coordinates used by ``expressions`` that ``scope`` provides."""
    values = {}
    for expression in expressions:
        for name in point_names(expression):
            node, point, axis = name.split(".")
            if node in scope:
                values[name] = scope[node].point(point)[0 if axis == "x" else 1]
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
    """Every string in a node's own fields (its children excluded)."""
    return [
        text
        for field in type(shape).model_fields
        if field not in type(shape).child_fields
        for text in _strings(getattr(shape, field))
    ]


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
