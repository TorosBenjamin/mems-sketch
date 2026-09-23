"""Children's corners rounded."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

from mems_sketch.core.component import Geometry, to_dbu
from mems_sketch.core.shapes.base import Operation, Point, RenderContext, Value
from mems_sketch.core.shapes.geometry import ARC_TOLERANCE_UM, segments

if TYPE_CHECKING:
    from mems_sketch.core.shapes.registry import Shape


class FilletShape(Operation):
    """Round corners: ``radius`` for convex corners, ``inner_radius`` for concave ones."""

    icon: ClassVar[str] = "fillet"
    wraps: ClassVar[tuple[str, ...]] = ("fillet",)

    kind: Literal["fillet"] = "fillet"
    children: list[Shape]
    radius: Value = 0.0
    inner_radius: Value = 0.0
    segments: Value | None = None  # per full circle; default from ARC_TOLERANCE_UM

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        r_out, r_in = ctx.ev(self.radius), ctx.ev(self.inner_radius)
        if r_out < 0 or r_in < 0:
            raise ValueError("fillet radii must not be negative")
        n = segments(max(r_out, r_in, ARC_TOLERANCE_UM), self.segments and ctx.ev(self.segments))
        geometry = Geometry()
        for layer, region in ctx.children([self.children])[0].layers.items():
            geometry.layers[layer] = region.merged().rounded_corners(to_dbu(r_in), to_dbu(r_out), n)
        return geometry, {}

    def summary(self) -> str:
        return f"fillet {self.radius}"

    @classmethod
    def wrap(cls, op: str, name: str, nodes: list[Shape]) -> FilletShape:
        return cls(name=name, radius=1.0, children=nodes)
