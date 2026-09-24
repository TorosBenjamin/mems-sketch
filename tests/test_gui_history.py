"""The History panel: commits, what each changed, and the changes on the canvas."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox

from mems_sketch.core.shapes import RectShape
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.history_panel import KEY_ROLE, UNCOMMITTED
from mems_sketch.storage import git

pytestmark = pytest.mark.skipif(not git.available(), reason="needs git")


@pytest.fixture
def window(qtbot, monkeypatch, git_repo, commit):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    w.document.nodes.add(RectShape(name="beam", layer="device", x0=0, y0=0, x1=40, y1=4))
    w.document.save(git_repo)
    commit(w.document, "Beam")
    return w


def resize(window, **fields):
    path = ((0, 0),)
    node = window.document.node(path)
    window.document.nodes.replace(path, type(node).model_validate({**node.model_dump(), **fields}))


def versions(panel):
    tree = panel.versions
    return [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]


def change_rows(panel):
    tree = panel.changes
    rows = []
    for i in range(tree.topLevelItemCount()):
        group = tree.topLevelItem(i)
        rows.append(group.text(0))
        rows += [f"  {group.child(j).text(0)}" for j in range(group.childCount())]
    return rows


def test_the_versions_and_their_changes(window, commit):
    resize(window, x1=50)
    commit(window.document, "Longer beam")
    window.tool_windows.open("history")
    panel = window.history
    assert versions(panel) == ["Uncommitted changes", "Longer beam", "Beam"]
    assert change_rows(panel) == ["No changes"]
    panel.versions.setCurrentItem(panel.versions.topLevelItem(1))
    assert change_rows(panel) == ["top", "  shape beam: x1 40 → 50"]


def test_edits_show_as_uncommitted_changes_on_the_canvas(window):
    window.tool_windows.open("history")
    resize(window, x1=30)
    panel = window.history
    assert panel.key == UNCOMMITTED
    assert change_rows(panel) == ["top", "  shape beam: x1 40 → 30"]
    assert len(window.canvas._change_items) == 1  # the removed end of the beam
    window.tool_windows.close("history")
    assert window.canvas._change_items == []


def test_clicking_a_change_selects_the_shape(window):
    window.document.nodes.add(RectShape(name="pad", layer="device", x0=0, y0=20, x1=10, y1=30))
    window.tool_windows.open("history")
    window.tree.select_paths([])
    group = window.history.changes.topLevelItem(0)
    window.history.changes.itemClicked.emit(group.child(0), 0)
    assert window.selection == [((0, 1),)]


def test_comparing_a_commit_with_the_design_now(window, commit):
    resize(window, x1=50)
    commit(window.document, "Longer beam")
    resize(window, y1=8)
    window.tool_windows.open("history")
    panel = window.history
    first = panel.versions.topLevelItem(2).data(0, KEY_ROLE)
    panel.choose(("since", first[1]))
    assert panel.title.text() == f"From {first[1][:7]} to now"
    assert change_rows(panel) == ["top", "  shape beam: x1 40 → 50; y1 4 → 8"]


def test_a_project_outside_git_says_how_to_get_history(qtbot, tmp_path):
    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    w.tool_windows.open("history")
    assert w.history.note.isVisible() and not w.history.split.isVisible()
    assert "git" in w.history.note.text()
