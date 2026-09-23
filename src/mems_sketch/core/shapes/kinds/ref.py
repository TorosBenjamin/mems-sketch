"""Instance of another component."""

from __future__ import annotations

from typing import ClassVar, Literal

import klayout.db as kdb
from pydantic import Field

from mems_sketch.core.component import Geometry, resolve_params
from mems_sketch.core.expressions import evaluate
from mems_sketch.core.shapes.base import Node, Point, RenderContext, Value
from mems_sketch.core.shapes.geometry import apply_transform, to_ictrans


class RefShape(Node):
    """An instance of a built-in or user-defined component."""

    category: ClassVar[str] = "reference"
    icon: ClassVar[str] = "component"
    placed: ClassVar[bool] = True

    kind: Literal["ref"] = "ref"
    component: str
    params: dict[str, Value] = Field(default_factory=dict)
    x: Value = 0.0
    y: Value = 0.0
    rotation: Value = 0.0  # degrees, counter-clockwise
    mirror_x: bool = False

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        child = ctx.lookup(self.component)
        child.check_placement(self.params)
        built, points = child.compile(resolve_params(child, self.params, ctx.variables))
        transform = self.placement(ctx.variables)
        geometry = Geometry()
        geometry.merge(built, to_ictrans(transform))
        return geometry, {name: apply_transform(transform, p) for name, p in points.items()}

    def moved(self, x, y, inner) -> dict:
        return {"x": x(self.x), "y": y(self.y)}

    def placement(self, variables: dict[str, float]) -> kdb.DCplxTrans:
        return kdb.DCplxTrans(
            1.0,
            evaluate(self.rotation, variables),
            self.mirror_x,
            evaluate(self.x, variables),
            evaluate(self.y, variables),
        )

    def summary(self) -> str:
        return self.component
