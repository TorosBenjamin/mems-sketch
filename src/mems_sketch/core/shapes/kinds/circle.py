"""Circle, approximated by segments."""

from __future__ import annotations

import math
from typing import ClassVar, Literal

from mems_sketch.core.levels import LEVEL
from mems_sketch.core.shapes.base import Primitive, Value
from mems_sketch.core.shapes.handles import RADIUS, Handle, rounded, shifted


class CircleShape(Primitive):
    """Circle on ``layer`` centred at (``x``, ``y``) with ``radius``. ``segments``
    sets how many straight edges approximate it in exports (default: enough to
    stay within the curve tolerance)."""

    icon: ClassVar[str] = "circle"

    kind: Literal["circle"] = "circle"
    layer: str = LEVEL  # see mems_sketch.core.levels
    x: Value = 0.0
    y: Value = 0.0
    radius: Value
    segments: Value | None = None  # default: from ARC_TOLERANCE_UM

    def moved(self, x, y, inner) -> dict:
        return {"x": x(self.x), "y": y(self.y)}

    def edit_handles(self, v) -> list[Handle]:
        """The radius, on the rim to the right and at the top."""
        x, y, r = v(self.x), v(self.y), v(self.radius)
        return [
            Handle("radius", x + r, y, RADIUS, ("radial", x, y, 0.0)),
            Handle("radius.top", x, y + r, RADIUS, ("radial", x, y, 90.0)),
        ]

    def handle_moved(self, key, to, v) -> dict:
        if key not in ("radius", "radius.top"):
            raise KeyError(f"a circle has no handle '{key}'")
        reach = math.hypot(to[0] - v(self.x), to[1] - v(self.y))
        return {"radius": shifted(self.radius, rounded(reach - v(self.radius)))}

    @classmethod
    def default(cls, layer: str) -> CircleShape:
        return cls(layer=layer, radius=25)
