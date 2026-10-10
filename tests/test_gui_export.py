"""File › Export… asks for a format's options in a dialog built from their
declarations, and remembers them."""

import shutil
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from helpers import read_gds
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QLineEdit,
    QMessageBox,
    QSpinBox,
)

from mems_sketch.export.base import ExportOption
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.export_dialog import ExportOptionsDialog, remember, remembered

EXAMPLES = Path(__file__).parent.parent / "examples"
OPTIONS = (
    ExportOption("size", 2.5, "Size", "How big.", minimum=0, maximum=10, suffix=" µm"),
    ExportOption("count", 3, "Count", minimum=1),
    ExportOption("flag", False, "Flag"),
    ExportOption("mode", "fast", "Mode", choices=("fast", "exact")),
    ExportOption("name", "", "Name"),
)


def test_the_dialog_has_an_editor_per_option(qtbot):
    values = {"size": 4.0, "count": 2, "flag": True, "mode": "exact", "name": "x"}
    dialog = ExportOptionsDialog("Probe", OPTIONS, values)
    qtbot.addWidget(dialog)
    kinds = {name: type(editor) for name, editor in dialog.editors.items()}
    assert kinds == {
        "size": QDoubleSpinBox,
        "count": QSpinBox,
        "flag": QCheckBox,
        "mode": QComboBox,
        "name": QLineEdit,
    }
    assert dialog.editors["size"].toolTip() == "How big."
    assert dialog.editors["size"].maximum() == 10
    assert dialog.values() == values
    dialog.editors["count"].setValue(9)
    assert dialog.values()["count"] == 9


def test_values_are_remembered_per_format(window):
    settings = window.settings
    assert remembered(settings, "probe", OPTIONS)["size"] == 2.5
    remember(settings, "probe", OPTIONS, {**remembered(settings, "probe", OPTIONS), "size": 7.0})
    assert remembered(settings, "probe", OPTIONS)["size"] == 7.0
    assert remembered(settings, "other", OPTIONS)["size"] == 2.5
    settings.set_value("export/probe/count", "nonsense")  # no longer fits: the default
    assert remembered(settings, "probe", OPTIONS)["count"] == 3


@pytest.fixture
def resonator(tmp_path):
    for name in ("resonator", "libraries"):
        shutil.copytree(
            EXAMPLES / name, tmp_path / name, ignore=shutil.ignore_patterns(".mems-sketch")
        )
    return tmp_path / "resonator"


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    return w


def test_export_asks_for_the_options(window, resonator, monkeypatch, tmp_path):
    window.open_project(str(resonator))
    target = tmp_path / "chip"  # no suffix: the chosen format's is added
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), "GDSII (*.gds)")
    )

    def accept(dialog):
        dialog.editors["grid_um"].setValue(0.005)
        dialog.editors["top_cell"].setText("CHIP")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ExportOptionsDialog, "exec", accept)
    window.export_file()
    layout = read_gds(f"{target}.gds")
    assert layout.dbu == pytest.approx(0.005)
    assert layout.top_cells() == ["CHIP"]
    assert window.settings.value("export/gds/grid_um") == "0.005"


def test_cancelling_the_options_exports_nothing(window, resonator, monkeypatch, tmp_path):
    window.open_project(str(resonator))
    target = tmp_path / "chip.gds"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), "GDSII (*.gds)")
    )
    monkeypatch.setattr(ExportOptionsDialog, "exec", lambda dialog: QDialog.DialogCode.Rejected)
    window.export_file()
    assert not target.exists()


def test_a_typed_extension_decides_the_format(window, resonator, monkeypatch, tmp_path):
    window.open_project(str(resonator))
    target = tmp_path / "chip.oas"  # typed while the GDSII filter was chosen
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), "GDSII (*.gds)")
    )
    titles = []

    def accept(dialog):
        titles.append(dialog.windowTitle())
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ExportOptionsDialog, "exec", accept)
    window.export_file()
    assert titles == ["Export as OASIS"]
    assert target.read_bytes().startswith(b"%SEMI-OASIS")
