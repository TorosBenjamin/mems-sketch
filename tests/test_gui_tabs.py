import shutil
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox

from mems_sketch.core.shapes import RefShape
from mems_sketch.core.user_component import ComponentDef
from mems_sketch.gui.app import MainWindow

EXAMPLES = Path(__file__).parent.parent / "examples"


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    return w


@pytest.fixture
def example(window, tmp_path):
    shutil.copytree(
        EXAMPLES / "resonator",
        tmp_path / "resonator",
        ignore=shutil.ignore_patterns(".mems-sketch"),
    )
    shutil.copytree(
        EXAMPLES / "libraries",
        tmp_path / "libraries",
        ignore=shutil.ignore_patterns(".mems-sketch"),
    )
    window.open_project(str(tmp_path / "resonator" / "project.yaml"))
    return window


def tab_names(window, pane=0):
    tabs = window.area.panes[pane]
    return [tabs.tabText(i) for i in range(tabs.count())]


def test_components_open_in_tabs_with_their_own_selection(example):
    w = example
    assert tab_names(w) == ["top"]
    w.tree.select_paths([((0, 0),)])
    w.open_component("suspension")
    assert tab_names(w) == ["top", "suspension"]
    assert w.document.active == "suspension"
    assert w.selection == []
    assert w.tree.topLevelItemCount() == 2  # the suspension's shapes
    w.open_component("top")  # reuses the tab
    assert tab_names(w) == ["top", "suspension"]
    assert w.selection == [((0, 0),)]  # each tab keeps its selection


def test_double_click_on_a_placed_component_opens_it(example):
    w = example
    w._tree_double_clicked(w.tree.topLevelItem(0), 0)  # mass: std.perforated_plate
    assert w.document.active == "std.perforated_plate"
    assert tab_names(w)[-1] == "std.perforated_plate (read-only)"
    w.open_component("top")
    w._view_double_clicked(w.area.current, -118.5, 46)  # suspension_left's anchor
    assert w.document.active == "suspension"


def test_library_tabs_are_read_only_but_take_trial_values(example):
    w = example
    w.open_component("std.perforated_plate")
    assert w.document.read_only and "viewing" in w.windowTitle()
    w.add_primitive("rect")
    assert "read-only" in w.statusBar().currentMessage()
    area_before = w.document.geometry().layers["device"].area()
    w.document.set_trial("pitch", 40)
    assert w.document.geometry().layers["device"].area() != area_before  # other holes
    assert not w.document.dirty and not w.document.can_undo()
    w.parameters._clear_trials()
    assert w.document.geometry().layers["device"].area() == area_before


def test_trial_values_do_not_change_the_design(example):
    w = example
    w.open_component("suspension")
    w.document.set_trial("turns", 6)
    assert w.document.project.components["suspension"].parameter("turns").default == 3
    row = w.parameters._names.index("turns")
    assert w.parameters.table.item(row, w.parameters.TRIAL).text() == "6"
    with pytest.raises(ValueError, match="turns"):
        w.document.set_trial("turns", 0.5)  # must be an integer
    assert w.document.trials["suspension"] == {"turns": 6}


def test_undo_goes_back_to_the_tab_of_the_change(example):
    w = example
    w.open_component("suspension")
    w.document.set_parameter("turns", 5)
    w.open_component("top")
    w.add_primitive("rect")
    w.open_component("std.perforated_plate")
    w.document.undo()  # the rectangle, added in top
    assert w.document.active == "top" and w.area.current.component == "top"
    w.document.undo()  # the spring turns, changed in suspension
    assert w.area.current.component == "suspension"
    assert tab_names(w) == ["top", "suspension", "std.perforated_plate (read-only)"]
    w.document.redo()
    assert w.area.current.component == "suspension"
    w.document.redo()
    assert w.area.current.component == "top"


def test_undo_reopens_a_closed_tab(example):
    w = example
    w.open_component("suspension")
    w.document.set_parameter("turns", 4)
    w.close_tab()
    assert "suspension" not in [v.component for v in w.area.views()]
    w.document.undo()
    assert w.area.current.component == "suspension"


def test_split_view_shows_edits_in_both_panes(example):
    w = example
    w.open_component("suspension")
    w.split_view()
    assert len(w.area.panes) == 2
    w.open_component("top")  # opens in the right pane, which is now current
    top_view = w.area.current
    assert w.area.pane_of(top_view) is w.area.panes[1]
    left = w.area.find("suspension", w.area.panes[0])
    w.area.set_current(left)
    before = top_view.canvas.content_rect()
    w.document.set_parameter("turns", 12)  # edited on the left...
    assert top_view.canvas.content_rect().height() > before.height()  # ...seen on the right
    w.area.unsplit()
    assert len(w.area.panes) == 1


def test_rename_and_delete_follow_the_tabs(window):
    w = window
    w.document.edit("add", lambda p: p.components.__setitem__("cell", ComponentDef(name="cell")))
    w.open_component("cell")
    w.document.rename_component("cell", "unit")
    assert "unit*" in tab_names(w)  # * : changed since the project was saved
    w.open_component("top")
    w.document.delete_component("unit")
    assert tab_names(w) == ["top"]


def test_tabs_are_remembered_per_project(example, tmp_path):
    w = example
    w.open_component("suspension")
    w.split_view()
    w.open_component("std.perforated_plate")
    w.open_project(str(tmp_path / "resonator"))  # reopen: tabs come back
    assert tab_names(w, 0) == ["top", "suspension"]
    assert tab_names(w, 1) == ["suspension", "std.perforated_plate (read-only)"]
    assert w.document.active == "std.perforated_plate"


def test_new_reference_placed_in_a_tab_does_not_change_other_tabs_selection(example):
    w = example
    w.tree.select_paths([((0, 1),)])
    w.open_component("suspension")
    w._select_result(lambda: w.document.add_component("anchor"))
    w.open_component("top")
    assert w.selection == [((0, 1),)]
    assert isinstance(w.document.node(((0, 1),)), RefShape)
