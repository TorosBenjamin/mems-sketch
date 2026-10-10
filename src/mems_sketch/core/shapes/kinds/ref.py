"""Instance of another component."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field

from mems_sketch.core.component import Geometry, resolve_params
from mems_sketch.core.expressions import evaluate
from mems_sketch.core.shapes.base import Node, Point, RenderContext, Value
from mems_sketch.core.shapes.geometry import apply_transform
from mems_sketch.core.transform import Transform


class RefShape(Node):
    """An instance of a built-in or user-defined component.

    ``level`` puts it on a level of the layer stack (``poly2``, or relative:
    ``level+1``); left out, it is on its own default level if it declares one,
    else on the level of the component placing it (mems_sketch.core.levels).
    """

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
    level: str | None = None

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        child = ctx.lookup(self.component)
        child.check_placement(self.params)
        try:
            level = ctx.stack.place(self.level, child.default_level, ctx.level)
        except ValueError as error:
            raise ValueError(f"'{self.name or self.component}': {error}") from None
        params = resolve_params(child, self.params, ctx.variables)
        built, points = child.compile(params, level)
        transform = self.placement(ctx.variables)
        geometry = Geometry()
        geometry.merge(built, transform)
        return geometry, {name: apply_transform(transform, p) for name, p in points.items()}

    def moved(self, x, y, inner) -> dict:
        return {"x": x(self.x), "y": y(self.y)}

    def placement(self, variables: dict[str, float]) -> Transform:
        return Transform(
            evaluate(self.x, variables),
            evaluate(self.y, variables),
            evaluate(self.rotation, variables),
            self.mirror_x,
        )

    def summary(self) -> str:
        return self.component
