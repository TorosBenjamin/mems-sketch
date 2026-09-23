"""Geometry helpers for the shape kinds: arcs, booleans, transforms."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import klayout.db as kdb

from mems_sketch.core.component import Geometry, to_dbu

if TYPE_CHECKING:
    from mems_sketch.core.shapes.base import ArcShape, PathShape, Point

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
            case "union":
                out = ra | rb
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


def annular_sector(shape: ArcShape, ev) -> kdb.Region:
    """Outer pie minus inner pie; with full angles this is a ring or disc."""
    cx, cy = ev(shape.x), ev(shape.y)
    r_in, r_out = ev(shape.inner_radius), ev(shape.outer_radius)
    start, end = ev(shape.start_angle), ev(shape.end_angle)
    if not 0 <= r_in < r_out:
        raise ValueError("arc needs 0 <= inner_radius < outer_radius")
    if end <= start:
        raise ValueError("arc end_angle must be greater than start_angle")
    n = segments(r_out, shape.segments and ev(shape.segments))

    def pie(r: float) -> kdb.Region:
        points = arc_points(cx, cy, r, start, end, n)
        if end - start < 360:
            points.append((cx, cy))
        return kdb.Region(kdb.Polygon([kdb.Point(to_dbu(x), to_dbu(y)) for x, y in points]))

    return pie(r_out) - pie(r_in) if r_in > 0 else pie(r_out)


def path_of(shape: PathShape, ev) -> kdb.Path:
    points = [kdb.Point(to_dbu(ev(x)), to_dbu(ev(y))) for x, y in shape.points]
    width = to_dbu(ev(shape.width))
    if width <= 0:
        raise ValueError("path width must be positive")
    ext = 0 if shape.ends == "flush" else width // 2
    return kdb.Path(points, width, ext, ext, shape.ends == "round")
