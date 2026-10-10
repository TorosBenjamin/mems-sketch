"""GDSII, OASIS and DXF export (:mod:`mems_sketch.layout`)."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from mems_sketch import layout
from mems_sketch.core.component import DBU_UM, Geometry
from mems_sketch.core.project import Project
from mems_sketch.core.region import IntPolygon, Region
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


class _LayoutExporter:
    format_name: ClassVar[str]
    file_extension: ClassVar[str]
    layout_format: ClassVar[str]
    options: ClassVar[tuple[ExportOption, ...]] = (GRID, TOP_CELL)

    def export(
        self,
        project: Project,
        geometry: Geometry,
        path: Path,
        grid_um: float = DBU_UM,
        top_cell: str = "",
    ) -> None:
        mapping = {n: (x.gds_layer, x.gds_datatype) for n, x in project.layers.items()}
        if hasattr(geometry, "references"):  # a cell of the geometry library (_geom)
            from mems_sketch.export.cells import write_cell

            write_cell(
                geometry,
                path,
                mapping,
                grid_um=grid_um,
                top_cell=top_cell or None,
                file_format=self.layout_format,
            )
            return
        steps = round(grid_um / DBU_UM)  # the grid in the geometry's 1 nm units
        if steps < 1 or abs(steps * DBU_UM - grid_um) > 1e-9 * DBU_UM:
            raise ValueError(f"the grid must be a whole multiple of 1 nm, not {grid_um:g} µm")
        out = layout.Layout(dbu=steps * DBU_UM)
        top = out.cell(top_cell or project.name or "TOP")
        for name, region in geometry.layers.items():
            if region.is_empty():
                continue
            if name not in mapping:
                raise ValueError(f"layer '{name}' has no GDS mapping; add it to the project")
            out.layer_names[mapping[name]] = name
            polygons = region.each_merged()
            if steps > 1:  # every point on a multiple of the grid, then in grid units
                polygons = _on_grid(region, steps * DBU_UM)
            for polygon in polygons:
                top.add(mapping[name], polygon)
        layout.write(out, path, self.layout_format)


def _on_grid(region: Region, grid_um: float) -> list[IntPolygon]:
    from mems_sketch.export.cells import geom

    g = geom()
    faces = []
    for polygon in region.each_merged():
        face = g.Region.polygon([(x * DBU_UM, y * DBU_UM) for x, y in polygon.hull])
        for hole in polygon.holes:
            face = face - g.Region.polygon([(x * DBU_UM, y * DBU_UM) for x, y in hole])
        faces.append(face)
    snapped = g.snap(g.Region.unite(faces), grid_um, 0.005)
    return [
        IntPolygon(
            [tuple(p) for p in hull.tolist()], [[tuple(p) for p in h.tolist()] for h in holes]
        )
        for hull, holes in snapped.polygons
    ]


class GdsExporter(_LayoutExporter):
    format_name = "gds"
    title = "GDSII"
    file_extension = ".gds"
    layout_format = "GDS2"


class OasisExporter(_LayoutExporter):
    format_name = "oasis"
    title = "OASIS"
    file_extension = ".oas"
    layout_format = "OASIS"


class DxfExporter(_LayoutExporter):
    format_name = "dxf"
    title = "DXF"
    file_extension = ".dxf"
    layout_format = "DXF"


BUILTIN = (GdsExporter, OasisExporter, DxfExporter)
