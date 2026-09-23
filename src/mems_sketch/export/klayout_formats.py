"""GDSII, OASIS and DXF export via KLayout's writers."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import klayout.db as kdb

from mems_sketch.core.component import DBU_UM, Geometry
from mems_sketch.core.project import Project


class _KLayoutExporter:
    format_name: ClassVar[str]
    file_extension: ClassVar[str]
    klayout_format: ClassVar[str]

    def export(self, project: Project, geometry: Geometry, path: Path) -> None:
        layout = kdb.Layout()
        layout.dbu = DBU_UM
        top = layout.create_cell(project.name or "TOP")
        for name, region in geometry.layers.items():
            layer = project.layers.get(name)
            if layer is None:
                raise ValueError(f"layer '{name}' has no GDS mapping; add it to the project")
            index = layout.layer(kdb.LayerInfo(layer.gds_layer, layer.gds_datatype, name))
            top.shapes(index).insert(region)
        options = kdb.SaveLayoutOptions()
        options.format = self.klayout_format
        layout.write(str(path), options)


class GdsExporter(_KLayoutExporter):
    format_name = "gds"
    file_extension = ".gds"
    klayout_format = "GDS2"


class OasisExporter(_KLayoutExporter):
    format_name = "oasis"
    file_extension = ".oas"
    klayout_format = "OASIS"


class DxfExporter(_KLayoutExporter):
    format_name = "dxf"
    file_extension = ".dxf"
    klayout_format = "DXF"


BUILTIN = (GdsExporter, OasisExporter, DxfExporter)
