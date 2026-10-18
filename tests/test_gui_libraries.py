"""The bundled libraries in the Components panel: in their categories (#176)."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox

from mems_sketch import ComponentDef
from mems_sketch.gui.app import MainWindow


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    return w


def group(window, title):
    tree = window.components.tree
    return next(
        tree.topLevelItem(i)
        for i in range(tree.topLevelItemCount())
        if tree.topLevelItem(i).text(0) == title
    )


def children(item) -> list[str]:
    return [item.child(i).text(0) for i in range(item.childCount())]


def test_a_library_shows_its_categories_in_their_order(window):
    mems = group(window, "mems")
    assert "Bundled library" in mems.toolTip(0)
    assert children(mems) == ["Anchors", "Springs", "Masses", "Electrodes"]
    springs = mems.child(1)
    assert children(springs) == ["serpentine_spring"]
    assert springs.data(0, window.components.CATEGORY_ROLE) == "mems/springs"
    assert springs.child(0).data(0, window.components.NAME_ROLE) == "mems.serpentine_spring"
    assert window.components.menu_for(springs).isEmpty()  # a category has no actions
    assert not any("Remove library" in a.text() for a in window.components.menu_for(mems).actions())


def test_a_collapsed_category_stays_collapsed(window):
    springs = group(window, "mems").child(1)
    springs.setExpanded(False)
    assert "Category: mems/springs" in window.components.collapsed
    window.components.refresh()
    assert not group(window, "mems").child(1).isExpanded()


def test_tags_are_shown_and_searched(window):
    from mems_sketch.gui.search import RESULT_ROLE

    window.document.project.components["spring"] = ComponentDef(name="spring", tags=["flexure"])
    window.components.refresh()
    window.search("Components")
    dialog = window._find_dialog
    dialog.search.setText("flexure")
    items = [dialog.list.topLevelItem(i) for i in range(dialog.list.topLevelItemCount())]
    titles = [i.data(0, RESULT_ROLE).title for i in items if i.data(0, RESULT_ROLE)]
    assert titles == ["spring"]
