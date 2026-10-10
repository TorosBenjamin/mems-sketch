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


class GuideShape(Node):
    category: ClassVar[str] = "guide"
    icon: ClassVar[str] = "guide"

    kind: Literal["guide"] = "guide"
    x0: Value = 0.0  # start
    y0: Value = -50.0
    x1: Value = 0.0  # end
    y1: Value = 50.0

    def moved(self, x, y, inner) -> dict:
        return {"x0": x(self.x0), "x1": x(self.x1), "y0": y(self.y0), "y1": y(self.y1)}

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
