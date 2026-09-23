"""Rectangle between two corners."""

from __future__ import annotations

from typing import ClassVar, Literal

from mems_sketch.core.component import Geometry
from mems_sketch.core.shapes.base import Point, Primitive, RenderContext, Value


class RectShape(Primitive):
    icon: ClassVar[str] = "rect"

    kind: Literal["rect"] = "rect"
    layer: str
    x0: Value
    y0: Value
    x1: Value
    y1: Value

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        geometry = Geometry()
        geometry.add_rect(
            self.layer, ctx.ev(self.x0), ctx.ev(self.y0), ctx.ev(self.x1), ctx.ev(self.y1)
        )
        return geometry, {}

    def moved(self, x, y, inner) -> dict:
        return {"x0": x(self.x0), "x1": x(self.x1), "y0": y(self.y0), "y1": y(self.y1)}

    @classmethod
    def default(cls, layer: str) -> RectShape:
        return cls(layer=layer, x0=0, y0=0, x1=100, y1=50)
