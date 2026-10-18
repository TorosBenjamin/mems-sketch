"""The comparison tab: two versions of a component on the canvas, marks on the
changed shapes (rings on the ones that only moved), stepping through them,
side by side, rolling a shape back."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox

from mems_sketch.core.shapes import Align, RectShape
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.comparison_view import MOVED, UNCOMMITTED, ComparisonView
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
    doc = w.document
    doc.nodes.add(RectShape(name="beam", layer="device", x0=0, y0=0, x1=40, y1=4))
    doc.nodes.add(
        RectShape(
            name="post",
            layer="device",
            x0=0,
            y0=0,
            x1=4,
            y1=10,
            align=Align(point="bottom", to="beam.top"),
        )
    )
    doc.save(git_repo)
    commit(doc, "Beam and post")
    return w


def resize(window, path, **fields):
    node = window.document.node(path)
    window.document.nodes.replace(path, type(node).model_validate({**node.model_dump(), **fields}))


def marks(view):
    return [(m.name, m.kind) for m in view.marks]


def test_an_edited_shape_is_marked_and_one_it_moved_is_ringed(window):
    resize(window, ((0, 0),), y1=8)  # the post sits on the beam: it moves up
    view = window.open_comparison()
    assert isinstance(view, ComparisonView) and window.area.comparison_view is view
    assert view.component == "top" and view.title() == "top: changes"
    assert marks(view) == [("beam", "changed"), ("post", MOVED)]
    assert view.marks[0].lines == ["y1 4 → 8"]
    assert view.marks[1].lines == ["moved y +4 µm", "because of another change"]
    drawn = view.canvas.marks
    assert [d.filled for d in drawn] == [True, False] and drawn[0].label == "1"
    assert view.counter.text() == "2 changes"


def test_added_and_removed_shapes(window, qtbot):
    window.document.nodes.add(RectShape(name="pad", layer="device", x0=0, y0=20, x1=10, y1=30))
    view = window.open_comparison()
    assert marks(view) == [("pad", "added")]
    window.document.undo()
    window.document.nodes.remove([((0, 1),)])  # the post
    qtbot.waitUntil(lambda: marks(view) == [("post", "removed")])  # it follows edits


def test_stepping_zooms_to_each_change_and_shows_its_card(window):
    resize(window, ((0, 0),), y1=8)
    view = window.open_comparison()
    view.step(1)
    assert view.current == 0 and view.counter.text() == "1 of 2"
    assert view.card.isVisible() and "beam" in view.card.title.text()
    assert view.canvas.marks[0].current
    view.step(1)
    assert view.current == 1 and "post" in view.card.title.text()
    view.step(1)
    assert view.current == 0  # round


def test_side_by_side_shows_both_versions_zoomed_together(window):
    resize(window, ((0, 0),), y1=8)
    view = window.open_comparison()
    view.side_button.setChecked(True)
    assert view.old_canvas.isVisible()
    view.step(1)
    assert view.old_canvas.view_state() == pytest.approx(view.canvas.view_state(), abs=0.5)
    view.overlay_button.setChecked(True)
    assert not view.old_canvas.isVisible()


def test_rolling_back_a_shape_from_its_card(window, qtbot):
    resize(window, ((0, 0),), y1=8)
    window.document.nodes.add(RectShape(name="pad", layer="device", x0=0, y0=20, x1=10, y1=30))
    view = window.open_comparison()
    view.go_to(0)  # the beam
    view.roll_back()
    assert window.document.node(((0, 0),)).y1 == 4
    qtbot.waitUntil(lambda: marks(view) == [("pad", "added")])  # it followed the edit
    view.go_to(0)
    view.roll_back()  # an added shape goes
    assert [s.name for s in window.document.shapes] == ["beam", "post"]
    window.document.undo()
    assert [s.name for s in window.document.shapes] == ["beam", "post", "pad"]


def test_a_commit_is_compared_from_the_git_panel(window, commit):
    resize(window, ((0, 0),), x1=50)
    commit(window.document, "Longer beam")
    window.tool_windows.open("git")
    item = window.git.versions.topLevelItem(0)
    window.git.versions.itemDoubleClicked.emit(item, 0)
    view = window.area.comparison_view
    assert view.key[0] == "commit"
    assert marks(view) == [("beam", "changed"), ("post", MOVED)]  # aligned to its top
    assert "Longer beam" in view.what.text()
    view.go_to(0)
    assert "Roll back" not in view.card.links.text()  # only uncommitted changes roll back


def test_a_change_double_clicked_in_the_commit_panel_opens_at_it(window):
    resize(window, ((0, 0),), y1=8)
    window.tool_windows.open("commit")
    tree = window.commit_panel.tree
    beam = tree.topLevelItem(0).child(0).child(0)
    assert beam.text(0) == "beam"
    tree.itemDoubleClicked.emit(beam, 0)
    view = window.area.comparison_view
    assert view.key == UNCOMMITTED and view.current == 0


def test_the_tab_is_one_and_is_not_kept_with_the_layout(window):
    resize(window, ((0, 0),), y1=8)
    first = window.open_comparison()
    assert window.open_comparison() is first
    assert "process" not in str(window.area.layout_state()["panes"])
    pane = window.area.pane_of(first)
    window.area.close_tab(pane, pane.indexOf(first))
    assert window.area.comparison_view is None
