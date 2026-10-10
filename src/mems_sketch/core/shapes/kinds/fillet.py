"""Children's corners rounded."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

from mems_sketch.core.shapes.base import Operation, Value

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

    def summary(self) -> str:
        return f"fillet {self.radius}"

    @classmethod
    def wrap(cls, op: str, name: str, nodes: list[Shape]) -> FilletShape:
        return cls(name=name, radius=1.0, children=nodes)
