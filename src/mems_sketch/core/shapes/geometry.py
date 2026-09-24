"""Geometry helpers for the shape kinds: arcs, booleans, transforms."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import klayout.db as kdb

from mems_sketch.core.component import DBU_UM, Geometry, to_dbu

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


# A corner to round or cut: where it is (µm), the radius (or chamfer length) and
# the style, "round" or "chamfer".
CornerSpec = tuple[float, float, float, str]
CORNER_TOLERANCE_DBU = 2  # a vertex this close to a corner's position is that corner


def round_corners(
    geometry: Geometry, corners: list[CornerSpec], explicit_segments: float | None = None
) -> Geometry:
    """``geometry`` with the given corners rounded (a tangent arc, convex or concave
    alike) or chamfered. Every corner must be a vertex of the merged geometry."""
    wanted = [(to_dbu(x), to_dbu(y), r, style) for x, y, r, style in corners]
    used = [False] * len(wanted)
    result = Geometry()
    for layer, region in geometry.layers.items():
        out = kdb.Region()
        for polygon in region.merged().each():
            hull = _rounded(list(polygon.each_point_hull()), wanted, used, explicit_segments)
            shape = kdb.Polygon(hull)
            for h in range(polygon.holes()):
                hole = list(polygon.each_point_hole(h))
                shape.insert_hole(_rounded(hole, wanted, used, explicit_segments))
            out.insert(shape)
        result.layers[layer] = out
    for (x, y, *_), found in zip(corners, used, strict=True):
        if not found:
            raise ValueError(f"({x:g}, {y:g}) is not a corner of the shape")
    return result


def _rounded(
    points: list[kdb.Point], wanted: list, used: list[bool], explicit: float | None
) -> list[kdb.Point]:
    n = len(points)
    cuts: dict[int, tuple[float, float, str, int]] = {}  # vertex: (t, radius, style, corner)
    for index, p in enumerate(points):
        for k, (x, y, r, style) in enumerate(wanted):
            if abs(p.x - x) <= CORNER_TOLERANCE_DBU and abs(p.y - y) <= CORNER_TOLERANCE_DBU:
                a, b = _unit(points[index - 1], p), _unit(points[(index + 1) % n], p)
                angle = math.acos(max(-1.0, min(1.0, a[0] * b[0] + a[1] * b[1])))
                if r < 0:
                    raise ValueError(f"corner radius must not be negative, got {r:g}")
                if angle < 1e-6 or math.pi - angle < 1e-6:
                    raise ValueError(
                        f"({x * DBU_UM:g}, {y * DBU_UM:g}) is not a corner of the shape"
                    )
                t = r if style == "chamfer" else r / math.tan(angle / 2)
                cuts[index] = (t / DBU_UM, r, style, k)
                used[k] = True
    for index, (t, r, _, k) in cuts.items():
        nxt = (index + 1) % n
        edge = math.dist(_xy(points[index]), _xy(points[nxt]))
        if t + cuts.get(nxt, (0.0,))[0] > edge + 1e-6:
            x, y = wanted[k][0] * DBU_UM, wanted[k][1] * DBU_UM
            raise ValueError(f"the corner at ({x:g}, {y:g}) does not fit a radius of {r:g}")
    result: list[kdb.Point] = []
    for index, p in enumerate(points):
        if index not in cuts:
            result.append(p)
            continue
        t, r, style, _ = cuts[index]
        a, b = _unit(points[index - 1], p), _unit(points[(index + 1) % n], p)
        p1 = (p.x + a[0] * t, p.y + a[1] * t)
        p2 = (p.x + b[0] * t, p.y + b[1] * t)
        if style == "chamfer" or r == 0:
            arc = [p1, p2]
        else:
            angle = math.acos(max(-1.0, min(1.0, a[0] * b[0] + a[1] * b[1])))
            bisector = _normal((a[0] + b[0], a[1] + b[1]))
            radius = r / DBU_UM
            d = radius / math.sin(angle / 2)
            cx, cy = p.x + bisector[0] * d, p.y + bisector[1] * d
            start = math.atan2(p1[1] - cy, p1[0] - cx)
            sweep = math.remainder(math.atan2(p2[1] - cy, p2[0] - cx) - start, 2 * math.pi)
            steps = max(2, math.ceil(segments(r, explicit) * abs(sweep) / (2 * math.pi)))
            arc = [
                (
                    cx + radius * math.cos(start + sweep * s / steps),
                    cy + radius * math.sin(start + sweep * s / steps),
                )
                for s in range(steps + 1)
            ]
        result += [kdb.Point(round(x), round(y)) for x, y in arc]
    return result


def _xy(p: kdb.Point) -> tuple[float, float]:
    return float(p.x), float(p.y)


def _normal(v: tuple[float, float]) -> tuple[float, float]:
    length = math.hypot(*v)
    return v[0] / length, v[1] / length


def _unit(towards: kdb.Point, origin: kdb.Point) -> tuple[float, float]:
    """The direction from ``origin`` to ``towards``."""
    return _normal((towards.x - origin.x, towards.y - origin.y))
