"""Wire of constant width along a centreline."""

from __future__ import annotations

import math
from typing import ClassVar, Literal

from pydantic import Field

from mems_sketch.core.levels import LEVEL
from mems_sketch.core.shapes.base import Primitive, Value
from mems_sketch.core.shapes.handles import (
    WIDTH,
    Handle,
    point_index,
    rounded,
    shifted,
    vertex_handles,
    vertex_moved,
)


class PathShape(Primitive):
    """A wire of constant ``width`` on ``layer`` along a centreline through
    ``points`` (two or more ``[x, y]``), e.g. a beam or a trace. ``ends``:
    ``flush`` (cut at the end points), ``square`` (extended by half the width)
    or ``round``. Its ports ``start`` and ``end`` are at its ends, pointing out."""

    icon: ClassVar[str] = "path"

    kind: Literal["path"] = "path"
    layer: str = LEVEL  # see mems_sketch.core.levels
    points: list[tuple[Value, Value]] = Field(min_length=2)
    width: Value
    ends: Literal["flush", "square", "round"] = "flush"

    def moved(self, x, y, inner) -> dict:
        return {"points": [(x(px), y(py)) for px, py in self.points]}

    def edit_handles(self, v) -> list[Handle]:
        """A handle on each vertex, and the width on the first segment's side."""
        handles = vertex_handles(self.points, v)
        side = self._side(v)
        if side is not None:
            (mx, my), (nx, ny) = side
            half = v(self.width) / 2
            handles.append(Handle("width", mx + nx * half, my + ny * half, WIDTH))
        return handles

    def handle_moved(self, key, to, v) -> dict:
        if key == "width":
            side = self._side(v)
            if side is None:
                raise KeyError("the path's first segment has no length")
            (mx, my), (nx, ny) = side
            across = abs((to[0] - mx) * nx + (to[1] - my) * ny)
            return {"width": shifted(self.width, rounded(2 * across - v(self.width)))}
        index = point_index(key)
        if index is None or index >= len(self.points):
            raise KeyError(f"a path has no handle '{key}'")
        return {"points": vertex_moved(self.points, index, to, v)}

    def _side(self, v):
        """The first segment's middle and its left normal, or None if it has no length."""
        (ax, ay), (bx, by) = [(v(x), v(y)) for x, y in self.points[:2]]
        length = math.hypot(bx - ax, by - ay)
        if length == 0:
            return None
        return ((ax + bx) / 2, (ay + by) / 2), (-(by - ay) / length, (bx - ax) / length)

    @classmethod
    def default(cls, layer: str) -> PathShape:
        return cls(layer=layer, points=[(0, 0), (100, 0), (100, 60)], width=4)
