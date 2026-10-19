"""Rectangle between two corners, its corners rounded by ``corner_radius``.

The radius is clamped to half the shorter side: at that it is a circle (a
square) or a stadium, built as such (exact arcs, no zero-length edges).
"""

from __future__ import annotations

from typing import ClassVar, Literal

from mems_sketch.core.levels import LEVEL
from mems_sketch.core.shapes.base import Primitive, Value


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

    @classmethod
    def default(cls, layer: str) -> RectShape:
        return cls(layer=layer, x0=0, y0=0, x1=100, y1=50)
