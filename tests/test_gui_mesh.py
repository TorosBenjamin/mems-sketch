"""The Mesh tool window (#50, #72): editing a component's mesh settings, the
refinements on the canvas, the preview, and exporting the mesh."""

import shutil
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from mems_sketch import meshing

QTimerClass = pytest.importorskip("PySide6.QtCore").QTimer

needs_gmsh = pytest.mark.skipif(not meshing.available(), reason="gmsh is not installed")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    from mems_sketch.gui.app import MainWindow

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    qtbot.waitExposed(w)
    folder = shutil.copytree(ROOT / "examples" / "resonator", tmp_path / "resonator")
    w.open_project(str(folder))
    w.tool_windows.open("mesh")
    return w


def apply(field, text: str) -> None:
    field.setText(text)
    field.returnPressed.emit()


@needs_gmsh
def test_sizes_are_edited_as_expressions_and_undone(window):
    panel = window.mesh
    apply(panel.size, "plate / 10")
    apply(panel.growth, "1.3")
    apply(panel.layer_fields["anchor"], "w_finger")
    mesh = window.document.project.components["top"].mesh
    assert (mesh.size, mesh.growth, mesh.layers) == ("plate / 10", 1.3, {"anchor": "w_finger"})
    assert panel.size.property("expression")  # shown like any expression, with its value
    apply(panel.layer_fields["anchor"], "")  # empty: back to the largest size
    assert window.document.project.components["top"].mesh.layers == {}
    window.document.undo()
    assert window.document.project.components["top"].mesh.layers == {"anchor": "w_finger"}


@needs_gmsh
def test_a_bad_value_is_refused_and_said(window):
    errors = []
    window.mesh.error.connect(errors.append)
    apply(window.mesh.growth, "1 +")
    assert errors and window.document.project.components["top"].mesh.growth == 1.2


@needs_gmsh
def test_refinements_are_added_edited_and_drawn(window):
    panel = window.mesh
    for kind in ("point", "box", "edges"):
        panel.add(kind)
    refine = window.document.project.components["top"].mesh.refine
    assert [r.kind for r in refine] == ["point", "box", "edges"]
    panel.refinements.setCurrentRow(0)
    apply(panel.refine_fields["radius"], "w_finger * 3")
    assert window.document.project.components["top"].mesh.refine[0].radius == "w_finger * 3"
    assert {k[0] for k in panel.refinement_marks()} == {"point", "box"}
    assert "refine" in window.canvas._mesh_items  # dashed on the canvas
    window.tool_windows.close("mesh")
    assert "refine" not in window.canvas._mesh_items  # only while the window is open
    window.tool_windows.open("mesh")
    panel.refinements.setCurrentRow(2)
    panel._remove()
    assert len(window.document.project.components["top"].mesh.refine) == 2


@needs_gmsh
def test_the_preview_is_drawn_and_kept_out_of_date_when_the_design_changes(window):
    panel = window.mesh
    apply(panel.size, "20")
    panel.automatic.click()  # off: a quick, coarse mesh of the whole device
    panel.live.setChecked(False)
    mesh = panel.preview()
    assert mesh is not None and window.canvas.mesh_shown()
    assert "triangles" in panel.summary.text() and "quality" in panel.summary.text()
    window.document.parameters.set("plate", 140.0)
    window.mesh.refresh()
    assert panel.mesh is mesh and panel.stale  # kept, said to be out of date
    assert panel.summary.text().startswith("Out of date") and window.canvas.mesh_shown()
    window.document.undo()
    window.mesh.refresh()
    assert not panel.stale  # the design it was made from again


@needs_gmsh
def test_a_bake_runs_in_another_process_and_the_window_answers(window, qtbot):
    import os

    window.open_component("suspension")
    panel = window.mesh
    ticks = []
    timer = QTimerClass(window)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(10)
    assert panel.start_bake() and panel.baking
    assert panel._bake._process.pid != os.getpid()
    assert panel.cancel_button.isVisible()
    qtbot.waitUntil(lambda: not panel.baking, timeout=60_000)
    timer.stop()
    assert panel.mesh is not None and ticks  # the window went on meanwhile
    assert not panel.cancel_button.isVisible()


@needs_gmsh
def test_cancel_stops_a_bake(window, qtbot):
    panel = window.mesh  # the whole resonator, automatic sizes: several seconds
    assert panel.start_bake()
    process = panel._bake._process
    panel.cancel_bake()
    qtbot.waitUntil(lambda: not process.is_alive(), timeout=10_000)
    assert not panel.baking and panel.mesh is None
    assert panel.summary.text() == "Cancelled."


@needs_gmsh
def test_a_quick_mesh_is_baked_again_after_an_edit(window, qtbot):
    window.open_component("suspension")
    panel = window.mesh
    apply(panel.size, "20")
    panel.automatic.click()  # off: quick
    first = panel.preview()
    assert first is not None and panel._bake_seconds < 5
    panel._bake_seconds = 0.1  # (as quick as on a fast machine)
    apply(panel.size, "15")  # an edit: out of date, then baked again by itself
    assert panel.stale
    qtbot.waitUntil(lambda: panel.mesh is not first and not panel.baking, timeout=60_000)
    assert not panel.stale and panel.mesh.settings.size == 15


@needs_gmsh
def test_export_uses_the_baked_mesh_when_current(window, monkeypatch, tmp_path):
    from mems_sketch.export.base import export

    window.open_component("suspension")
    panel = window.mesh
    apply(panel.size, "20")
    mesh = panel.preview()
    assert mesh is not None

    def no_meshing(*args, **kwargs):
        raise AssertionError("meshed again")

    monkeypatch.setattr(meshing, "_entities", no_meshing)
    document = window.document
    path, _ = document.export_with_report(tmp_path / "s.msh", "msh", {})
    assert '"spring.device"' in path.read_text()
    elmer, _ = document.export_with_report(tmp_path / "s.elmer", "elmer", {})
    assert (elmer / "mesh.header").read_text().split()[1] == str(mesh.element_count)
    with pytest.raises(AssertionError, match="meshed again"):  # changed: meshed anew
        export(document.project, tmp_path / "x.msh", component="suspension", params={"turns": 4})


@needs_gmsh
def test_a_library_component_shows_its_settings_read_only(window):
    window.open_component("mems.anchor")
    window.mesh.refresh()
    assert window.mesh.size.isReadOnly()
    assert not window.mesh.refine_actions.buttons["Add a refinement"].isEnabled()


@needs_gmsh
def test_export_from_the_panel_offers_the_mesh_formats(window, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QDialog, QFileDialog

    from mems_sketch.gui.export_dialog import ExportOptionsDialog

    offered = []
    target = tmp_path / "resonator.elmer"

    def save_as(*args, **kwargs):
        offered.append(args[3].split(";;")[0])
        return str(target), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", save_as)
    monkeypatch.setattr(ExportOptionsDialog, "exec", lambda d: QDialog.DialogCode.Accepted)
    apply(window.mesh.size, "20")
    window.mesh.automatic.click()  # off: quick
    window.mesh.export_button.click()
    assert offered[0].startswith("Mesh: gmsh")
    assert (target / "mesh.header").is_file()


def test_without_gmsh_the_panel_says_how_to_get_it(qtbot, monkeypatch):
    from mems_sketch.editing import EditSession
    from mems_sketch.gui.mesh_panel import MeshPanel

    monkeypatch.setattr(meshing, "available", lambda: False)
    panel = MeshPanel(EditSession())
    qtbot.addWidget(panel)
    assert panel.unavailable.isVisibleTo(panel) and not panel.page.isVisibleTo(panel)
    assert "mems-sketch[mesh]" in panel.unavailable.label.text()


@needs_gmsh
def test_a_3d_preview_is_counted_not_drawn(window):
    window.open_component("suspension")  # small: a 3D mesh of the whole device is big
    panel = window.mesh
    apply(panel.size, "10")
    panel.dimensions.setCurrentText("3D")
    mesh = panel.preview()  # the example's process (mems.surface) places its layers
    assert mesh is not None and mesh.dim == 3
    assert "prisms" in panel.summary.text()
    assert not window.canvas.mesh_shown()


@needs_gmsh
def test_the_element_order_and_prisms_through_are_set(window):
    window.open_component("suspension")
    panel = window.mesh
    panel.refresh()
    assert panel.order.currentIndex() == 1 and panel.through.value() == 2  # the defaults
    panel.order.setCurrentIndex(0)
    panel.order.activated.emit(0)
    panel.through.setValue(0)
    panel.through.editingFinished.emit()
    mesh = window.document.project.components["suspension"].mesh
    assert (mesh.order, mesh.through) == (1, 0)
    window.document.undo()
    assert window.document.project.components["suspension"].mesh.through == 2


@needs_gmsh
def test_the_automatic_map_is_turned_off_and_on_and_shown(window):
    window.open_component("suspension")
    panel = window.mesh
    panel.refresh()
    assert panel.automatic.isChecked() and panel.across.isEnabled()
    panel.automatic.click()
    assert window.document.project.components["suspension"].mesh.automatic is False
    assert not panel.across.isEnabled()
    panel.automatic.click()
    apply(panel.across, "3")
    assert window.document.project.components["suspension"].mesh.across == 3
    panel.show_map.setChecked(True)
    marks = panel.size_map_marks()
    assert marks and all(size < 20 for _, _, size in marks)
    assert any(key.startswith("sizes") for key in window.canvas._mesh_items)  # drawn
    panel.show_map.setChecked(False)
    assert not any(key.startswith("sizes") for key in window.canvas._mesh_items)


@needs_gmsh
def test_the_preview_names_the_shapes(window):
    window.open_component("suspension")
    mesh = window.mesh.preview()
    assert mesh is not None
    assert {"spring.device", "anchor.anchor"} <= set(mesh.parts)
