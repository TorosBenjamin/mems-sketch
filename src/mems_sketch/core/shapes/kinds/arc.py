"""Annular sector: a ring, a disc, or part of one."""

from __future__ import annotations

from typing import ClassVar, Literal

import klayout.db as kdb

from mems_sketch.core.component import Geometry, to_dbu
from mems_sketch.core.shapes.base import Point, Primitive, RenderContext, Value
from mems_sketch.core.shapes.geometry import arc_points, segments


class ArcShape(Primitive):
    """Annular sector (a ring when the angles span 360°). Angles in degrees, CCW from +x."""

    icon: ClassVar[str] = "arc"

    kind: Literal["arc"] = "arc"
    layer: str
    x: Value = 0.0
    y: Value = 0.0
    inner_radius: Value = 0.0
    outer_radius: Value
    start_angle: Value = 0.0
    end_angle: Value = 360.0
    segments: Value | None = None  # for a full circle; scaled by the swept angle

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        geometry = Geometry()
        geometry.layers[self.layer] = self._sector(ctx)
        return geometry, {}

    def _sector(self, ctx: RenderContext) -> kdb.Region:
        """Outer pie minus inner pie; with full angles this is a ring or disc."""
        cx, cy = ctx.ev(self.x), ctx.ev(self.y)
        r_in, r_out = ctx.ev(self.inner_radius), ctx.ev(self.outer_radius)
        start, end = ctx.ev(self.start_angle), ctx.ev(self.end_angle)
        if not 0 <= r_in < r_out:
            raise ValueError("arc needs 0 <= inner_radius < outer_radius")
        if end <= start:
            raise ValueError("arc end_angle must be greater than start_angle")
        n = segments(r_out, self.segments and ctx.ev(self.segments))

        def pie(r: float) -> kdb.Region:
            points = arc_points(cx, cy, r, start, end, n)
            if end - start < 360:
                points.append((cx, cy))
            return kdb.Region(kdb.Polygon([kdb.Point(to_dbu(x), to_dbu(y)) for x, y in points]))

        return pie(r_out) - pie(r_in) if r_in > 0 else pie(r_out)

    def moved(self, x, y, inner) -> dict:
        return {"x": x(self.x), "y": y(self.y)}

    @classmethod
    def default(cls, layer: str) -> ArcShape:
        return cls(layer=layer, inner_radius=20, outer_radius=30, end_angle=180)
