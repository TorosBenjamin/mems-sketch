"""Boolean of two child lists, per layer.

Each operand list is merged first, and everything on a layer is merged in the
end anyway, so there is no union: several shapes on one layer already are one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

from mems_sketch.core.shapes.base import Operation

if TYPE_CHECKING:
    from mems_sketch.core.shapes.registry import Shape


class BooleanShape(Operation):
    """``a`` ``op`` ``b``, layer by layer: ``subtract`` (a without b), ``intersect``
    (where both are) or ``xor`` (where one is). ``a`` and ``b`` are shapes, a map
    by name."""

    icon: ClassVar[str] = "subtract"
    child_fields: ClassVar[tuple[str, ...]] = ("a", "b")
    wraps: ClassVar[tuple[str, ...]] = ("subtract", "intersect", "xor")
    cuts: ClassVar[bool] = True

    kind: Literal["boolean"] = "boolean"
    op: Literal["subtract", "intersect", "xor"]
    a: list[Shape]
    b: list[Shape]

    def summary(self) -> str:
        return self.op

    def operand_labels(self) -> tuple[str, ...]:
        return {
            "subtract": ("Keep", "Cut away"),
            "intersect": ("Shapes", "Only where these are"),
            "xor": ("Shapes", "Except where these are"),
        }[self.op]

    def icon_name(self) -> str:
        return self.op

    @classmethod
    def wrap(cls, op: str, name: str, nodes: list[Shape]) -> BooleanShape:
        if len(nodes) < 2:
            raise ValueError(f"{op} needs at least two selected shapes")
        return cls(name=name, op=op, a=nodes[:1], b=nodes[1:])
