"""Wire of constant width along a centreline."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field

from mems_sketch.core.levels import LEVEL
from mems_sketch.core.shapes.base import Primitive, Value


class PathShape(Primitive):
    """A wire of constant ``width`` on ``layer`` along a centreline through
    ``points`` (two or more ``[x, y]``), e.g. a beam or a trace. ``ends``:
    ``flush`` (cut at the end points), ``square`` (extended by half the width)
    or ``round``. Its ports ``start`` and ``end`` are at its ends, pointing out."""

    icon: ClassVar[str] = "path"

    kind: Literal["path"] = "path"
    layer: str = LEVEL  # see mems_sketch.core.levels
    points: list[tuple[Value, Value]] = Field(min_length=2)
    width: Value
    ends: Literal["flush", "square", "round"] = "flush"

    def moved(self, x, y, inner) -> dict:
        return {"points": [(x(px), y(py)) for px, py in self.points]}

    @classmethod
    def default(cls, layer: str) -> PathShape:
        return cls(layer=layer, points=[(0, 0), (100, 0), (100, 60)], width=4)
