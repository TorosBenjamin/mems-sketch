"""Children's outlines grown or shrunk."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

from mems_sketch.core.component import Geometry, to_dbu
from mems_sketch.core.shapes.base import Operation, Point, RenderContext, Value

if TYPE_CHECKING:
    from mems_sketch.core.shapes.registry import Shape


class OffsetShape(Operation):
    """Grow (positive ``distance``) or shrink (negative) the children's outlines."""

    icon: ClassVar[str] = "offset"
    wraps: ClassVar[tuple[str, ...]] = ("offset",)
    cuts: ClassVar[bool] = True

    kind: Literal["offset"] = "offset"
    children: list[Shape]
    distance: Value
    corners: Literal["square", "bevel"] = "square"

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        mode = 2 if self.corners == "square" else 1
        d = to_dbu(ctx.ev(self.distance))
        geometry = Geometry()
        for layer, region in ctx.children([self.children])[0].layers.items():
            geometry.layers[layer] = region.sized(d, mode)
        return geometry, {}

    def summary(self) -> str:
        return f"offset {self.distance}"

    @classmethod
    def wrap(cls, op: str, name: str, nodes: list[Shape]) -> OffsetShape:
        return cls(name=name, distance=1.0, children=nodes)
