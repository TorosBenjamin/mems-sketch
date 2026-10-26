"""The quick-look viewer (#141): a component on a read-only canvas, its
parameters to try (nothing saved), and Open in editor."""

import shutil
import uuid
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def resonator(tmp_path) -> Path:
    return Path(shutil.copytree(ROOT / "examples" / "resonator", tmp_path / "resonator"))


def spring_file(folder: Path) -> Path:
    return folder / "components" / "suspension" / "suspension.msketch"


@pytest.fixture
def viewer(qtbot, resonator):
    from mems_sketch.gui.viewer import open_viewer

    view = open_viewer(spring_file(resonator))
    qtbot.addWidget(view)
    view.show()
    qtbot.waitExposed(view)
    return view


def test_a_components_file_opens_read_only(viewer):
    from mems_sketch.gui.panels import ComponentsPanel, ShapeTree
    from mems_sketch.gui.properties import PropertyEditor

    assert viewer.component == "suspension" and not viewer.error
    assert not viewer.geometry_shown.is_empty()
    assert list(viewer.fields) == ["turns"]
    for kind in (ComponentsPanel, ShapeTree, PropertyEditor):  # nothing that edits
        assert not viewer.findChildren(kind)


def test_trial_values_rebuild_but_do_not_change_the_file(viewer, resonator):
    before = spring_file(resonator).read_text()
    shown = viewer.geometry_shown
    field = viewer.fields["turns"]
    field.setText("6")
    field.returnPressed.emit()
    assert viewer.trials == {"turns": 6.0} and viewer.geometry_shown is not shown
    field.setText("0")  # below its minimum: the reason is shown, the picture kept
    field.returnPressed.emit()
    assert viewer.error and viewer.message.isVisibleTo(viewer)
    viewer.reset_values()
    assert viewer.trials == {} and not viewer.error
    assert spring_file(resonator).read_text() == before


def test_layers_can_be_hidden_and_rulers_put_down(viewer):
    item = viewer.layers.item(0)
    item.setCheckState(item.checkState().Unchecked)
    assert viewer.visible[item.text()] is False
    viewer.measure.setChecked(True)
    viewer._pressed(0.0, 0.0, None)
    viewer._pressed(30.0, 40.0, None)
    assert viewer.rulers == [(0.0, 0.0, 30.0, 40.0)]
    viewer._escape()
    assert viewer.rulers == []


def test_open_in_editor_asks_for_the_project_at_the_component(qtbot, resonator):
    from mems_sketch.gui.viewer import open_viewer

    viewer = open_viewer(spring_file(resonator))  # it closes itself: not the qtbot's
    asked = []
    viewer.open_in_editor.connect(lambda path, component: asked.append((path, component)))
    viewer.editor.click()
    assert asked == [(str(spring_file(resonator)), "suspension")]
    qtbot.waitUntil(lambda: not _alive(viewer))


def _alive(widget) -> bool:
    import shiboken6

    return shiboken6.isValid(widget)


def test_a_manifest_shows_the_top_component(qtbot, resonator):
    from mems_sketch.gui.viewer import open_viewer

    view = open_viewer(resonator / "resonator.msketch")
    qtbot.addWidget(view)
    assert view.component == "top" and {"plate", "pitch", "w_finger"} <= set(view.fields)


# -- where a double-clicked component file goes -------------------------------------


@pytest.fixture
def window(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from mems_sketch.gui.app import MainWindow

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    qtbot.waitExposed(w)
    return w


def test_a_component_file_is_viewed_unless_its_project_is_open(window, resonator):
    from mems_sketch.gui.associations import opens_in_viewer

    path = str(spring_file(resonator))
    assert opens_in_viewer(path, [])
    assert not opens_in_viewer(str(resonator / "resonator.msketch"), [])  # a project
    window.open_project(str(resonator))
    assert not opens_in_viewer(path, [window])  # its tab in the editor instead


def test_view_from_a_components_right_click_menu(window, qtbot, tmp_path):
    window.document.create(tmp_path / "chip", "chip", process="mems.surface")
    from PySide6.QtWidgets import QTreeWidgetItemIterator

    panel = window.components
    items = []
    found = QTreeWidgetItemIterator(panel.tree)
    while found.value():
        items.append(found.value())
        found += 1
    item = next(it for it in items if it.data(0, panel.NAME_ROLE) == "top")
    assert "View" in [a.text() for a in panel.menu_for(item).actions()]
    viewer = window.view_component("top")
    assert viewer.component == "top" and viewer.editor.isEnabled()
    viewer.editor.click()
    assert window.document.active == "top"
    qtbot.waitUntil(lambda: not _alive(viewer))


def test_a_view_request_is_handed_to_the_running_app(qtbot):
    from mems_sketch.gui import associations

    name = f"mems-sketch-test-{uuid.uuid4().hex}"
    opened, viewed = [], []
    server = associations.listen(opened.append, name, view_path=viewed.append)
    try:
        assert associations.forward("a/b.msketch", name, view=True)
        qtbot.waitUntil(lambda: bool(viewed), timeout=3000)
        assert viewed[0].endswith("a/b.msketch") and not opened
    finally:
        server.close()


def test_view_is_in_the_usage():
    from mems_sketch.gui.app import USAGE

    assert "--view FILE" in USAGE
