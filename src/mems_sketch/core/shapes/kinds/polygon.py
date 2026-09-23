"""Closed polygon through its points."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field

from mems_sketch.core.component import Geometry
from mems_sketch.core.shapes.base import Point, Primitive, RenderContext, Value


class PolygonShape(Primitive):
    icon: ClassVar[str] = "polygon"

    kind: Literal["polygon"] = "polygon"
    layer: str
    points: list[tuple[Value, Value]] = Field(min_length=3)

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        geometry = Geometry()
        geometry.add_polygon(self.layer, [(ctx.ev(x), ctx.ev(y)) for x, y in self.points])
        return geometry, {}

    def moved(self, x, y, inner) -> dict:
        return {"points": [(x(px), y(py)) for px, py in self.points]}

    @classmethod
    def default(cls, layer: str) -> PolygonShape:
        return cls(layer=layer, points=[(0, 0), (60, 0), (30, 50)])
