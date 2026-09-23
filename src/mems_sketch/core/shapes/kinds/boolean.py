"""Boolean of two child lists, per layer."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

from mems_sketch.core.component import Geometry
from mems_sketch.core.shapes.base import Operation, Point, RenderContext
from mems_sketch.core.shapes.geometry import boolean_op

if TYPE_CHECKING:
    from mems_sketch.core.shapes.registry import Shape


class BooleanShape(Operation):
    icon: ClassVar[str] = "union"
    child_fields: ClassVar[tuple[str, ...]] = ("a", "b")
    wraps: ClassVar[tuple[str, ...]] = ("union", "subtract", "intersect", "xor")

    kind: Literal["boolean"] = "boolean"
    op: Literal["union", "subtract", "intersect", "xor"]
    a: list[Shape]
    b: list[Shape]

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        a, b = ctx.children([self.a, self.b])
        return boolean_op(self.op, a, b), {}

    def summary(self) -> str:
        return self.op

    def icon_name(self) -> str:
        return self.op

    @classmethod
    def wrap(cls, op: str, name: str, nodes: list[Shape]) -> BooleanShape:
        if len(nodes) < 2:
            raise ValueError(f"{op} needs at least two selected shapes")
        return cls(name=name, op=op, a=nodes[:1], b=nodes[1:])
