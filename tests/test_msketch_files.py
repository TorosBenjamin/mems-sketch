"""Opening projects without the file picker (#138): ``.msketch`` files told apart
by their contents (#139), double-clicking them (#140), and pickers inside the
app for libraries and processes, with a preview when importing (#143)."""

import uuid
from pathlib import Path

import pytest

from mems_sketch import RefShape
from mems_sketch.core.project import new_project
from mems_sketch.storage import load, save
from mems_sketch.storage.project_files import (
    ProjectFormatError,
    file_kind,
    item_file,
    locate,
    manifest_file,
    use_msketch_names,
)

ROOT = Path(__file__).parent.parent


def project_with_private(tmp_path: Path) -> Path:
    project = new_project("chip")
    project.components["comb"] = project.components["top"].model_copy(update={"name": "comb"})
    project.components["comb/finger"] = project.components["top"].model_copy(
        update={"name": "comb/finger"}
    )
    return save(project, tmp_path / "chip")


# -- #139: one extension, told apart by what is in the file ------------------------------


def test_a_new_project_is_written_with_msketch_names(tmp_path):
    folder = project_with_private(tmp_path)
    assert manifest_file(folder) == folder / "chip.msketch"
    assert (folder / "components" / "comb" / "comb.msketch").is_file()
    assert (folder / "components" / "comb" / "finger" / "finger.msketch").is_file()
    assert (folder / "processes" / "main" / "main.msketch").is_file()
    assert not list(folder.rglob("*.yaml"))
    assert file_kind(folder / "chip.msketch") == "project"
    assert file_kind(folder / "components" / "comb" / "comb.msketch") == "component"
    assert file_kind(folder / "processes" / "main" / "main.msketch") == "process"
    assert (
        (folder / "components" / "comb" / "comb.msketch")
        .read_text()
        .startswith("kind: component\n")
    )


def test_old_names_still_load_and_are_kept_when_saved(tmp_path):
    old = ROOT / "tests" / "data" / "mems_std"  # a library written before #139
    assert (old / "project.yaml").is_file()
    project = load(old)
    folder = tmp_path / "copy"
    save(project, folder)  # a new folder: new names
    assert manifest_file(folder).suffix == ".msketch"
    kept = tmp_path / "kept"
    (kept).mkdir()
    (kept / "project.yaml").write_text((folder / "copy.msketch").read_text())
    for path in folder.rglob("*.msketch"):
        if path.parent != folder:
            target = kept / path.parent.relative_to(folder)
            target.mkdir(parents=True, exist_ok=True)
            name = "component.yaml" if file_kind(path) == "component" else "process.yaml"
            text = path.read_text().split("\n", 1)[1] or "{}\n"  # without its kind: header
            (target / name).write_text(text)
    save(load(kept), kept)  # saving keeps the names it has
    assert (kept / "project.yaml").is_file() and not list(kept.rglob("*.msketch"))


def test_the_old_names_are_renamed_to_msketch(tmp_path):
    folder = project_with_private(tmp_path)
    before = load(folder)
    # make it an old project first
    manifest = folder / "chip.msketch"
    manifest.rename(folder / "project.yaml")
    for path in list(folder.rglob("*.msketch")):
        name = "component.yaml" if file_kind(path) == "component" else "process.yaml"
        path.with_name(name).write_text(path.read_text().split("\n", 1)[1] or "{}\n")
        path.unlink()
    assert load(folder) == before
    renamed = use_msketch_names(folder)
    assert len(renamed) == 5  # the manifest, three components and the process
    assert manifest_file(folder).name == "chip.msketch" and not list(folder.rglob("*.yaml"))
    assert load(folder) == before


def test_rename_files_on_the_command_line(tmp_path, capsys):
    from mems_sketch.cli import main

    folder = project_with_private(tmp_path)
    assert main(["rename-files", str(folder)]) == 0
    assert "has .msketch names already" in capsys.readouterr().out


def test_two_files_for_one_thing_is_an_error(tmp_path):
    folder = project_with_private(tmp_path)
    (folder / "project.yaml").write_text((folder / "chip.msketch").read_text())
    with pytest.raises(ProjectFormatError, match="keep one project file"):
        load(folder)
    (folder / "project.yaml").unlink()
    comb = folder / "components" / "comb"
    (comb / "component.yaml").write_text("description: twin\n")
    with pytest.raises(ProjectFormatError, match="keep one component file"):
        load(folder)


def test_a_file_must_be_what_its_place_says(tmp_path):
    folder = project_with_private(tmp_path)
    comb = folder / "components" / "comb" / "comb.msketch"
    comb.write_text(comb.read_text().replace("kind: component", "kind: process"))
    with pytest.raises(ProjectFormatError, match="is a process file, not a component's"):
        load(folder)


def test_a_renamed_component_folder_still_finds_its_file(tmp_path):
    folder = project_with_private(tmp_path)
    (folder / "components" / "comb").rename(folder / "components" / "combs")
    manifest = manifest_file(folder)
    manifest.write_text(
        manifest.read_text().replace("comb: components/comb", "comb: components/combs")
    )
    assert item_file(folder / "components" / "combs", "component.yaml").name == "comb.msketch"
    assert "comb/finger" in load(folder).components


def test_any_file_says_what_opening_it_opens(tmp_path):
    folder = project_with_private(tmp_path)
    assert locate(folder) == (folder, "project", None)
    assert locate(folder / "chip.msketch") == (folder, "project", None)
    finger = folder / "components" / "comb" / "finger" / "finger.msketch"
    assert locate(finger) == (folder, "component", "comb/finger")
    process = folder / "processes" / "main" / "main.msketch"
    assert locate(process) == (folder, "process", "main")
    assert set(load(finger).components) == set(load(folder).components)


# -- #140: double-clicking a file -----------------------------------------------------


def test_the_linux_file_type_and_desktop_entry(tmp_path):
    import xml.etree.ElementTree as ET

    from mems_sketch.gui import associations

    written = associations.install_linux(share=tmp_path, icon=b"png")
    names = {p.relative_to(tmp_path).as_posix() for p in written}
    assert names == {
        "mime/packages/mems-sketch.xml",
        "applications/mems-sketch.desktop",
        "icons/hicolor/256x256/apps/mems-sketch.png",
    }
    desktop = (tmp_path / "applications" / "mems-sketch.desktop").read_text()
    assert "MimeType=application/x-mems-sketch;" in desktop and " %f" in desktop
    ET.fromstring(associations.MIME_XML)
    # The AppImage ships the same type and entry (packaging/linux).
    packaged = (ROOT / "packaging" / "linux" / "mems-sketch-mime.xml").read_text()
    assert packaged == associations.MIME_XML
    entry = (ROOT / "packaging" / "linux" / "mems-sketch.desktop").read_text()
    assert "MimeType=application/x-mems-sketch;" in entry


def test_the_windows_installer_and_macos_bundle_register_msketch():
    iss = (ROOT / "packaging" / "windows" / "mems-sketch.iss").read_text()
    assert "ChangesAssociations=yes" in iss and 'Subkey: "Software\\Classes\\.msketch"' in iss
    spec = (ROOT / "packaging" / "mems-sketch.spec").read_text()
    assert "UTExportedTypeDeclarations" in spec and '["msketch"]' in spec


def test_a_second_start_hands_its_file_to_the_running_app(qtbot):
    pytest.importorskip("PySide6")
    from mems_sketch.gui import associations

    name = f"mems-sketch-test-{uuid.uuid4().hex}"
    opened = []
    server = associations.listen(opened.append, name)
    try:
        assert associations.forward("some/file.msketch", name)
        qtbot.waitUntil(lambda: bool(opened), timeout=3000)
        assert opened[0].endswith("some/file.msketch")
    finally:
        server.close()
    assert not associations.forward("x.msketch", f"nobody-{uuid.uuid4().hex}")


def test_macos_open_events_are_taken(qtbot):
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QFileOpenEvent
    from PySide6.QtWidgets import QApplication

    from mems_sketch.gui.associations import OpenEvents

    opened = []
    events = OpenEvents(opened.append)
    app = QApplication.instance()
    app.installEventFilter(events)
    try:
        QApplication.sendEvent(app, QFileOpenEvent("/tmp/chip.msketch"))
    finally:
        app.removeEventFilter(events)
    assert opened == ["/tmp/chip.msketch"]


@pytest.fixture
def window(qtbot, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QMessageBox

    from mems_sketch.gui.app import MainWindow

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    qtbot.waitExposed(w)
    return w


def test_a_file_goes_to_the_window_with_its_project(window, tmp_path):
    from mems_sketch.gui import associations

    folder = project_with_private(tmp_path)
    window.open_project(str(folder))
    made = []
    finger = folder / "components" / "comb" / "finger" / "finger.msketch"
    found = associations.route(str(finger), [window], lambda: made.append(1))
    assert found is window and not made
    assert window.document.active == "comb/finger"  # its tab


def test_opening_a_process_file_opens_the_process_tab(window, tmp_path):
    folder = project_with_private(tmp_path)
    window.open_project(str(folder / "processes" / "main" / "main.msketch"))
    assert window.document.path == folder
    assert window.area.process_view is not None


def test_the_file_menu_renames_an_old_project(window, tmp_path):
    folder = project_with_private(tmp_path)
    (folder / "chip.msketch").rename(folder / "project.yaml")
    window.open_project(str(folder))
    assert window.actions_.msketch_names.isVisible()
    window.use_msketch_names()
    assert (folder / "chip.msketch").is_file() and not (folder / "project.yaml").exists()
    assert not window.actions_.msketch_names.isVisible()


# -- #143: pickers inside the app ------------------------------------------------------


def test_the_picker_filters_and_chooses(qtbot):
    pytest.importorskip("PySide6")
    from mems_sketch.gui.component_picker import Entry
    from mems_sketch.gui.picker import Picker

    entries = [
        Entry("mems.surface", "mems", "One released layer", "process"),
        Entry("mems.soi", "mems", "Silicon on insulator", "process"),
        Entry("main", "This project", "Its own", "process"),
    ]
    picker = Picker("Choose", entries)
    qtbot.addWidget(picker)
    assert picker.visible_names() == ["mems.surface", "mems.soi", "main"]
    picker.search.setText("insulator")
    assert picker.visible_names() == ["mems.soi"]
    picker.search.setText("nothing like it")
    assert picker.visible_names() == [] and not picker.ok.isEnabled()
    picker.search.setText("")
    picker.choose("main")
    assert picker.chosen.name == "main"


def test_libraries_are_offered_from_next_to_the_project(window, tmp_path, monkeypatch):
    import shutil

    folder = project_with_private(tmp_path)
    shutil.copytree(ROOT / "tests" / "data" / "mems_std", tmp_path / "acme")
    window.open_project(str(folder))
    panel = window.components
    offered = []

    def choose(picker):
        offered.extend(picker.visible_names())
        picker.choose("acme")
        return True

    monkeypatch.setattr(panel, "run_picker", choose)
    panel.add_library()
    assert "acme" in offered
    assert "acme" in window.document.project.libraries


def test_a_process_is_chosen_from_a_list(window, monkeypatch):
    window.open_process()
    view = window.area.process_view
    seen = []

    def choose(picker):
        seen.extend(picker.visible_names())
        picker.choose("mems.soi")
        return True

    monkeypatch.setattr(view, "run_picker", choose)
    view.choose_process()
    assert {"mems.surface", "mems.soi", "mems.polysilicon"} <= set(seen)
    assert window.document.project.process_name == "mems.soi"


def test_the_import_dialog_previews_the_cell(window, tmp_path):
    from mems_sketch.core.imports import gds_bytes
    from mems_sketch.gui.import_dialog import ImportDialog

    window.document.nodes.add(RefShape(name="comb", component="mems.comb_drive"))
    geometry = window.document.results.geometry()
    path = tmp_path / "comb.gds"
    path.write_bytes(gds_bytes(geometry, {"device": (1, 0), "anchor": (2, 0)}, "COMB"))
    dialog = ImportDialog(window.document, path, window)
    assert dialog.preview.pixmap() is not None and not dialog.preview.pixmap().isNull()
    assert dialog.summary.text().startswith("COMB: ") and "layers" in dialog.summary.text()
