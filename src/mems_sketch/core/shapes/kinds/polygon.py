"""Closed polygon through its points."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field

from mems_sketch.core.levels import LEVEL
from mems_sketch.core.shapes.base import Primitive, Value


class PolygonShape(Primitive):
    icon: ClassVar[str] = "polygon"

    kind: Literal["polygon"] = "polygon"
    layer: str = LEVEL  # see mems_sketch.core.levels
    points: list[tuple[Value, Value]] = Field(min_length=3)

    def moved(self, x, y, inner) -> dict:
        return {"points": [(x(px), y(py)) for px, py in self.points]}

    @classmethod
    def default(cls, layer: str) -> PolygonShape:
        return cls(layer=layer, points=[(0, 0), (60, 0), (30, 50)])
