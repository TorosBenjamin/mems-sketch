"""Layouts written from the geometry library's cells (``_geom``): GDSII,
OASIS and DXF (:mod:`mems_sketch.layout`), on an output grid, keeping the
hierarchy.

- Each cell becomes a cell of the file, written once however often it is
  placed, and an array becomes an array reference (requirement OUT-4).
- Each cell's geometry is snapped to the grid in its own frame
  (:func:`_geom.snap`), and what snapping changed is reported (OUT-3).
- A placement keeps the hierarchy only if it maps the grid onto itself: no
  scaling, a quarter-turn rotation, and an offset (and array steps) on the
  grid. Otherwise its geometry is flattened into the parent first, so that it
  is snapped where it ends up. Flattened placements are counted in the report.

Snapping cell by cell does not see gaps that close between separately placed
cells; ``keep_hierarchy=False`` flattens everything and reports those too.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mems_sketch import layout
from mems_sketch.core.region import IntPolygon

LAYOUT_FORMATS = {".gds": "GDS2", ".oas": "OASIS", ".dxf": "DXF"}


def geom() -> Any:
    """The geometry library's Python module."""
    try:
        from mems_sketch import _geom  # type: ignore[attr-defined]
    except ImportError:
        import _geom  # built in build/geom/ until it is packaged
    return _geom


@dataclass
class LayerReport:
    cell: str
    layer: str
    report: Any  # _geom.SnapReport


@dataclass
class WriteReport:
    """What writing a layout to its grid changed: one entry per cell and
    layer written, and how many placements were flattened because they would
    have put geometry off the grid."""

    grid_um: float
    layers: list[LayerReport] = field(default_factory=list)
    flattened: int = 0

    @property
    def events(self) -> list[tuple[str, str, Any]]:
        """(cell, layer, _geom.SnapEvent) for every change snapping made."""
        return [(e.cell, e.layer, ev) for e in self.layers for ev in e.report.events]

    @property
    def changed_shape(self) -> bool:
        return any(e.report.changed_shape for e in self.layers)


def _on_grid(value: float, grid: float) -> int | None:
    """``value`` in grid units if it is a whole number of them."""
    steps = value / grid
    nearest = round(steps)
    return nearest if abs(steps - nearest) < 1e-6 else None


def _grid_trans(t: Any, grid: float) -> tuple[int, bool, int, int] | None:
    """``t`` as (quarter turns, mirror, x, y) on the grid, if it maps the grid
    onto itself; None otherwise. Both mirror about the x axis, then rotate."""
    if t.scale != 1.0:
        return None
    quarter = t.angle_deg / 90.0
    turns = round(quarter)
    if abs(quarter - turns) > 1e-9:
        return None
    x, y = _on_grid(t.dx, grid), _on_grid(t.dy, grid)
    if x is None or y is None:
        return None
    return turns % 4, bool(t.mirror_x), x, y


def write_cell(
    top: Any,
    path: str | Path,
    layers: Mapping[str, tuple[int, int]],
    *,
    grid_um: float = 0.001,
    chord_um: float = 0.005,
    keep_hierarchy: bool = True,
    top_cell: str | None = None,
    file_format: str | None = None,
) -> WriteReport:
    """Write the ``_geom.Cell`` ``top`` and the cells it places to ``path``.

    ``layers`` maps each layer name to its GDS (layer, datatype); a layer with
    geometry but no mapping is an error. The format is ``file_format``
    (``GDS2``, ``OASIS``, ``DXF``) or the file's extension.
    """
    g = geom()
    path = Path(path)
    file_format = file_format or LAYOUT_FORMATS.get(path.suffix.lower())
    if file_format is None:
        raise ValueError(f"no layout format for '{path.suffix}' files")
    if not (grid_um > 0 and math.isfinite(grid_um)):
        raise ValueError("the grid must be a positive length")
    missing = sorted(set(_all_layers(top)) - set(layers))
    if missing:
        raise ValueError(f"layer(s) {', '.join(missing)} have no GDS mapping")

    out = layout.Layout(dbu=grid_um)
    out.layer_names = {layers[name]: name for name in layers}
    report = WriteReport(grid_um)
    written: dict[Any, str] = {}  # _geom.Cell -> its name in the file
    names: dict[str, int] = {}

    def unique(name: str) -> str:
        count = names.get(name, 0)
        names[name] = count + 1
        return name if count == 0 else f"{name}${count}"

    def write(cell: Any, name: str | None = None) -> str:
        if cell in written:
            return written[cell]
        target = out.cell(unique(name or cell.name or "CELL"))
        written[cell] = target.name
        flattened: dict[str, list[Any]] = {}
        for child, t, (columns, rows, dx, dy) in cell.references:
            trans = _grid_trans(t, grid_um) if keep_hierarchy else None
            a, b = _on_grid(dx, grid_um), _on_grid(dy, grid_um)
            if trans is not None and a is not None and b is not None:
                child_name = write(child)
                turns, mirror, x, y = trans
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
                continue
            report.flattened += 1
            for i in range(columns):
                for j in range(rows):
                    placed = g.Transform.translation(i * dx, j * dy) * t
                    for layer in child.layers:
                        flattened.setdefault(layer, []).append(
                            child.flat(layer).transformed(placed)
                        )
        for layer in cell.layers:
            parts = [cell.own(layer), *flattened.get(layer, [])]
            region = g.Region.unite(parts) if len(parts) > 1 else parts[0]
            if region.empty:
                continue
            snapped = g.snap(region, grid_um, chord_um)
            report.layers.append(LayerReport(target.name, layer, snapped.report))
            for hull, holes in snapped.polygons:
                target.add(
                    layers[layer],
                    IntPolygon(
                        [tuple(p) for p in hull.tolist()],
                        [[tuple(p) for p in h.tolist()] for h in holes],
                    ),
                )
        return written[cell]

    write(top, top_cell)
    # Children before their parents, as some readers want.
    order = list(dict.fromkeys(reversed(list(out.cells))))
    out.cells = {name: out.cells[name] for name in order}
    layout.write(out, path, file_format)
    return report


def _all_layers(cell: Any) -> set[str]:
    return set(cell.layers)  # a cell's layers include those of the cells it places
