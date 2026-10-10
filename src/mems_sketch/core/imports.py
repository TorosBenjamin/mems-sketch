"""Imported layouts: a cell of a GDS file, placed like any component.

An import is linked, not converted: the project keeps a copy of the file (in
its ``imports/`` folder) and a component that stands for one of its cells,
flattened. The component has no parameters and is read-only; it is placed,
arrayed, aligned (to its ``center``, ``top_left``, ...) and rounded like any
other. Its GDS layers are mapped onto the project's layers; layers that are
not mapped are left out. Importing the file again updates every placement.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field

from mems_sketch import layout as layout_files
from mems_sketch.core.component import DBU_UM, Component, Geometry, Params
from mems_sketch.core.region import IntPolygon, Region
from mems_sketch.layout import gds
from mems_sketch.layout.model import Layout

GdsLayer = tuple[int, int]  # layer, datatype


def layer_key(layer: GdsLayer) -> str:
    """How a GDS layer is written in a project file: ``"5/0"``."""
    return f"{layer[0]}/{layer[1]}"


def parse_layer_key(key: str) -> GdsLayer:
    layer, _, datatype = key.partition("/")
    return int(layer), int(datatype or 0)


@dataclass
class ImportedCell:
    """One imported cell: ``name`` (the component's name), the ``file`` it comes
    from (in the project's ``imports/`` folder), the ``cell`` and which project
    layer each GDS layer goes to (``"5/0": "metal"``)."""

    name: str
    file: str
    cell: str
    layers: dict[str, str] = field(default_factory=dict)
    description: str = ""
    data: bytes = field(default=b"", repr=False)  # the file's content

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


# -- reading GDS files ----------------------------------------------------------

_layouts: dict[str, Layout] = {}  # by digest: a file is read once
_names: dict[str, dict[GdsLayer, str]] = {}  # layer names of files made from documents


def read_layout(data: bytes) -> Layout:
    """The layout in a GDS file's content."""
    digest = hashlib.sha256(data).hexdigest()
    if digest not in _layouts:
        try:
            _layouts[digest] = layout_files.read(data)
        except (ValueError, struct.error) as exc:
            raise ValueError(f"not a readable GDS file: {exc}") from None
    return _layouts[digest]


def gds_bytes(geometry: Geometry, layers: dict[str, tuple[int, int]], cell: str) -> bytes:
    """``geometry`` as a GDS file with one cell; each layer gets its GDS numbers
    from ``layers`` and keeps its name for :func:`layer_names`."""
    out = Layout(dbu=DBU_UM)
    top = out.cell(cell)
    for name, (number, datatype) in layers.items():
        region = geometry.layers.get(name)
        if region is not None:
            for polygon in region.each_merged():
                top.add((number, datatype), polygon)
    data = gds.write(out)
    _names[hashlib.sha256(data).hexdigest()] = {gds_layer: n for n, gds_layer in layers.items()}
    return data


def cells(data: bytes) -> list[str]:
    """The file's cells, top cells first (what an import would usually pick)."""
    layout = read_layout(data)
    tops = layout.top_cells()
    return tops + sorted(name for name in layout.cells if name not in tops)


def gds_layers(data: bytes, cell: str) -> list[GdsLayer]:
    """The GDS layers that have shapes in ``cell`` (its sub-cells included)."""
    layout = read_layout(data)
    _cell(layout, cell)
    return sorted(layout.layers_in(cell))


def layer_names(data: bytes) -> dict[GdsLayer, str]:
    """The names of the file's layers, when it was made from a geometry document
    (GDS has none of its own)."""
    return dict(_names.get(hashlib.sha256(data).hexdigest(), {}))


def _cell(layout: Layout, name: str) -> None:
    if name not in layout.cells:
        raise ValueError(f"the file has no cell '{name}'")


def cell_geometry(imported: ImportedCell) -> Geometry:
    """The cell, flattened, on the project layers its GDS layers are mapped to."""
    layout = read_layout(imported.data)
    _cell(layout, imported.cell)
    scale = layout.dbu / DBU_UM  # the file's grid to ours (1 nm)
    geometry = Geometry()
    for gds_layer in sorted(layout.layers_in(imported.cell)):
        target = imported.layers.get(layer_key(gds_layer))
        if not target:
            continue
        polygons = layout.flat(imported.cell, gds_layer)
        if scale != 1:
            polygons = [
                IntPolygon(
                    [(round(x * scale), round(y * scale)) for x, y in polygon.hull],
                    [[(round(x * scale), round(y * scale)) for x, y in h] for h in polygon.holes],
                )
                for polygon in polygons
            ]
        # Merged: overlaps united, and holes that the file joined to their
        # outline by cuts made holes again.
        geometry.layers[target] = (
            geometry.region(target) + Region.from_polygons(polygons)
        ).merged()
    return geometry


class ImportedComponent(Component):
    """A component that stands for an imported cell (no parameters; the engine
    builds it from :func:`cell_geometry`)."""

    Params = Params

    def __init__(self, imported: ImportedCell) -> None:
        self.imported = imported
        self.type_name = imported.name
