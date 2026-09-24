"""Geometry helpers for the shape kinds: arcs, booleans, transforms."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import klayout.db as kdb

from mems_sketch.core.component import Geometry, to_dbu

if TYPE_CHECKING:
    from mems_sketch.core.shapes.base import Point

ARC_TOLERANCE_UM = 0.005  # max chord deviation for circles and arcs
MAX_ARC_SEGMENTS = 4096


def to_ictrans(transform: kdb.DCplxTrans) -> kdb.ICplxTrans:
    return kdb.ICplxTrans(
        transform.mag,
        transform.angle,
        transform.is_mirror(),
        to_dbu(transform.disp.x),
        to_dbu(transform.disp.y),
    )


def apply_transform(transform: kdb.DCplxTrans, point: Point) -> Point:
    p = transform * kdb.DPoint(*point)
    return p.x, p.y


def boolean_op(op: str, a: Geometry, b: Geometry) -> Geometry:
    result = Geometry()
    for layer in a.layers.keys() | b.layers.keys():
        ra = a.layers.get(layer, kdb.Region())
        rb = b.layers.get(layer, kdb.Region())
        match op:
            case "subtract":
                out = ra - rb
            case "intersect":
                out = ra & rb
            case "xor":
                out = ra ^ rb
        if not out.is_empty():
            result.layers[layer] = out
    return result


def segments(radius: float, explicit: float | None) -> int:
    """Segments per full circle so the chord error stays below ARC_TOLERANCE_UM."""
    if explicit:
        n = int(explicit)
    elif radius <= ARC_TOLERANCE_UM:
        n = 8
    else:
        n = math.ceil(math.pi / math.acos(1 - ARC_TOLERANCE_UM / radius))
    return max(8, min(n, MAX_ARC_SEGMENTS))


def arc_points(cx, cy, r, start, end, n_full) -> list[tuple[float, float]]:
    sweep = end - start
    n = max(1, math.ceil(n_full * abs(sweep) / 360))
    full = abs(sweep) >= 360
    count = n if full else n + 1  # a full circle must not repeat its first point
    return [
        (
            cx + r * math.cos(math.radians(start + sweep * k / n)),
            cy + r * math.sin(math.radians(start + sweep * k / n)),
        )
        for k in range(count)
    ]
