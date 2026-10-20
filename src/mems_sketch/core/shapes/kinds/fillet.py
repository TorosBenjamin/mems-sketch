"""Children's corners rounded."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

from mems_sketch.core.shapes.base import Operation, Value

if TYPE_CHECKING:
    from mems_sketch.core.shapes.registry import Shape


class FilletShape(Operation):
    """Round every corner of its ``children``: ``radius`` for convex corners,
    ``inner_radius`` for concave ones (0: sharp). ``segments`` per quarter circle in
    exports (default: within the curve tolerance). For chosen corners only, use a
    corners modifier.

    Fillet or connection? A fillet rounds whatever its children make, however
    they were drawn (overlapping beams, a polygon); a connection fills in where
    beams' ends meet, from their ports, without the beams overlapping, and
    checks each end lies on its beam."""

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
