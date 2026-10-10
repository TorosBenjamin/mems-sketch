"""Circle, approximated by segments."""

from __future__ import annotations

from typing import ClassVar, Literal

from mems_sketch.core.levels import LEVEL
from mems_sketch.core.shapes.base import Primitive, Value


class CircleShape(Primitive):
    icon: ClassVar[str] = "circle"

    kind: Literal["circle"] = "circle"
    layer: str = LEVEL  # see mems_sketch.core.levels
    x: Value = 0.0
    y: Value = 0.0
    radius: Value
    segments: Value | None = None  # default: from ARC_TOLERANCE_UM

    def moved(self, x, y, inner) -> dict:
        return {"x": x(self.x), "y": y(self.y)}

    @classmethod
    def default(cls, layer: str) -> CircleShape:
        return cls(layer=layer, radius=25)
