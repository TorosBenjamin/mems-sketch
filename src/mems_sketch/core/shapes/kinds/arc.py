"""Annular sector: a ring, a disc, or part of one."""

from __future__ import annotations

from typing import ClassVar, Literal

from mems_sketch.core.levels import LEVEL
from mems_sketch.core.shapes.base import Primitive, Value


class ArcShape(Primitive):
    """Annular sector (a ring when the angles span 360°). Angles in degrees, CCW from +x."""

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
