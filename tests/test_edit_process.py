"""A process of the project's own, defined and edited in the GUI (#307): a
library's process copied into the project, a new process, and layers with a
colour of their own."""

import dataclasses

import pytest

from mems_sketch.core.process import Layer
from mems_sketch.editing import EditSession
from mems_sketch.storage import load


@pytest.fixture
def surface(tmp_path) -> EditSession:
    session = EditSession()
    session.create(tmp_path / "chip", "chip", process="mems.surface")
    return session


def test_a_librarys_process_is_copied_into_the_project(surface):
    session = surface
    session.process.set_constant("undercut", 3.0)  # a change to the library's process
    rule = next(iter(session.project.process.rules))
    session.process.set_reason(rule, "our fab etches further")
    with pytest.raises(ValueError, match="copy it into the project"):
        session.process.add_layer()
    name = session.process.make_own()
    project = session.project
    assert name == "surface" and project.process_name == "surface"
    assert project.base_process is None and project.processes["surface"] is project.process
    assert project.process.constants["undercut"] == 3.0  # the change came along
    assert project.process.rules[rule].message == "our fab etches further"
    assert [lv.layer for lv in project.process.levels] == ["wiring", "device", "metal"]
    assert "Copied from mems.surface." in project.process.description
    layer = session.process.add_layer()  # all of it editable now
    assert layer in project.layers
    session.undo()
    session.undo()
    assert session.project.process_name == "mems.surface"  # one undo takes the copy back


def test_the_copy_is_saved_and_read_back(surface):
    session = surface
    session.process.make_own("acme")
    name = session.process.add_layer()
    layer = session.project.layers[name]
    session.process.set_layer(name, dataclasses.replace(layer, color="#ff8800"))
    session.save()
    project = load(session.path)
    assert project.process_name == "acme" and project.base_process is None
    assert project.layers[name].color == "#ff8800"
    assert "device" in project.layers


def test_a_new_process_from_scratch(surface):
    session = surface
    name = session.process.new_process("fab2")
    assert name == "fab2" and session.project.process_name == "fab2"
    assert set(session.project.layers) == {"device", "anchor", "metal"}
    with pytest.raises(ValueError, match="already"):
        session.process.new_process("fab2")
    with pytest.raises(ValueError, match="not a valid process name"):
        session.process.new_process("my fab")
    session.process.use("mems.surface")  # the new one stays in the project
    assert "fab2" in session.project.process_names()


def test_a_project_process_is_not_copied_again():
    session = EditSession()
    with pytest.raises(ValueError, match="own process already"):
        session.process.make_own()


def test_a_layer_colour_must_look_like_one(tmp_path):
    from mems_sketch.storage.project_files import ProjectFormatError, process_from_data

    process = process_from_data({"layers": {"device": {"gds": [1, 0], "color": "#4A90D9"}}})
    assert process.layers["device"].color == "#4a90d9"
    with pytest.raises(ProjectFormatError, match="is not like"):
        process_from_data({"layers": {"device": {"gds": [1, 0], "color": "blue"}}})


# -- the Process tab ------------------------------------------------------------------


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QMessageBox

    from mems_sketch.gui.app import MainWindow

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    qtbot.waitExposed(w)
    w.document.create(tmp_path / "chip", "chip", process="mems.surface")
    return w


def test_the_process_tab_copies_the_librarys_process_in(window):
    window.open_process()
    view = window.area.process_view
    assert view.copy_in.isVisible() and view.layers.library_note.isVisible()
    assert not view.layers.actions.buttons["Add layer"].isEnabled()
    view.copy_in.click()
    assert window.document.project.process_name == "surface"
    assert not view.copy_in.isVisible() and not view.layers.library_note.isVisible()
    assert view.layers.actions.buttons["Add layer"].isEnabled()


def test_a_new_process_from_the_tab(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    window.open_process()
    view = window.area.process_view
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("acme", True))
    view.new.click()
    assert window.document.project.process_name == "acme"


def test_a_layers_colour_is_chosen_and_drawn(window, monkeypatch):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QColorDialog

    window.document.process.make_own()
    window.open_process()
    panel = window.area.process_view.layers
    row = panel._layer_names.index("device")
    monkeypatch.setattr(QColorDialog, "getColor", lambda *a, **k: QColor("#12ab34"))
    panel._cell_double_clicked(row, panel.COLOR)
    assert window.document.project.layers["device"].color == "#12ab34"
    assert panel.layers.item(row, panel.COLOR).text() == "#12ab34"
    assert window.layers.colors["device"].name() == "#12ab34"  # what the canvas draws with
    panel.choose_color("device", None)
    assert window.document.project.layers["device"].color is None


def test_renaming_a_layer_keeps_its_colour(window):
    session = window.document
    session.process.make_own()
    device = session.project.layers["device"]
    session.process.set_layer("device", dataclasses.replace(device, color="#000080"))
    session.process.set_layer("device", Layer("structure", device.gds_layer, 0, "#000080"))
    assert session.project.layers["structure"].color == "#000080"


def test_a_layers_z_and_thickness_are_edited_as_expressions(window):
    window.document.process.make_own()
    window.open_process()
    panel = window.area.process_view.layers
    row = panel._layer_names.index("device")
    panel.layers.item(row, panel.Z).setText("process.t_wiring + process.t_oxide")
    panel.layers.item(row, panel.THICKNESS).setText("3")
    layer = window.document.project.layers["device"]
    assert (layer.z, layer.thickness) == ("process.t_wiring + process.t_oxide", 3.0)
    assert window.document.project.process.heights()["device"] == (2.5, 3.0)
    assert "2.5" in panel.layers.item(row, panel.THICKNESS).toolTip()
    panel.layers.item(row, panel.THICKNESS).setText("")  # empty: not extruded
    assert "device" not in window.document.project.process.heights()
