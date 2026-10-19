"""Handles: the parts of a shape you drag on the canvas to change it (a side of a
rectangle, a polygon's vertex, a circle's rim).

A kind lists its handles with :meth:`~mems_sketch.core.shapes.base.Node.edit_handles`
and says what a drag changes with
:meth:`~mems_sketch.core.shapes.base.Node.handle_moved`, both in the node's own
frame (before its alignment moves it). A drag changes the fields it moves by an
offset (:func:`~mems_sketch.core.shapes.rewrite.offset_value`), so expressions
are kept: ``length`` dragged 3 µm becomes ``length + 3``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

# What a handle does (Handle.role): how it is drawn and the cursor over it
EDGE, CORNER, VERTEX, RADIUS, ANGLE, WIDTH = "edge", "corner", "vertex", "radius", "angle", "width"

Evaluate = Callable[[object], float]  # a value (number or expression) as a number
Point = tuple[float, float]


@dataclass(frozen=True)
class Handle:
    """One handle: ``key`` names it to :meth:`handle_moved`; ``x, y`` is where it
    is, in the node's frame; ``role`` is one of the constants above."""

    key: str
    x: float
    y: float
    role: str


def shifted(value, by: float):
    """``value`` moved by ``by`` (expressions kept; see ``offset_value``)."""
    from mems_sketch.core.shapes.rewrite import offset_value  # (it imports the kinds)

    return offset_value(value, by)


def point_key(index: int) -> str:
    return f"point.{index}"


def point_index(key: str) -> int | None:
    """The vertex a ``point.<i>`` key names, else None."""
    prefix, _, index = key.partition(".")
    return int(index) if prefix == "point" and index.isdigit() else None


def vertex_handles(points, v: Evaluate) -> list[Handle]:
    return [Handle(point_key(i), v(x), v(y), VERTEX) for i, (x, y) in enumerate(points)]


def vertex_moved(points, index: int, to: Point, v: Evaluate) -> list:
    """``points`` with vertex ``index`` at ``to`` (each coordinate moved by an offset)."""
    moved = list(points)
    x, y = points[index]
    moved[index] = (shifted(x, to[0] - v(x)), shifted(y, to[1] - v(y)))
    return moved


def angle_of(center: Point, point: Point) -> float:
    """The direction from ``center`` to ``point``, in degrees."""
    return math.degrees(math.atan2(point[1] - center[1], point[0] - center[0]))


def turn(start: float, end: float) -> float:
    """The smallest turn from angle ``start`` to ``end``, in (-180, 180]."""
    delta = (end - start) % 360.0
    return delta - 360.0 if delta > 180.0 else delta


def on_circle(center: Point, radius: float, angle: float) -> Point:
    a = math.radians(angle)
    return center[0] + radius * math.cos(a), center[1] + radius * math.sin(a)


def rounded(value: float) -> float:
    """``value`` to the nm grid, for a value worked out from a distance or angle."""
    return round(value, 6) + 0.0
