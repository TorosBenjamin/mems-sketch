"""Guide: a construction line that draws nothing.

A guide helps build the layout: a mirror modifier can mirror across it
(``about: centerline``), and its points (``start``, ``end``, ``center`` and
the corners of the box around it) can be aligned to and used in expressions,
like any shape's. It can itself be aligned, e.g. its centre to
``mass.center``, so the line follows the design. It has no layer: it is not
exported and not checked, and it adds nothing to the geometry.
"""

from __future__ import annotations

import math
from typing import ClassVar, Literal

from mems_sketch.core.shapes.base import Node, Value
from mems_sketch.core.shapes.handles import VERTEX, Handle, shifted


class GuideShape(Node):
    """A construction line from (``x0``, ``y0``) to (``x1``, ``y1``) that draws nothing."""

    category: ClassVar[str] = "guide"
    icon: ClassVar[str] = "guide"

    kind: Literal["guide"] = "guide"
    x0: Value = 0.0  # start
    y0: Value = -50.0
    x1: Value = 0.0  # end
    y1: Value = 50.0

    def moved(self, x, y, inner) -> dict:
        return {"x0": x(self.x0), "x1": x(self.x1), "y0": y(self.y0), "y1": y(self.y1)}

    def edit_handles(self, v) -> list[Handle]:
        """Its two ends: ``x0 y0`` (the start) and ``x1 y1``."""
        return [
            Handle("x0 y0", v(self.x0), v(self.y0), VERTEX),
            Handle("x1 y1", v(self.x1), v(self.y1), VERTEX),
        ]

    def handle_moved(self, key, to, v) -> dict:
        if key not in ("x0 y0", "x1 y1"):
            raise KeyError(f"a guide has no handle '{key}'")
        x, y = key.split()
        return {
            x: shifted(getattr(self, x), to[0] - v(getattr(self, x))),
            y: shifted(getattr(self, y), to[1] - v(getattr(self, y))),
        }

    def drag_sketch(self, v) -> tuple[list, bool]:
        return [(v(self.x0), v(self.y0)), (v(self.x1), v(self.y1))], False

    def summary(self) -> str:
        values = (self.x0, self.y0, self.x1, self.y1)
        if not all(isinstance(v, int | float) for v in values):
            return "guide"
        angle = math.degrees(math.atan2(self.y1 - self.y0, self.x1 - self.x0)) % 180
        return f"guide {angle:g}°"

    def detail(self) -> str:
        return self.summary().removeprefix("guide").strip() or "guide"

    @classmethod
    def default(cls, layer: str) -> GuideShape:
        """A vertical guide through the origin (guides have no layer)."""
        return cls()
