"""GDSII, OASIS and DXF export via KLayout's writers."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import klayout.db as kdb

from mems_sketch.core.component import DBU_UM, Geometry
from mems_sketch.core.project import Project
from mems_sketch.export.base import ExportOption

GRID = ExportOption(
    "grid_um",
    DBU_UM,
    "Grid",
    "The file's database unit: every point is snapped to it. A whole multiple of 1 nm.",
    minimum=DBU_UM,
    maximum=1.0,
    suffix=" µm",
    decimals=4,
)
TOP_CELL = ExportOption(
    "top_cell", "", "Top cell", "The top cell's name; empty: the project's name."
)


class _KLayoutExporter:
    format_name: ClassVar[str]
    file_extension: ClassVar[str]
    klayout_format: ClassVar[str]
    options: ClassVar[tuple[ExportOption, ...]] = (GRID, TOP_CELL)

    def export(
        self,
        project: Project,
        geometry: Geometry,
        path: Path,
        grid_um: float = DBU_UM,
        top_cell: str = "",
    ) -> None:
        steps = round(grid_um / DBU_UM)  # the grid in the geometry's 1 nm units
        if steps < 1 or abs(steps * DBU_UM - grid_um) > 1e-9 * DBU_UM:
            raise ValueError(f"the grid must be a whole multiple of 1 nm, not {grid_um:g} µm")
        layout = kdb.Layout()
        layout.dbu = steps * DBU_UM
        top = layout.create_cell(top_cell or project.name or "TOP")
        for name, region in geometry.layers.items():
            layer = project.layers.get(name)
            if layer is None:
                raise ValueError(f"layer '{name}' has no GDS mapping; add it to the project")
            index = layout.layer(kdb.LayerInfo(layer.gds_layer, layer.gds_datatype, name))
            if steps > 1:
                # Every point on a multiple of the grid, then in grid units.
                region = region.snapped(steps, steps).transformed(kdb.ICplxTrans(1 / steps))
                region = region.merged()
            top.shapes(index).insert(region)
        options = kdb.SaveLayoutOptions()
        options.format = self.klayout_format
        layout.write(str(path), options)


class GdsExporter(_KLayoutExporter):
    format_name = "gds"
    title = "GDSII"
    file_extension = ".gds"
    klayout_format = "GDS2"


class OasisExporter(_KLayoutExporter):
    format_name = "oasis"
    title = "OASIS"
    file_extension = ".oas"
    klayout_format = "OASIS"


class DxfExporter(_KLayoutExporter):
    format_name = "dxf"
    title = "DXF"
    file_extension = ".dxf"
    klayout_format = "DXF"


BUILTIN = (GdsExporter, OasisExporter, DxfExporter)
