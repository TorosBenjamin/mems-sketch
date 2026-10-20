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
INSERT = "insert"  # the middle of a polygon's or path's side: dragged, it adds a vertex

Evaluate = Callable[[object], float]  # a value (number or expression) as a number
Point = tuple[float, float]


@dataclass(frozen=True)
class Handle:
    """One handle: ``key`` names it to :meth:`handle_moved`; ``x, y`` is where it
    is, in the node's frame; ``role`` is one of the constants above.

    ``entry`` says where typed values put it (see :func:`typed_point`): ``("xy",)``
    (the default: its x and y), ``("x",)`` or ``("y",)`` (one coordinate),
    ``("radial", cx, cy, angle)`` (a distance from a centre), ``("angle", cx, cy,
    r)`` (a direction about a centre, in degrees) or ``("across", mx, my, nx, ny)``
    (a width across a line through ``m`` with normal ``n``).
    """

    key: str
    x: float
    y: float
    role: str
    entry: tuple = ("xy",)


# How a dimension is drawn (Dimension.style)
LINEAR = "linear"  # from a to b, its line set off to ``side``
RADIAL = "radial"  # from a centre (a) to the rim (b)
ACROSS = "across"  # a width: from one side (a) to the other (b), arrows from outside


@dataclass(frozen=True)
class Dimension:
    """A value of a node that is an expression, drawn as a dimension on the canvas:
    ``field`` (``x1``, ``radius``, ``point.2.x``) is ``expression``, worth ``value``;
    the dimension runs from ``a`` to ``b`` in the node's frame. A linear one is set
    off from that line towards ``side`` (a unit vector; zero: drawn on the line).

    A coordinate is measured from the frame's origin, so the dimension says what
    the value is: ``x1 = length`` runs from x = 0 to the side at x1."""

    field: str
    expression: str
    value: float
    a: Point
    b: Point
    side: Point = (0.0, 0.0)
    style: str = LINEAR


def coordinate_dimensions(points, v: Evaluate) -> list[Dimension]:
    """A dimension for each vertex coordinate that is an expression, from the axis to
    the vertex (``point.<i>.x`` / ``.y``)."""
    found = []
    for i, (x, y) in enumerate(points):
        px, py = v(x), v(y)
        if isinstance(x, str):
            found.append(Dimension(f"point.{i}.x", x, px, (0.0, py), (px, py)))
        if isinstance(y, str):
            found.append(Dimension(f"point.{i}.y", y, py, (px, 0.0), (px, py)))
    return found


def typed_point(handle: Handle, values: list[float]) -> Point:
    """Where typed ``values`` put ``handle`` (its frame); raises ValueError if they
    are not as many as it takes (:func:`entry_size`)."""
    entry = handle.entry
    if len(values) != entry_size(handle):
        count = entry_size(handle)
        raise ValueError(
            f"type {'one value' if count == 1 else 'x, y'} for {handle_label(handle.key)}"
        )
    if entry[0] == "xy":
        return values[0], values[1]
    if entry[0] == "x":
        return values[0], handle.y
    if entry[0] == "y":
        return handle.x, values[0]
    if entry[0] == "radial":
        _, cx, cy, angle = entry
        return on_circle((cx, cy), values[0], angle)
    if entry[0] == "angle":
        _, cx, cy, r = entry
        return on_circle((cx, cy), r, values[0])
    _, mx, my, nx, ny = entry  # across
    return mx + nx * values[0] / 2, my + ny * values[0] / 2


def entry_size(handle: Handle) -> int:
    """How many values a handle takes when typed: 2 for a point, else 1."""
    return 2 if handle.entry[0] == "xy" else 1


def handle_label(key: str) -> str:
    """A handle's key as people read it: ``x1``, ``x0, y1``, ``point 3``, ``outer radius``."""
    index = point_index(key)
    if index is not None:
        return f"point {index + 1}"
    prefix, _, rest = key.partition(".")
    if prefix == "insert" and rest.isdigit():
        return f"a new point after point {int(rest) + 1}"
    if prefix == "via" and rest.isdigit():
        return f"via point {int(rest) + 1}"
    return ", ".join(part.replace("_", " ") for part in key.split()).replace(".top", "")


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


def vertex_handles(points, v: Evaluate, closed: bool) -> list[Handle]:
    """A handle on each vertex, then one in the middle of each side (``insert.<i>``:
    the side from vertex ``i``; a closed outline's last side too)."""
    at = [(v(x), v(y)) for x, y in points]
    handles = [Handle(point_key(i), x, y, VERTEX) for i, (x, y) in enumerate(at)]
    sides = len(at) if closed else len(at) - 1
    for i in range(sides):
        (ax, ay), (bx, by) = at[i], at[(i + 1) % len(at)]
        handles.append(Handle(f"insert.{i}", (ax + bx) / 2, (ay + by) / 2, INSERT))
    return handles


def insert_index(key: str) -> int | None:
    """The vertex an ``insert.<i>`` key adds a point after, else None."""
    prefix, _, index = key.partition(".")
    return int(index) if prefix == "insert" and index.isdigit() else None


def vertex_inserted(points, after: int, to: Point) -> list:
    """``points`` with a new vertex at ``to`` after vertex ``after``."""
    return [*points[: after + 1], (rounded(to[0]), rounded(to[1])), *points[after + 1 :]]


def vertex_removed(points, index: int, fewest: int) -> list | None:
    """``points`` without vertex ``index``, or None if that would leave fewer than
    ``fewest``."""
    if len(points) <= fewest or not 0 <= index < len(points):
        return None
    return [p for i, p in enumerate(points) if i != index]


def points_moved(points, key: str, to: Point, v: Evaluate, closed: bool) -> list | None:
    """``points`` after dragging handle ``key`` (a vertex, or a side's middle that
    adds one) to ``to``; None for another key."""
    index = point_index(key)
    if index is not None and index < len(points):
        return vertex_moved(points, index, to, v)
    after = insert_index(key)
    sides = len(points) if closed else len(points) - 1
    if after is not None and after < sides:
        return vertex_inserted(points, after, to)
    return None


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
