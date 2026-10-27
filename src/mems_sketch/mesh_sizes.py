"""The automatic mesh size map (MSH-8, #73): how fine the mesh must be where,
from the geometry alone.

At each corner of each outline it finds:

- **the local feature size**: the distance to the nearest edge across from it,
  of the same layer: through the material (a beam's width) or through the space
  next to it (the gap to a comb's next finger). Edges along the outline beside
  the corner (as on a curve) are not across from it: only those within 50° of
  the outline's normal count;
- **the curvature**: on a curve made of short segments, its radius, so that a
  circle gets :data:`PER_CIRCLE` elements round it.

The size there is the smaller of the feature size over ``across`` (elements
across a beam or a gap) and the curve's share, never above the largest size.
Corners are binned by size (:func:`bins`), so the mesher grades away from each
bin by the growth rate (see :mod:`mems_sketch.meshing`).
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

PER_CIRCLE = 16  # elements round a full circle
ACROSS_COS = math.cos(math.radians(50))  # how near the normal an edge across must be
STRAIGHT = math.radians(3)  # a smaller turn is no curve
SMALLEST = 1e-3  # µm: no automatic size below this
BIN_RATIO = 1.5  # sizes within this factor of each other share a bin


@dataclass(frozen=True)
class SizePoint:
    """A corner of an outline and the size the mesh needs there (µm)."""

    layer: str
    x: float
    y: float
    size: float


def size_map(
    rings_by_layer: dict[str, list[np.ndarray]], across: float, largest: float
) -> list[SizePoint]:
    """The sizes needed at the corners of the outlines (each layer's closed rings,
    ``(n, 2)`` points in µm): those finer than ``largest`` only."""
    found: list[SizePoint] = []
    for layer, rings in rings_by_layer.items():
        rings = [_clean(r) for r in rings]
        rings = [r for r in rings if len(r) >= 3]
        if not rings:
            continue
        sizes = _layer_sizes(rings, across, largest)
        points = np.vstack(rings)
        found += [
            SizePoint(layer, float(x), float(y), float(s))
            for (x, y), s in zip(points, sizes, strict=True)
            if s < largest
        ]
    return found


def rings_of(geometry, layers: Iterable[str] | None = None) -> dict[str, list[np.ndarray]]:
    """Each layer's outlines (hulls and holes) in µm."""
    wanted = None if layers is None else set(layers)
    rings: dict[str, list[np.ndarray]] = {}
    for name, region in geometry.layers.items():
        if region.is_empty() or (wanted is not None and name not in wanted):
            continue
        for hull, holes in region.points_um():
            rings.setdefault(name, []).extend(np.asarray(r, dtype=float) for r in (hull, *holes))
    return rings


def bins(points: list[SizePoint]) -> list[tuple[float, list[SizePoint]]]:
    """The points grouped by size, finest first: each group with the smallest size
    in it, sizes within :data:`BIN_RATIO` of it."""
    groups: list[tuple[float, list[SizePoint]]] = []
    for point in sorted(points, key=lambda p: p.size):
        if groups and point.size <= groups[-1][0] * BIN_RATIO:
            groups[-1][1].append(point)
        else:
            groups.append((point.size, [point]))
    return groups


def _clean(ring: np.ndarray) -> np.ndarray:
    """``ring`` without repeated points (nor its first point again at the end)."""
    ring = np.asarray(ring, dtype=float)
    if len(ring) < 2:
        return ring
    keep = np.ones(len(ring), dtype=bool)
    keep[1:] = np.hypot(*(ring[1:] - ring[:-1]).T) > 1e-9
    ring = ring[keep]
    if len(ring) > 1 and np.hypot(*(ring[0] - ring[-1])) <= 1e-9:
        ring = ring[:-1]
    return ring


def _layer_sizes(rings: list[np.ndarray], across: float, largest: float) -> np.ndarray:
    points = np.vstack(rings)
    # Each corner's edges: the one ending at it and the one starting at it.
    starts, ends, before, after = [], [], [], []
    offset = 0
    for ring in rings:
        n = len(ring)
        index = np.arange(n) + offset
        starts.append(index)
        ends.append(np.roll(index, -1))
        before.append(np.roll(index, 1))  # the edge (before -> corner) has this start
        after.append(index)
        offset += n
    starts, ends = np.concatenate(starts), np.concatenate(ends)
    before, after = np.concatenate(before), np.concatenate(after)
    a, b = points[starts], points[ends]  # every edge
    incoming = points - points[before]
    outgoing = points[ends[after]] - points
    l_in = np.hypot(*incoming.T)
    l_out = np.hypot(*outgoing.T)
    t_in = incoming / np.maximum(l_in, 1e-12)[:, None]
    t_out = outgoing / np.maximum(l_out, 1e-12)[:, None]
    normal = np.stack([-(t_in[:, 1] + t_out[:, 1]), t_in[:, 0] + t_out[:, 0]], axis=1)
    length = np.hypot(*normal.T)
    normal = np.where(length[:, None] > 1e-9, normal / np.maximum(length, 1e-12)[:, None], 0)
    no_normal = length <= 1e-9  # a spike: any direction will do
    # Curvature: the turn at the corner over the length it is spread along.
    turn = np.arccos(np.clip((t_in * t_out).sum(axis=1), -1.0, 1.0))
    radius = np.where(turn > STRAIGHT, 0.5 * (l_in + l_out) / np.maximum(turn, 1e-12), np.inf)
    curved = 2 * math.pi * radius / PER_CIRCLE
    # The feature size: the nearest edge across, within reach (wider is coarse anyway).
    reach = largest * across
    feature = np.full(len(points), np.inf)
    cell = max(reach, 1e-6)
    keys = np.floor(points / cell).astype(np.int64)
    edge_lo = np.floor(np.minimum(a, b) / cell).astype(np.int64)
    edge_hi = np.floor(np.maximum(a, b) / cell).astype(np.int64)
    grid: dict[tuple[int, int], list[int]] = {}
    for e in range(len(a)):
        for i in range(edge_lo[e, 0], edge_hi[e, 0] + 1):
            for j in range(edge_lo[e, 1], edge_hi[e, 1] + 1):
                grid.setdefault((i, j), []).append(e)
    corners_by_cell: dict[tuple[int, int], list[int]] = {}
    for k, key in enumerate(map(tuple, keys)):
        corners_by_cell.setdefault(key, []).append(k)
    for (i, j), corners in corners_by_cell.items():
        candidates = sorted(
            {e for di in (-1, 0, 1) for dj in (-1, 0, 1) for e in grid.get((i + di, j + dj), ())}
        )
        if not candidates:
            continue
        corners = np.asarray(corners)
        edges = np.asarray(candidates)
        p = points[corners][:, None, :]  # (c, 1, 2)
        ea, eb = a[edges][None], b[edges][None]  # (1, e, 2)
        d = eb - ea
        t = np.clip(((p - ea) * d).sum(-1) / np.maximum((d * d).sum(-1), 1e-24), 0, 1)
        nearest = ea + t[..., None] * d
        offset = nearest - p
        distance = np.hypot(offset[..., 0], offset[..., 1])
        direction = offset / np.maximum(distance, 1e-12)[..., None]
        facing = np.abs((direction * normal[corners][:, None, :]).sum(-1)) >= ACROSS_COS
        facing |= no_normal[corners][:, None]
        own = (edges[None, :] == before[corners][:, None]) | (
            edges[None, :] == after[corners][:, None]
        )
        ok = facing & ~own & (distance > 1e-9) & (distance < reach)
        best = np.where(ok, distance, np.inf).min(axis=1)
        feature[corners] = np.minimum(feature[corners], best)
    sizes = np.minimum(feature / across, curved)
    return np.clip(sizes, SMALLEST, largest)
