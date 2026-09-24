"""Importing a GDS cell as a component: placed, saved and updated like any other."""

import klayout.db as kdb
import pytest

from mems_sketch.core.process import Layer
from mems_sketch.core.project import Project
from mems_sketch.editing import EditSession
from mems_sketch.storage import load, save


def write_gds(path, boxes, dbu=0.001, cell="FRAME"):
    """A GDS file with ``boxes``: (layer, datatype, x0, y0, x1, y1) in µm, the
    last box inside a sub-cell (so the import has to flatten)."""
    layout = kdb.Layout()
    layout.dbu = dbu
    top = layout.create_cell(cell)
    child = layout.create_cell("PART")
    for index, (layer, datatype, *box) in enumerate(boxes):
        target = child if index == len(boxes) - 1 else top
        target.shapes(layout.layer(layer, datatype)).insert(kdb.DBox(*box))
    top.insert(kdb.DCellInstArray(child.cell_index(), kdb.DTrans()))
    layout.write(str(path))
    return path


@pytest.fixture
def gds(tmp_path):
    return write_gds(
        tmp_path / "Pad frame.gds",
        [(1, 0, 0, 0, 100, 10), (5, 0, 0, 0, 10, 10), (1, 0, 0, 90, 100, 100)],
    )


@pytest.fixture
def session():
    project = Project()
    project.add_layer(Layer("device", 1))
    return EditSession(project)


def area(session, layer="device", component=None):
    region = session.results.geometry(component=component).layers.get(layer)
    return 0.0 if region is None else region.area() / 1e6


def test_an_imported_cell_is_a_component_to_place(session, gds):
    name = session.imports.add(gds)
    assert name == "pad_frame"
    imported = session.project.imports[name]
    assert (imported.cell, imported.layers) == ("FRAME", {"1/0": "device", "5/0": "gds5_0"})
    assert session.project.layers["gds5_0"].gds_layer == 5  # a new layer for 5/0
    session.nodes.add_component(name, x=500)
    assert area(session) == pytest.approx(2000)  # both boxes, the sub-cell's too
    assert area(session, "gds5_0") == pytest.approx(100)
    box = session.results.geometry().layers["device"].bbox()
    assert (box.left, box.right) == (500_000, 600_000)


def test_layers_can_be_mapped_or_left_out(session, gds):
    name = session.imports.add(gds, layers={"1/0": "device", "5/0": ""})
    assert "gds5_0" not in session.project.layers
    session.nodes.add_component(name)
    assert set(session.results.geometry().layers) == {"device"}


def test_a_file_on_another_grid_keeps_its_size(session, tmp_path):
    path = write_gds(tmp_path / "coarse.gds", [(1, 0, 0, 0, 50, 20)], dbu=0.01)
    session.nodes.add_component(session.imports.add(path))
    assert area(session) == pytest.approx(1000)


def test_saved_with_a_copy_of_the_file(session, gds, tmp_path):
    name = session.imports.add(gds, name="frame")
    session.nodes.add_component(name)
    save(session.project, tmp_path / "proj")
    assert (tmp_path / "proj" / "imports" / "Pad frame.gds").read_bytes() == gds.read_bytes()
    again = load(tmp_path / "proj")
    assert again == session.project
    gds.unlink()  # the project does not need the original any more
    assert EditSession(again).results.geometry().layers["device"].area() > 0
    again.imports.clear()
    save(again, tmp_path / "proj")
    assert not (tmp_path / "proj" / "imports").exists()  # nothing left that uses it


def test_reimporting_updates_every_placement(session, gds, tmp_path):
    name = session.imports.add(gds)
    session.nodes.add_component(name)
    session.nodes.add_component(name, x=200)
    newer = write_gds(tmp_path / "v2.gds", [(1, 0, 0, 0, 100, 30), (1, 0, 50, 50, 51, 51)])
    session.imports.reimport(name, newer)
    assert area(session) == pytest.approx(2 * 3001)
    session.undo()
    assert area(session) == pytest.approx(2 * 2000)


def test_names_and_removal(session, gds):
    name = session.imports.add(gds)
    with pytest.raises(ValueError, match="already exists"):
        session.imports.add(gds, name=name)
    with pytest.raises(ValueError, match="already exists|imported"):
        session.components.new(name)  # a component cannot take an import's name
    session.nodes.add_component(name)
    with pytest.raises(ValueError, match="still placed"):
        session.imports.remove(name)
    session.undo()
    session.imports.remove(name)
    assert name not in session.project.imports


def test_a_file_that_is_not_gds_is_refused(session, tmp_path):
    bad = tmp_path / "notes.gds"
    bad.write_text("hello")
    with pytest.raises(ValueError, match="not a readable GDS file"):
        session.imports.add(bad)
