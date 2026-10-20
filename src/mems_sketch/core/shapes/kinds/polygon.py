"""Closed polygon through its points."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field

from mems_sketch.core.levels import LEVEL
from mems_sketch.core.shapes.base import Primitive, Value
from mems_sketch.core.shapes.handles import (
    Dimension,
    Handle,
    coordinate_dimensions,
    point_index,
    points_moved,
    vertex_handles,
    vertex_removed,
)


class PolygonShape(Primitive):
    """Closed polygon on ``layer`` through ``points`` (three or more ``[x, y]``,
    in order; the last joins the first). Straight edges only: round corners
    with a corners modifier, or use a path, arc or circle for curves."""

    icon: ClassVar[str] = "polygon"

    kind: Literal["polygon"] = "polygon"
    layer: str = LEVEL  # see mems_sketch.core.levels
    points: list[tuple[Value, Value]] = Field(min_length=3)

    def moved(self, x, y, inner) -> dict:
        return {"points": [(x(px), y(py)) for px, py in self.points]}

    def edit_handles(self, v) -> list[Handle]:
        """A handle on each vertex (``point.0``, ...) and in the middle of each side."""
        return vertex_handles(self.points, v, closed=True)

    def dimensions(self, v) -> list[Dimension]:
        """Its vertices' coordinates (see :func:`coordinate_dimensions`)."""
        return coordinate_dimensions(self.points, v)

    def corner_vertices(self, v) -> list[tuple[float, float]]:
        return [(v(x), v(y)) for x, y in self.points]

    def handle_moved(self, key, to, v) -> dict:
        points = points_moved(self.points, key, to, v, closed=True)
        if points is None:
            raise KeyError(f"a polygon has no handle '{key}'")
        return {"points": points}

    def handle_removed(self, key) -> dict | None:
        index = point_index(key)
        points = None if index is None else vertex_removed(self.points, index, fewest=3)
        return None if points is None else {"points": points}

    @classmethod
    def default(cls, layer: str) -> PolygonShape:
        return cls(layer=layer, points=[(0, 0), (60, 0), (30, 50)])
