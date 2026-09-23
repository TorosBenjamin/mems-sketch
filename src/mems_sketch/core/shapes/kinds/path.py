"""Wire of constant width along a centreline."""

from __future__ import annotations

from typing import ClassVar, Literal

import klayout.db as kdb
from pydantic import Field

from mems_sketch.core.component import Geometry, to_dbu
from mems_sketch.core.shapes.base import Point, Primitive, RenderContext, Value


class PathShape(Primitive):
    """A wire of constant ``width`` along a centreline, e.g. a beam or a trace."""

    icon: ClassVar[str] = "path"

    kind: Literal["path"] = "path"
    layer: str
    points: list[tuple[Value, Value]] = Field(min_length=2)
    width: Value
    ends: Literal["flush", "square", "round"] = "flush"

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        points = [kdb.Point(to_dbu(ctx.ev(x)), to_dbu(ctx.ev(y))) for x, y in self.points]
        width = to_dbu(ctx.ev(self.width))
        if width <= 0:
            raise ValueError("path width must be positive")
        ext = 0 if self.ends == "flush" else width // 2
        geometry = Geometry()
        geometry.region(self.layer).insert(kdb.Path(points, width, ext, ext, self.ends == "round"))
        return geometry, {}

    def moved(self, x, y, inner) -> dict:
        return {"points": [(x(px), y(py)) for px, py in self.points]}

    @classmethod
    def default(cls, layer: str) -> PathShape:
        return cls(layer=layer, points=[(0, 0), (100, 0), (100, 60)], width=4)
