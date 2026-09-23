"""Circle, approximated by segments."""

from __future__ import annotations

from typing import ClassVar, Literal

from mems_sketch.core.component import Geometry
from mems_sketch.core.shapes.base import Point, Primitive, RenderContext, Value
from mems_sketch.core.shapes.geometry import arc_points, segments


class CircleShape(Primitive):
    icon: ClassVar[str] = "circle"

    kind: Literal["circle"] = "circle"
    layer: str
    x: Value = 0.0
    y: Value = 0.0
    radius: Value
    segments: Value | None = None  # default: from ARC_TOLERANCE_UM

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        r = ctx.ev(self.radius)
        n = segments(r, self.segments and ctx.ev(self.segments))
        geometry = Geometry()
        geometry.add_polygon(self.layer, arc_points(ctx.ev(self.x), ctx.ev(self.y), r, 0, 360, n))
        return geometry, {}

    def moved(self, x, y, inner) -> dict:
        return {"x": x(self.x), "y": y(self.y)}

    @classmethod
    def default(cls, layer: str) -> CircleShape:
        return cls(layer=layer, radius=25)
