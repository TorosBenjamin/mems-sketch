"""The Commit panel (local changes by component, choosing and committing them) and
the Git panel (the commits, what each changed), and their changes on the canvas."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from mems_sketch.core.shapes import RectShape
from mems_sketch.gui import theme
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.commit_panel import ROW_ROLE
from mems_sketch.gui.git_panel import KEY_ROLE
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


def rows(tree):
    """Every row as indented "name | detail" text."""
    found = []

    def walk(item, depth):
        found.append("  " * depth + " | ".join(t for t in (item.text(0), item.text(1)) if t))
        for index in range(item.childCount()):
            walk(item.child(index), depth + 1)

    for index in range(tree.topLevelItemCount()):
        walk(tree.topLevelItem(index), 0)
    return found


def row(tree, name):
    stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    while stack:
        item = stack.pop()
        if item.text(0) == name:
            return item
        stack += [item.child(i) for i in range(item.childCount())]
    raise LookupError(name)


@pytest.fixture
def panel(window):
    window.tool_windows.open("commit")
    return window.commit_panel


# -- the Commit panel ------------------------------------------------------------


def test_the_commit_panel_sits_on_the_left_and_lists_changes_by_component(window, panel):
    assert window.tool_windows._windows["commit"].anchor == "left-top"
    assert panel.no_changes.isVisible() and not panel.tree.isVisible()
    resize(window, x1=50, y1=8)
    assert rows(panel.tree) == [
        "Changes  1",
        "  top | 1 change",
        "    beam | 2 changes",  # two fields: a line each
        "      x1 40 → 50",
        "      y1 4 → 8",
    ]
    resize(window, y1=4)
    assert rows(panel.tree)[2] == "    beam | x1 40 → 50"  # one field: on its row


def test_a_short_panel_keeps_room_for_the_changes(window, panel):
    """#259: the message box gives way to the list (it used to be about a row and a
    half high), down to two lines of its own."""
    resize(window, x1=50, y1=8)
    window.resize(1200, 600)
    rows_high = panel.tree.height() / panel.tree.fontMetrics().height()
    assert rows_high >= 4
    lines = panel.message.height() / panel.message.fontMetrics().lineSpacing()
    assert lines >= 2


def test_names_are_coloured_by_what_happened(window, panel):
    resize(window, x1=50)
    window.document.nodes.add(RectShape(name="pad", layer="device", x0=0, y0=20, x1=10, y1=30))
    tree = panel.tree
    assert row(tree, "beam").foreground(0).color() == theme.color("vcs_changed")
    assert row(tree, "pad").foreground(0).color() == theme.color("vcs_added")
    assert row(tree, "top").foreground(0).color() == theme.color("vcs_changed")


def test_other_files_and_untracked_ones(window, panel, git_repo):
    (git_repo / "notes.md").write_text("ideas")
    panel.reload()
    assert rows(panel.tree) == ["Unversioned files  1", "  notes.md | new file"]
    notes = row(panel.tree, "notes.md")
    assert notes.checkState(0) == Qt.CheckState.Unchecked  # not committed unless chosen
    assert notes.foreground(0).color() == theme.color("vcs_untracked")


def test_committing_the_chosen_components(window, panel, git_repo):
    window.document.components.new("frame")
    window.document.set_active("top")
    window.document.save()
    __import__("subprocess").run(["git", "add", "-A"], cwd=git_repo, check=True)
    __import__("subprocess").run(["git", "commit", "-qm", "Frame"], cwd=git_repo, check=True)
    panel.reload()
    resize(window, x1=50)
    window.document.set_active("frame")
    window.document.nodes.add(RectShape(name="pad", layer="device", x0=0, y0=20, x1=10, y1=30))
    window.document.set_active("top")
    assert panel.count.text() == "2 of 2 chosen"
    row(panel.tree, "frame").setCheckState(0, Qt.CheckState.Unchecked)
    assert panel.count.text() == "1 of 2 chosen"
    assert panel.message.placeholderText() == "Change beam"  # from the chosen changes
    panel.message.setPlainText("Longer beam")
    panel.commit_button.click()
    assert window.document.history.commits()[0].subject == "Longer beam"
    assert rows(panel.tree)[1] == "  frame | 1 change"  # still to commit
    assert row(panel.tree, "frame").checkState(0) == Qt.CheckState.Checked  # chosen again
    assert not window.document.dirty


def test_amending_starts_from_the_last_message(window, panel):
    resize(window, x1=50)
    panel.amend.setChecked(True)
    assert panel.message.toPlainText() == "Beam"
    panel.message.setPlainText("Longer beam")
    panel.commit_button.click()
    assert [c.subject for c in window.document.history.commits()] == ["Longer beam"]
    assert not panel.amend.isChecked()


def test_a_heading_checks_its_rows(window, panel):
    resize(window, x1=50)
    heading = panel.tree.topLevelItem(0)
    heading.setCheckState(0, Qt.CheckState.Unchecked)
    assert row(panel.tree, "top").checkState(0) == Qt.CheckState.Unchecked
    assert not panel.commit_button.isEnabled()
    row(panel.tree, "top").setCheckState(0, Qt.CheckState.Checked)
    assert heading.checkState(0) == Qt.CheckState.Checked


def test_rolling_back_a_component_from_the_panel(window, panel):
    resize(window, x1=50)
    panel.tree.setCurrentItem(row(panel.tree, "beam"))  # a change: its component
    panel.rollback_current()
    assert window.document.node(((0, 0),)).x1 == 40
    assert panel.no_changes.isVisible()
    window.document.undo()
    assert rows(panel.tree)[1] == "  top | 1 change"


def test_clicking_a_change_selects_the_shape(window, panel):
    window.document.nodes.add(RectShape(name="pad", layer="device", x0=0, y0=20, x1=10, y1=30))
    window.tree.select_paths([])
    panel.tree.itemClicked.emit(row(panel.tree, "pad"), 0)
    assert window.selection == [((0, 1),)]


def test_edits_show_on_the_canvas_while_the_panel_is_open(window, panel):
    resize(window, x1=30)
    assert len(window.canvas._change_items) == 1  # the removed end of the beam
    window.tool_windows.close("commit")
    assert window.canvas._change_items == []


def test_the_header_shows_the_branch(window, panel):
    assert panel.header_context.text() == window.document.history.branch


def test_a_project_outside_git_can_start_one(qtbot, tmp_path):
    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    w.tool_windows.open("commit")
    panel = w.commit_panel
    assert panel.note.isVisible() and not panel.body.isVisible()
    assert "git" in panel.note.text()
    assert not panel.init_button.isVisible()  # save it first
    w.document.save(tmp_path / "plain")
    assert panel.init_button.isVisible()
    panel.init_button.click()
    assert panel.body.isVisible() and not panel.init_button.isVisible()
    assert panel.message.placeholderText() == f"Start {w.document.project.name}"


def test_rows_stay_folded_when_the_list_follows_an_edit(window, panel):
    resize(window, x1=50, y1=8)
    row(panel.tree, "beam").setExpanded(False)
    resize(window, x1=60)
    assert not row(panel.tree, "beam").isExpanded()
    assert row(panel.tree, "top").isExpanded()


# -- the Git panel --------------------------------------------------------------------


def commits(panel):
    tree = panel.versions
    return [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]


def test_the_git_panel_lists_commits_and_what_each_changed(window, commit):
    resize(window, x1=50)
    commit(window.document, "Longer beam")
    window.tool_windows.open("git")
    panel = window.git
    assert window.tool_windows._windows["git"].anchor == "bottom"
    assert commits(panel) == ["Longer beam", "Beam"]
    assert panel.no_changes.isVisible()  # nothing chosen yet
    panel.versions.setCurrentItem(panel.versions.topLevelItem(0))
    assert rows(panel.changes) == ["top | 1 change", "  beam | x1 40 → 50"]
    assert panel.compared.text().startswith("Commit ")


def test_comparing_a_commit_with_the_design_now(window, commit):
    resize(window, x1=50)
    commit(window.document, "Longer beam")
    resize(window, y1=8)
    window.tool_windows.open("git")
    panel = window.git
    first = panel.versions.topLevelItem(1).data(0, KEY_ROLE)
    panel.choose(("since", first[1]))
    assert panel.compared.text() == f"From {first[1][:7]} to now"
    assert rows(panel.changes)[1:] == ["  beam | 2 changes", "    x1 40 → 50", "    y1 4 → 8"]


def test_restoring_from_the_git_panel_shows_in_the_commit_panel(window, commit, panel):
    resize(window, x1=50)
    commit(window.document, "Longer beam")
    window.tool_windows.open("git")
    first = window.git.versions.topLevelItem(1).data(0, KEY_ROLE)[1]
    window.git.restore(first)
    assert window.document.node(((0, 0),)).x1 == 40
    assert rows(panel.tree)[2] == "    beam | x1 50 → 40"


def test_committing_updates_the_git_panel(window, panel):
    window.tool_windows.open("git")
    resize(window, x1=50)
    panel.message.setPlainText("Longer beam")
    panel.commit_button.click()
    assert commits(window.git) == ["Longer beam", "Beam"]


def shown(panel):
    tree = panel.versions
    items = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    return [item.text(0) for item in items if not item.isHidden()]


def run_git(folder, *args):
    __import__("subprocess").run(["git", *args], cwd=folder, check=True, capture_output=True)


def test_the_git_panel_lists_the_branch_checked_out_another_or_all(window, commit, git_repo):
    resize(window, x1=50)
    commit(window.document, "Longer beam")
    current = git.branch(git_repo)
    run_git(git_repo, "checkout", "-q", "-b", "side")
    resize(window, y1=8)
    commit(window.document, "Wider beam")
    run_git(git_repo, "checkout", "-q", current)
    window.tool_windows.open("git")
    panel = window.git
    box = panel.branch_box
    assert [box.itemText(i) for i in range(box.count())] == [current, "side", "All branches"]
    assert commits(panel) == ["Longer beam", "Beam"]
    box.setCurrentIndex(2)
    box.activated.emit(2)
    assert commits(panel) == ["Wider beam", "Longer beam", "Beam"]
    labels = [
        ref.name for ref in panel._commits[panel.versions.topLevelItem(1).data(0, KEY_ROLE)[1]].refs
    ]
    assert labels == [current]
    box.setCurrentIndex(1)
    box.activated.emit(1)
    assert commits(panel) == ["Wider beam", "Longer beam", "Beam"]
    assert panel.branch == "side"
    panel.reload()  # the choice stays
    assert panel.branch_box.currentText() == "side"


def test_searching_and_the_author_filter_narrow_the_commits(window, commit):
    resize(window, x1=50)
    commit(window.document, "Longer beam")
    window.tool_windows.open("git")
    panel = window.git
    assert not panel.graph.flat
    panel.search.setText("longer")
    assert shown(panel) == ["Longer beam"]
    assert panel.graph.flat  # the lines would join the wrong commits
    sha = panel.versions.topLevelItem(1).data(0, KEY_ROLE)[1]
    panel.search.setText(sha[:7])
    assert shown(panel) == ["Beam"]
    panel.search.setText("nothing like it")
    assert panel.no_match.isVisible() and not panel.versions.isVisible()
    panel.search.clear()
    assert shown(panel) == ["Longer beam", "Beam"] and not panel.graph.flat
    authors = panel.author_box
    assert [authors.itemText(i) for i in range(authors.count())] == ["All authors", "Test"]
    authors.setCurrentIndex(1)
    authors.activated.emit(1)
    assert shown(panel) == ["Longer beam", "Beam"] and panel.graph.flat


def test_the_chosen_commit_shows_its_message_and_where_it_is(window, commit, git_repo):
    resize(window, x1=50)
    commit(window.document, "Notes\n\nWhy <it> matters.")
    run_git(git_repo, "tag", "v1")
    window.tool_windows.open("git")
    panel = window.git
    assert not panel.details.isVisible()
    panel.versions.setCurrentItem(panel.versions.topLevelItem(0))
    assert panel.details.isVisible()
    assert "Notes" in panel.subject.text()
    assert "Why &lt;it&gt; matters." in panel.message.text()
    sha = panel.key[1]
    assert sha[:7] in panel.meta.text() and "Test" in panel.meta.text()
    assert "v1" in panel.meta.text() and git.branch(git_repo) in panel.meta.text()
    panel.since_button.click()
    assert panel.key == ("since", sha)
    opened = []
    panel.compare_requested.connect(lambda *args: opened.append(args))
    panel.open_button.click()
    assert opened == [(("since", sha), None, None)]


def test_the_last_panel_shown_draws_on_the_canvas(window, commit, panel):
    window.document.nodes.add(RectShape(name="pad", layer="device", x0=0, y0=20, x1=10, y1=30))
    commit(window.document, "Pad")
    resize(window, x1=30)
    panel.refresh()
    assert len(window.canvas._change_items) == 1  # the beam's end, uncommitted
    window.tool_windows.open("git")
    window.git.versions.setCurrentItem(window.git.versions.topLevelItem(0))  # "Pad"
    assert len(window.canvas._change_items) == 1  # the pad, added by that commit
    assert ROW_ROLE  # (the Commit panel's rows are still there)


def test_the_git_panel_does_not_hold_the_bottom_panel_open(window, commit):
    # Its filters and buttons are cut off in a small panel, not making it (and the
    # editor above) keep a large minimum size.
    resize(window, x1=50)
    commit(window.document, "Longer beam")
    window.tool_windows.open("git")
    panel = window.git
    panel.versions.setCurrentItem(panel.versions.topLevelItem(0))
    assert panel.minimumSizeHint().width() <= 240
    assert panel.minimumSizeHint().height() <= 200
