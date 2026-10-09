"""Layouts written from the geometry library's cells (``_geom``): GDSII,
OASIS and DXF through KLayout, on an output grid, keeping the hierarchy.

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

import klayout.db as kdb

KLAYOUT_FORMATS = {".gds": "GDS2", ".oas": "OASIS", ".dxf": "DXF"}


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


def _grid_trans(t: Any, grid: float) -> kdb.Trans | None:
    """``t`` as KLayout's integer transformation, if it maps the grid onto
    itself; None otherwise. Both mirror about the x axis, then rotate."""
    if t.scale != 1.0:
        return None
    quarter = t.angle_deg / 90.0
    turns = round(quarter)
    if abs(quarter - turns) > 1e-9:
        return None
    x, y = _on_grid(t.dx, grid), _on_grid(t.dy, grid)
    if x is None or y is None:
        return None
    return kdb.Trans(turns % 4, bool(t.mirror_x), x, y)


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
    (KLayout's name: ``GDS2``, ``OASIS``, ``DXF``) or the file's extension.
    """
    g = geom()
    path = Path(path)
    file_format = file_format or KLAYOUT_FORMATS.get(path.suffix.lower())
    if file_format is None:
        raise ValueError(f"no layout format for '{path.suffix}' files")
    if not (grid_um > 0 and math.isfinite(grid_um)):
        raise ValueError("the grid must be a positive length")
    missing = sorted(set(_all_layers(top)) - set(layers))
    if missing:
        raise ValueError(f"layer(s) {', '.join(missing)} have no GDS mapping")

    layout = kdb.Layout()
    layout.dbu = grid_um
    indexes = {name: layout.layer(kdb.LayerInfo(*layers[name], name)) for name in layers}
    report = WriteReport(grid_um)
    written: dict[Any, int] = {}  # _geom.Cell -> KLayout cell index
    names: dict[str, int] = {}

    def unique(name: str) -> str:
        count = names.get(name, 0)
        names[name] = count + 1
        return name if count == 0 else f"{name}${count}"

    def write(cell: Any, name: str | None = None) -> int:
        if cell in written:
            return written[cell]
        target = layout.create_cell(unique(name or cell.name or "CELL"))
        written[cell] = target.cell_index()
        flattened: dict[str, list[Any]] = {}
        for child, t, (columns, rows, dx, dy) in cell.references:
            trans = _grid_trans(t, grid_um) if keep_hierarchy else None
            a, b = _on_grid(dx, grid_um), _on_grid(dy, grid_um)
            if trans is not None and a is not None and b is not None:
                index = write(child)
                if columns > 1 or rows > 1:
                    target.insert(
                        kdb.CellInstArray(
                            index, trans, kdb.Vector(a, 0), kdb.Vector(0, b), columns, rows
                        )
                    )
                else:
                    target.insert(kdb.CellInstArray(index, trans))
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
            shapes = target.shapes(indexes[layer])
            for hull, holes in snapped.polygons:
                polygon = kdb.Polygon([kdb.Point(int(x), int(y)) for x, y in hull])
                for hole in holes:
                    polygon.insert_hole([kdb.Point(int(x), int(y)) for x, y in hole])
                shapes.insert(polygon)
        return written[cell]

    write(top, top_cell)
    options = kdb.SaveLayoutOptions()
    options.format = file_format
    layout.write(str(path), options)
    return report


def _all_layers(cell: Any) -> set[str]:
    return set(cell.layers)  # a cell's layers include those of the cells it places
