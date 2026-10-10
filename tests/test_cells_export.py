"""Layouts written from the geometry library's cells: on the output grid,
keeping cells and array references (requirements OUT-2 to OUT-4). Needs the
library's Python module, built with -DMGEOM_BUILD_PYTHON=ON (see
src/geom/README.md); skipped without it."""

import os

import pytest
from helpers import read_gds

from mems_sketch.core.project import Layer, Project
from mems_sketch.core.region import Region
from mems_sketch.export.base import export
from mems_sketch.export.cells import geom, write_cell

try:
    g = geom()
except ImportError:
    if os.environ.get("MGEOM_REQUIRED"):  # CI's geometry job: never skip there
        raise
    pytest.skip("the geometry library's Python module is not built", allow_module_level=True)

LAYERS = {"device": (1, 0), "etch": (2, 0)}


def read(path):
    return read_gds(path)


def top_cell(layout):
    (top,) = layout.top_cells()
    return layout.cells[top]


def flat_region(layout, layer=(1, 0)):
    return Region.from_polygons(layout.flat(top_cell(layout).name, layer))


def plate(dx=10.0, columns=10, rows=10, transform=None):
    hole = g.CellBuilder("hole").add("etch", g.Region.circle((0, 0), 2)).build()
    return (
        g.CellBuilder("plate")
        .add("device", g.Region.rect(0, 0, 100, 100))
        .place_array(hole, columns, rows, dx, dx, transform or g.Transform(dx=5, dy=5))
        .build()
    )


def test_arrays_stay_array_references(tmp_path):
    report = write_cell(plate(), tmp_path / "plate.gds", LAYERS)
    layout = read(tmp_path / "plate.gds")
    assert layout.dbu == pytest.approx(0.001)
    assert sorted(layout.cells) == ["hole", "plate"]
    [instance] = top_cell(layout).placements
    assert (instance.columns, instance.rows) == (10, 10)
    assert (instance.column_step, instance.row_step) == ((10000, 0), (0, 10000))
    assert (instance.x, instance.y, instance.angle, instance.mirror) == (5000, 5000, 0, False)
    assert flat_region(layout, (2, 0)).count() == 100
    assert report.flattened == 0
    assert not report.changed_shape
    assert {(e.cell, e.layer) for e in report.layers} == {("plate", "device"), ("hole", "etch")}


def test_the_hierarchy_matches_snapping_the_flat_geometry(tmp_path):
    cell = plate(transform=g.Transform(dx=5, dy=5, angle_deg=90, mirror_x=True))
    write_cell(cell, tmp_path / "plate.gds", LAYERS)
    layout = read(tmp_path / "plate.gds")
    written = flat_region(layout, (2, 0))
    [inst] = top_cell(layout).placements
    assert inst.angle == 90 and inst.mirror  # quarter turns
    expected = Region.from_polygons(
        (hull.tolist(), [h.tolist() for h in holes])
        for hull, holes in g.snap(cell.flat("etch")).polygons
    )
    assert (written ^ expected).is_empty()


@pytest.mark.parametrize(
    "transform, dx",
    [
        (g.Transform(dx=5.0005, dy=5), 10.0),  # an offset off the grid
        (g.Transform(dx=5, dy=5, angle_deg=45), 10.0),  # not a quarter turn
        (g.Transform(dx=5, dy=5, scale=2), 10.0),  # scaled
        (g.Transform(dx=5, dy=5), 10.0005),  # an array step off the grid
    ],
)
def test_placements_that_leave_the_grid_are_flattened(tmp_path, transform, dx):
    cell = plate(dx=dx, columns=3, rows=3, transform=transform)
    report = write_cell(cell, tmp_path / "plate.gds", LAYERS)
    layout = read(tmp_path / "plate.gds")
    assert list(layout.cells) == ["plate"]
    assert report.flattened == 1
    area = flat_region(layout, (2, 0)).area() * layout.dbu**2
    # Each circle split at a 5 nm chord loses about 2/3 of the chord times its
    # circumference.
    expected = cell.flat("etch").area - 9 * 2 / 3 * 0.005 * 2 * 3.14159 * 2 * transform.scale
    assert area == pytest.approx(expected, rel=5e-4)


def test_everything_flat_on_request(tmp_path):
    report = write_cell(plate(), tmp_path / "flat.gds", LAYERS, keep_hierarchy=False)
    assert list(read(tmp_path / "flat.gds").cells) == ["plate"]
    assert report.flattened == 1


def test_a_coarser_grid_and_the_report(tmp_path):
    two = g.Region.rect(0, 0, 1, 1) | g.Region.rect(1.004, 0, 2, 1)
    cell = g.CellBuilder("gap").add("device", two).build()
    report = write_cell(cell, tmp_path / "gap.gds", LAYERS, grid_um=0.01, top_cell="CHIP")
    layout = read(tmp_path / "gap.gds")
    assert layout.dbu == pytest.approx(0.01)
    assert layout.top_cells() == ["CHIP"]
    assert report.changed_shape
    [(cell_name, layer, event)] = report.events
    assert (cell_name, layer) == ("CHIP", "device")
    assert event.change == g.SnapChange.merged
    assert flat_region(layout).count() == 1


def test_cells_with_the_same_name_get_distinct_names(tmp_path):
    a = g.CellBuilder("part").add("device", g.Region.rect(0, 0, 1, 1)).build()
    b = g.CellBuilder("part").add("device", g.Region.rect(0, 0, 2, 2)).build()
    top = g.CellBuilder("top").place(a).place(b, g.Transform(dx=10)).build()
    write_cell(top, tmp_path / "top.gds", LAYERS)
    assert sorted(read(tmp_path / "top.gds").cells) == [
        "part",
        "part$1",
        "top",
    ]


@pytest.mark.parametrize("suffix, magic", [(".oas", b"%SEMI-OASIS"), (".dxf", b"0\nSECTION")])
def test_other_formats(tmp_path, suffix, magic):
    write_cell(plate(columns=2, rows=2), tmp_path / f"plate{suffix}", LAYERS)
    written = (tmp_path / f"plate{suffix}").read_bytes()
    assert written.replace(b"\r\n", b"\n").startswith(magic)  # DXF is text: CRLF on Windows


def test_errors(tmp_path):
    with pytest.raises(ValueError, match="etch have no GDS mapping"):
        write_cell(plate(), tmp_path / "x.gds", {"device": (1, 0)})
    with pytest.raises(ValueError, match="no layout format"):
        write_cell(plate(), tmp_path / "x.svg", LAYERS)


def test_the_gds_exporter_takes_a_library_cell(tmp_path):
    project = Project(name="chip")
    project.add_layer(Layer("device", 1, 0))
    project.add_layer(Layer("etch", 2, 0))
    path = export(project, tmp_path / "chip.gds", geometry=plate(), options={"grid_um": 0.005})
    layout = read(path)
    assert layout.dbu == pytest.approx(0.005)
    assert layout.top_cells() == ["plate"]
    assert len(layout.cells) == 2
