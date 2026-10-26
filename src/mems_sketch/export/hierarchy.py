"""GDSII, OASIS and DXF written as cells (requirement OUT-4, #74).

The engine builds each component a design places whole once, as a cell of its
own (``Build.cells``). The layout keeps that:

- **A cell per component**, named after it (``mems.anchor`` is ``mems_anchor``;
  the same component with other parameter values ``mems_anchor$1``, ...). The
  top cell is named after the exported component unless a name is given.
- **Placements as references**, and copies on a regular grid (an array
  modifier's) as one array reference.
- **Each cell is rounded onto the grid once, in its own frame**, by the engine,
  from the exact geometry. A placement that would put that off the grid (an
  offset off the grid, a turn other than a quarter, a scale) is flattened into
  the cell holding it instead, and rounded there.

Rounding cell by cell does not see gaps that close between separately placed
cells; exporting without the hierarchy rounds everything together.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mems_sketch import layout
from mems_sketch.core.region import IntPolygon

if TYPE_CHECKING:
    from mems_sketch.engine import Build

Transform = tuple[float, float, float, bool, float]  # dx, dy (µm), angle, mirror_x, scale


def cell_name(component: str) -> str:
    """A component's name as a cell's: the characters GDSII allows (letters,
    digits, ``_``, ``?``, ``$``), others as ``_``."""
    return re.sub(r"[^A-Za-z0-9_?$]", "_", component) or "CELL"


def write_tree(
    build: Build,
    path: str | Path,
    layers: Mapping[str, tuple[int, int]],
    *,
    grid_um: float,
    chord_um: float,
    file_format: str,
    top_cell: str | None = None,
) -> int:
    """Write ``build``'s component and the cells it places to ``path``; ``layers``
    maps each layer to its GDS (layer, datatype). Returns how many placements were
    flattened because they would have put geometry off the grid."""
    top, cells = build.cells(grid_um, chord_um)
    out = layout.Layout(dbu=grid_um)
    out.layer_names = {gds: name for name, gds in layers.items()}
    written: dict[str, str] = {}  # engine key -> cell name
    taken: dict[str, int] = {}
    flattened = 0

    def unique(name: str) -> str:
        count = taken.get(name, 0)
        taken[name] = count + 1
        return name if count == 0 else f"{name}${count}"

    def write(key: str, name: str | None = None) -> str:
        nonlocal flattened
        if key in written:
            return written[key]
        own, placed, component = cells[key]
        target = out.cell(unique(name or cell_name(component)))
        written[key] = target.name
        extra: dict[str, list] = defaultdict(list)  # flattened placements, µm regions
        groups: dict[tuple, list[tuple[int, int]]] = defaultdict(list)
        for child, transform in placed:
            on_grid = _on_grid(transform, grid_um)
            if on_grid is None:
                flattened += 1
                for layer, region in _flat(child, cells, grid_um).items():
                    extra[layer].append(region.transformed(_geom_transform(transform)))
                continue
            turns, mirror, x, y = on_grid
            groups[(child, turns, mirror)].append((x, y))
        for (child, turns, mirror), at in groups.items():
            child_name = write(child)
            for x, y, columns, rows, a, b in _arrays(at):
                target.placements.append(
                    layout.Placement(
                        child_name,
                        x,
                        y,
                        angle=90.0 * turns,
                        mirror=mirror,
                        columns=columns,
                        rows=rows,
                        column_step=(a, 0),
                        row_step=(0, b),
                    )
                )
        for layer in sorted(set(own) | set(extra)):
            if layer not in layers:
                raise ValueError(f"layer '{layer}' has no GDS mapping; add it to the project")
            mapping = layers[layer]
            if layer in extra:  # rounded again, together with what was flattened into it
                for polygon in _snapped(
                    own.get(layer, ((), None))[0], extra[layer], grid_um, chord_um
                ):
                    target.add(mapping, polygon)
                continue
            for hull, holes in own[layer][0]:
                target.add(mapping, IntPolygon(hull, list(holes)))
        return target.name

    write(top, top_cell)
    order = list(dict.fromkeys(reversed(list(out.cells))))  # children before parents
    out.cells = {name: out.cells[name] for name in order}
    layout.write(out, path, file_format)
    return flattened


def _on_grid(t: Transform, grid: float) -> tuple[int, bool, int, int] | None:
    """A placement as (quarter turns, mirror, x, y in grid units) if it maps the
    grid onto itself; None otherwise."""
    dx, dy, angle, mirror, scale = t
    if scale != 1.0:
        return None
    turns = round(angle / 90.0)
    if abs(angle / 90.0 - turns) > 1e-9:
        return None
    x, y = dx / grid, dy / grid
    if abs(x - round(x)) > 1e-6 or abs(y - round(y)) > 1e-6:
        return None
    return turns % 4, bool(mirror), round(x), round(y)


def _arrays(at: list[tuple[int, int]]) -> list[tuple[int, int, int, int, int, int]]:
    """Placements at ``at`` as (x, y, columns, rows, column step, row step): one
    array when they fill a regular grid along x and y, else one each."""
    points = sorted(set(at))
    if len(points) == len(at) and len(points) > 1:
        xs, ys = sorted({x for x, _ in points}), sorted({y for _, y in points})
        if len(xs) * len(ys) == len(points) and _even(xs) and _even(ys):
            a = xs[1] - xs[0] if len(xs) > 1 else 0
            b = ys[1] - ys[0] if len(ys) > 1 else 0
            return [(xs[0], ys[0], len(xs), len(ys), a, b)]
    return [(x, y, 1, 1, 0, 0) for x, y in at]


def _even(values: list[int]) -> bool:
    return len({b - a for a, b in pairwise(values)}) <= 1


def _geom():
    from mems_sketch.export.cells import geom

    return geom()


def _geom_transform(t: Transform):
    dx, dy, angle, mirror, scale = t
    return _geom().Transform(dx, dy, angle, bool(mirror), scale)


def _region(polygons, grid: float):
    """Polygons in grid units as a geometry library region, µm."""
    g = _geom()
    faces = []
    for hull, holes in polygons:
        face = g.Region.polygon([(x * grid, y * grid) for x, y in hull])
        for hole in holes:
            face = face - g.Region.polygon([(x * grid, y * grid) for x, y in hole])
        faces.append(face)
    return g.Region.unite(faces) if faces else g.Region()


def _flat(key: str, cells: Mapping[str, Any], grid: float) -> dict[str, Any]:
    """The cell ``key`` with everything it places merged in: {layer: region, µm}."""
    own, placed, _ = cells[key]
    parts: dict[str, list] = defaultdict(list)
    for layer, (found, _box) in own.items():
        parts[layer].append(_region(found, grid))
    for child, transform in placed:
        for layer, region in _flat(child, cells, grid).items():
            parts[layer].append(region.transformed(_geom_transform(transform)))
    g = _geom()
    return {layer: g.Region.unite(regions) for layer, regions in parts.items()}


def _snapped(own, extra: list, grid: float, chord: float) -> list[IntPolygon]:
    g = _geom()
    region = g.Region.unite([_region(own, grid), *extra])
    snapped = g.snap(region, grid, chord)
    return [
        IntPolygon(
            [tuple(p) for p in hull.tolist()], [[tuple(p) for p in h.tolist()] for h in holes]
        )
        for hull, holes in snapped.polygons
    ]
