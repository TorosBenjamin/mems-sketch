"""Annular sector: a ring, a disc, or part of one."""

from __future__ import annotations

import math
from typing import ClassVar, Literal

from mems_sketch.core.levels import LEVEL
from mems_sketch.core.shapes.base import Primitive, Value
from mems_sketch.core.shapes.handles import (
    ANGLE,
    RADIUS,
    Handle,
    angle_of,
    on_circle,
    rounded,
    shifted,
    turn,
)


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

    def edit_handles(self, v) -> list[Handle]:
        """The radii, half way round; the end angles, half way across (none on a full
        ring); the inner radius only when there is one."""
        center = v(self.x), v(self.y)
        start, end = v(self.start_angle), v(self.end_angle)
        inner, outer = v(self.inner_radius), v(self.outer_radius)
        middle = (start + end) / 2
        radial = ("radial", *center, middle)
        handles = [Handle("outer_radius", *on_circle(center, outer, middle), RADIUS, radial)]
        if inner > 0:
            at = on_circle(center, inner, middle)
            handles.append(Handle("inner_radius", *at, RADIUS, radial))
        if abs(end - start) < 360:
            across = (inner + outer) / 2
            turning = ("angle", *center, across)
            handles += [
                Handle("start_angle", *on_circle(center, across, start), ANGLE, turning),
                Handle("end_angle", *on_circle(center, across, end), ANGLE, turning),
            ]
        return handles

    def handle_moved(self, key, to, v) -> dict:
        center = v(self.x), v(self.y)
        if key in ("outer_radius", "inner_radius"):
            value = getattr(self, key)
            reach = math.hypot(to[0] - center[0], to[1] - center[1])
            return {key: shifted(value, rounded(reach - v(value)))}
        if key in ("start_angle", "end_angle"):
            value = getattr(self, key)
            return {key: shifted(value, rounded(turn(v(value), angle_of(center, to))))}
        raise KeyError(f"an arc has no handle '{key}'")

    @classmethod
    def default(cls, layer: str) -> ArcShape:
        return cls(layer=layer, inner_radius=20, outer_radius=30, end_angle=180)
