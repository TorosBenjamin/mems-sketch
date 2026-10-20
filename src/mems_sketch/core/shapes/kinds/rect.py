"""Rectangle between two corners, its corners rounded by ``corner_radius``.

The radius is clamped to half the shorter side: at that it is a circle (a
square) or a stadium, built as such (exact arcs, no zero-length edges).
"""

from __future__ import annotations

from typing import ClassVar, Literal

from mems_sketch.core.levels import LEVEL
from mems_sketch.core.shapes.base import Primitive, Value
from mems_sketch.core.shapes.handles import CORNER, EDGE, Dimension, Handle, shifted


class RectShape(Primitive):
    """Rectangle from corner (``x0``, ``y0``) to the opposite one (``x1``, ``y1``),
    on ``layer``; ``corner_radius`` rounds all four corners."""

    icon: ClassVar[str] = "rect"

    kind: Literal["rect"] = "rect"
    layer: str = LEVEL  # see mems_sketch.core.levels
    x0: Value
    y0: Value
    x1: Value
    y1: Value
    corner_radius: Value = 0.0

    def moved(self, x, y, inner) -> dict:
        return {"x0": x(self.x0), "x1": x(self.x1), "y0": y(self.y0), "y1": y(self.y1)}

    def edit_handles(self, v) -> list[Handle]:
        """A handle on each side (``x0``: the side at ``x0``) and corner (``x0 y1``)."""
        x0, y0, x1, y1 = v(self.x0), v(self.y0), v(self.x1), v(self.y1)
        xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
        return [
            Handle("x0", x0, ym, EDGE, ("x",)),
            Handle("x1", x1, ym, EDGE, ("x",)),
            Handle("y0", xm, y0, EDGE, ("y",)),
            Handle("y1", xm, y1, EDGE, ("y",)),
            *(
                Handle(f"{kx} {ky}", x, y, CORNER)
                for kx, x in (("x0", x0), ("x1", x1))
                for ky, y in (("y0", y0), ("y1", y1))
            ),
        ]

    def dimensions(self, v) -> list[Dimension]:
        """``x1`` above it and ``x0`` below, ``y1`` on its left and ``y0`` on its right,
        each from the axis to its side."""
        x0, y0, x1, y1 = v(self.x0), v(self.y0), v(self.x1), v(self.y1)
        top, bottom, left, right = max(y0, y1), min(y0, y1), min(x0, x1), max(x0, x1)
        found = []
        for field, x, at, side in (("x1", x1, top, (0.0, 1.0)), ("x0", x0, bottom, (0.0, -1.0))):
            if isinstance(getattr(self, field), str):
                found.append(Dimension(field, getattr(self, field), x, (0.0, at), (x, at), side))
        for field, y, at, side in (("y1", y1, left, (-1.0, 0.0)), ("y0", y0, right, (1.0, 0.0))):
            if isinstance(getattr(self, field), str):
                found.append(Dimension(field, getattr(self, field), y, (at, 0.0), (at, y), side))
        return found

    def corner_vertices(self, v) -> list[tuple[float, float]]:
        x0, y0, x1, y1 = v(self.x0), v(self.y0), v(self.x1), v(self.y1)
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

    def handle_moved(self, key, to, v) -> dict:
        changed = {}
        for field in key.split():
            if field not in ("x0", "x1", "y0", "y1"):
                raise KeyError(f"a rectangle has no handle '{key}'")
            value = getattr(self, field)
            changed[field] = shifted(value, to[0 if field[0] == "x" else 1] - v(value))
        return changed

    @classmethod
    def default(cls, layer: str) -> RectShape:
        return cls(layer=layer, x0=0, y0=0, x1=100, y1=50)
