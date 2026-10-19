"""Annular sector: a ring, a disc, or part of one."""

from __future__ import annotations

from typing import ClassVar, Literal

from mems_sketch.core.levels import LEVEL
from mems_sketch.core.shapes.base import Primitive, Value


class ArcShape(Primitive):
    """Annular sector on ``layer`` centred at (``x``, ``y``), between ``inner_radius``
    (0: a disc or pie slice) and ``outer_radius``, from ``start_angle`` to
    ``end_angle`` (degrees, counter-clockwise from +x; a ring when they span
    360°). ``segments``: straight edges for a full circle in exports (default:
    within the curve tolerance)."""

    icon: ClassVar[str] = "arc"

    kind: Literal["arc"] = "arc"
    layer: str = LEVEL  # see mems_sketch.core.levels
    x: Value = 0.0
    y: Value = 0.0
    inner_radius: Value = 0.0
    outer_radius: Value
    start_angle: Value = 0.0
    end_angle: Value = 360.0
    segments: Value | None = None  # for a full circle; scaled by the swept angle

    def moved(self, x, y, inner) -> dict:
        return {"x": x(self.x), "y": y(self.y)}

    @classmethod
    def default(cls, layer: str) -> ArcShape:
        return cls(layer=layer, inner_radius=20, outer_radius=30, end_angle=180)
