"""Instance of another component."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field

from mems_sketch.core.expressions import evaluate
from mems_sketch.core.shapes.base import Node, Value
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
