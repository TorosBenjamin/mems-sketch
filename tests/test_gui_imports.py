"""File › Import GDS…: the dialog, the Imported group, placing and re-importing."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.import_dialog import LEAVE_OUT, ImportDialog


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    return w


@pytest.fixture
def gds(tmp_path, write_gds):
    boxes = [(1, 0, 0, 0, 100, 10), (7, 0, 0, 0, 10, 10), (1, 0, 0, 90, 100, 100)]
    return write_gds(tmp_path / "frame.gds", boxes)


def accept(monkeypatch, adjust=lambda dialog: None):
    def exec_(dialog):
        adjust(dialog)
        return True

    monkeypatch.setattr(ImportDialog, "exec", exec_)


def test_the_dialog_proposes_a_name_the_cell_and_the_layers(window, gds):
    dialog = ImportDialog(window.document, gds)
    assert dialog.name.text() == "frame"
    assert dialog.cell.currentText() == "FRAME"
    rows = {
        dialog.layers.item(r, 0).text(): dialog.layers.cellWidget(r, 1).currentText()
        for r in range(dialog.layers.rowCount())
    }
    assert rows == {"1/0": "device", "7/0": "gds7_0 (new)"}
    assert dialog.choices()["layers"] == {"1/0": "device", "7/0": "gds7_0"}


def test_importing_lists_the_cell_and_places_it(window, gds, monkeypatch):
    def leave_out_7(dialog):
        combo = dialog.layers.cellWidget(1, 1)
        combo.setCurrentIndex(combo.findText(LEAVE_OUT))

    accept(monkeypatch, leave_out_7)
    name = window.components.import_gds(str(gds))
    assert name == "frame" and "gds7_0" not in window.document.project.layers
    tree = window.components.tree
    groups = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
    assert "Imported" in groups
    path = window.document.nodes.add_component(name)
    assert window.document.node(path).component == "frame"
    assert window.document.results.geometry().layers["device"].area() > 0


def test_an_imported_cell_opens_read_only_and_says_where_it_is_from(window, gds, monkeypatch):
    accept(monkeypatch)
    window.components.import_gds(str(gds))
    window.open_component("frame")
    assert window.document.read_only
    assert window.tree.topLevelItem(0).text(0) == "Imported component"


def test_the_components_menu_reimports_and_removes(window, gds, tmp_path, monkeypatch, write_gds):
    accept(monkeypatch)
    window.components.import_gds(str(gds))
    items = window.components.tree.findItems("frame", Qt.MatchFlag.MatchRecursive)
    menu = window.components.menu_for(items[0])
    texts = [a.text() for a in menu.actions()]
    assert "Re-import…" in texts and "Remove import" in texts
    newer = write_gds(tmp_path / "v2.gds", [(1, 0, 0, 0, 50, 50), (1, 0, 0, 0, 1, 1)])
    window.components.reimport("frame", str(newer))
    assert window.document.project.imports["frame"].data == newer.read_bytes()
    window.document.imports.remove("frame")
    assert "frame" not in window.document.project.imports


def test_a_file_that_is_not_gds_is_reported(window, tmp_path):
    bad = tmp_path / "x.gds"
    bad.write_text("nope")
    errors = []
    window.components.error.connect(errors.append)
    assert window.components.import_gds(str(bad)) is None
    assert errors and "GDS" in errors[0]
