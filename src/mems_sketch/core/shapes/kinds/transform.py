"""Children moved, rotated, mirrored and scaled as one piece."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

import klayout.db as kdb
from pydantic import Field, field_validator

from mems_sketch.core.component import Geometry
from mems_sketch.core.expressions import evaluate
from mems_sketch.core.shapes.base import Operation, Point, RenderContext, Value
from mems_sketch.core.shapes.geometry import to_ictrans

if TYPE_CHECKING:
    from mems_sketch.core.shapes.registry import Shape


class TransformShape(Operation):
    """Mirror ``children`` about x, scale, rotate and move them as one piece.

    For something reusable, make a component instead; a transform is for
    moving a few shapes together once. Files written before the rename used
    ``kind: group``, which is still read.
    """

    icon: ClassVar[str] = "transform"
    placed: ClassVar[bool] = True
    wraps: ClassVar[tuple[str, ...]] = ("transform", "group")

    kind: Literal["transform", "group"] = "transform"
    children: list[Shape] = Field(default_factory=list)
    x: Value = 0.0
    y: Value = 0.0
    rotation: Value = 0.0
    mirror_x: bool = False
    scale: Value = 1.0

    @field_validator("kind")
    @classmethod
    def _current_kind(cls, kind: str) -> str:
        return "transform"

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        if ctx.ev(self.scale) <= 0:
            raise ValueError("transform scale must be positive")
        transform = self.placement(ctx.variables)
        inverse = transform.inverted()
        inner_scope = {k: p.seen_through(inverse) for k, p in ctx.scope.items()}
        (inner,) = ctx.children([self.children], inner_scope)
        geometry = Geometry()
        geometry.merge(inner, to_ictrans(transform))
        return geometry, {}

    def moved(self, x, y, inner) -> dict:
        return {"x": x(self.x), "y": y(self.y)}  # the children stay in their own frame

    def placement(self, variables: dict[str, float]) -> kdb.DCplxTrans:
        return kdb.DCplxTrans(
            evaluate(self.scale, variables),
            evaluate(self.rotation, variables),
            self.mirror_x,
            evaluate(self.x, variables),
            evaluate(self.y, variables),
        )

    @classmethod
    def wrap(cls, op: str, name: str, nodes: list[Shape]) -> TransformShape:
        return cls(name=name, children=nodes)
