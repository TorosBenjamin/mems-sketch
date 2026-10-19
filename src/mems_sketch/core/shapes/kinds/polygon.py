"""Closed polygon through its points."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field

from mems_sketch.core.levels import LEVEL
from mems_sketch.core.shapes.base import Primitive, Value
from mems_sketch.core.shapes.handles import Handle, point_index, vertex_handles, vertex_moved


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
        """A handle on each vertex (``point.0``, ``point.1``, ...)."""
        return vertex_handles(self.points, v)

    def handle_moved(self, key, to, v) -> dict:
        index = point_index(key)
        if index is None or index >= len(self.points):
            raise KeyError(f"a polygon has no handle '{key}'")
        return {"points": vertex_moved(self.points, index, to, v)}

    @classmethod
    def default(cls, layer: str) -> PolygonShape:
        return cls(layer=layer, points=[(0, 0), (60, 0), (30, 50)])
