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
import os
import tempfile
from dataclasses import dataclass, field

import klayout.db as kdb

from mems_sketch.core.component import DBU_UM, Component, Geometry, Params

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

_layouts: dict[str, kdb.Layout] = {}  # by digest: a file is read once


def read_layout(data: bytes) -> kdb.Layout:
    """The layout in a GDS (or OASIS) file's content."""
    digest = hashlib.sha256(data).hexdigest()
    if digest not in _layouts:
        layout = kdb.Layout()
        try:
            if hasattr(layout, "read_bytes"):
                layout.read_bytes(data)
            else:  # older klayout
                with tempfile.NamedTemporaryFile(suffix=".gds", delete=False) as f:
                    f.write(data)
                try:
                    layout.read(f.name)
                finally:
                    os.unlink(f.name)
        except RuntimeError as exc:
            raise ValueError(f"not a readable GDS file: {exc}") from None
        _layouts[digest] = layout
    return _layouts[digest]


def cells(data: bytes) -> list[str]:
    """The file's cells, top cells first (what an import would usually pick)."""
    layout = read_layout(data)
    tops = [cell.name for cell in layout.top_cells()]
    others = sorted(cell.name for cell in layout.each_cell() if cell.name not in tops)
    return tops + others


def gds_layers(data: bytes, cell: str) -> list[GdsLayer]:
    """The GDS layers that have shapes in ``cell`` (its sub-cells included)."""
    layout = read_layout(data)
    top = _cell(layout, cell)
    found = []
    for index in layout.layer_indexes():
        if not top.begin_shapes_rec(index).at_end():
            info = layout.get_info(index)
            found.append((info.layer, info.datatype))
    return sorted(found)


def layer_names(data: bytes) -> dict[GdsLayer, str]:
    """The names the file gives its layers (OASIS can, GDS cannot)."""
    layout = read_layout(data)
    names = {}
    for index in layout.layer_indexes():
        info = layout.get_info(index)
        if info.name:
            names[(info.layer, info.datatype)] = info.name
    return names


def _cell(layout: kdb.Layout, name: str) -> kdb.Cell:
    cell = layout.cell(name)
    if cell is None:
        raise ValueError(f"the file has no cell '{name}'")
    return cell


def cell_geometry(imported: ImportedCell) -> Geometry:
    """The cell, flattened, on the project layers its GDS layers are mapped to."""
    layout = read_layout(imported.data)
    top = _cell(layout, imported.cell)
    scale = layout.dbu / DBU_UM  # the file's grid to ours (1 nm)
    trans = kdb.ICplxTrans(scale)
    geometry = Geometry()
    for index in layout.layer_indexes():
        info = layout.get_info(index)
        target = imported.layers.get(layer_key((info.layer, info.datatype)))
        if not target:
            continue
        region = kdb.Region(top.begin_shapes_rec(index))
        if region.is_empty():
            continue
        geometry.region(target).insert(region.transformed(trans) if scale != 1 else region)
    return geometry


class ImportedComponent(Component):
    """A component that stands for an imported cell (no parameters)."""

    Params = Params

    def __init__(self, imported: ImportedCell) -> None:
        self.imported = imported
        self.type_name = imported.name

    def build(self, params: Params) -> Geometry:
        return cell_geometry(self.imported)
