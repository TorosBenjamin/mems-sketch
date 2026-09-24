"""The New Project wizard."""

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox

from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.new_project import NewProjectDialog

EXAMPLES = Path(__file__).parent.parent / "examples"


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    return w


@pytest.fixture
def dialog(qtbot, tmp_path):
    d = NewProjectDialog(str(tmp_path))
    qtbot.addWidget(d)
    return d


def test_it_suggests_a_free_name_and_shows_where_it_goes(dialog, tmp_path):
    (tmp_path / "untitled").mkdir()
    dialog.kinds.setCurrentRow(1)
    dialog.kinds.setCurrentRow(0)  # suggested again, now that untitled is taken
    assert dialog.name.text() == "untitled2"
    assert dialog.folder() == tmp_path / "untitled2"
    assert str(tmp_path / "untitled2") in dialog.target.text()
    assert dialog.create_button.isEnabled()


def test_a_name_typed_by_the_user_is_kept_when_switching_kind(dialog, tmp_path):
    dialog.name.setText("")
    dialog.name.textEdited.emit("Comb test")
    dialog.name.setText("Comb test")
    dialog.kinds.setCurrentRow(1)
    assert dialog.name.text() == "Comb test"
    assert dialog.folder() == tmp_path / "Comb_test"  # a folder-safe name
    assert dialog.values()["library"] is True


def test_it_refuses_an_empty_name_or_an_existing_project(dialog, tmp_path):
    dialog.name.setText(" ")
    assert not dialog.create_button.isEnabled() and "name" in dialog.target.text()
    (tmp_path / "chip").mkdir()
    (tmp_path / "chip" / "project.yaml").write_text("")
    dialog.name.setText("chip")
    assert not dialog.create_button.isEnabled()
    assert dialog.target.property("error") and "already holds" in dialog.target.text()


def test_libraries_and_the_process_are_passed_on(dialog):
    dialog.add_library(str(EXAMPLES / "libraries" / "mems_std"))
    assert dialog.libraries.count() == 1 and "mems_std" in dialog.libraries.item(0).text()
    dialog.set_process_from(str(EXAMPLES / "resonator"))
    values = dialog.values()
    assert values["libraries"] == [str(EXAMPLES / "libraries" / "mems_std")]
    assert values["process_from"] == str(EXAMPLES / "resonator")
    dialog.libraries.item(0).setSelected(True)
    dialog.remove.click()
    assert dialog.libraries.count() == 0


def test_new_project_creates_and_saves_it_with_its_libraries(window, tmp_path, monkeypatch):
    def fill(dialog):
        dialog.location.setText(str(tmp_path))
        dialog.name.setText("chip")
        dialog.add_library(str(EXAMPLES / "libraries" / "mems_std"))
        return dialog.DialogCode.Accepted

    monkeypatch.setattr(window, "show_dialog", fill)
    window.new_project()
    assert window.document.path == tmp_path / "chip" and not window.document.dirty
    assert (tmp_path / "chip" / "project.yaml").exists()
    assert "mems_std" in window.document.project.libraries
    assert window.windowTitle().startswith("chip") or "chip" in window.windowTitle()
    assert [v.component for v in window.area.views()] == ["top"]


def test_cancelling_the_wizard_changes_nothing(window, monkeypatch):
    before = window.document.project
    monkeypatch.setattr(window, "show_dialog", lambda dialog: dialog.DialogCode.Rejected)
    window.new_project()
    assert window.document.project is before
