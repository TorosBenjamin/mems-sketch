"""The Shapes panel as an outliner (#256): drag shapes to reorder them and into
or out of an operation's operands, operand rows that say what they do, and
Enter to open a placed component."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QMessageBox

from mems_sketch.core.shapes import RectShape
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.panels import OPERAND_ROLE, PATH_ROLE

Position = QAbstractItemView.DropIndicatorPosition


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    for i, name in enumerate("abc"):
        w.document.nodes.add(
            RectShape(name=name, layer="device", x0=i * 20, y0=0, x1=i * 20 + 10, y1=10)
        )
    w.document.nodes.wrap([((0, 0),), ((0, 1),)], "subtract")
    return w


def rows(tree):
    """Every row of the tree, depth first."""
    found, pending = [], [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    while pending:
        item = pending.pop(0)
        found.append(item)
        pending[0:0] = [item.child(i) for i in range(item.childCount())]
    return found


def row(tree, text):
    return next(item for item in rows(tree) if item.text(0) == text)


def test_the_operands_say_what_they_do(window):
    tree = window.tree
    labels = [item.text(0) for item in rows(tree) if item.data(0, OPERAND_ROLE) is not None]
    assert labels == ["Keep", "Cut away"]


def test_where_a_drop_puts_the_shapes(window):
    tree = window.tree
    cut_away, c, a = row(tree, "Cut away"), row(tree, "c"), row(tree, "a")
    assert tree.drop_target(cut_away, Position.OnItem) == (((0, 0),), 1, 1)  # its end
    assert tree.drop_target(cut_away, Position.AboveItem) == (((0, 0),), 1, 0)
    assert tree.drop_target(c, Position.AboveItem) == (None, 0, 1)
    assert tree.drop_target(a, Position.BelowItem) == (((0, 0),), 0, 1)
    assert tree.drop_target(None, Position.OnViewport) == (None, 0, 2)


def test_a_drop_moves_the_shapes_and_keeps_them_selected(window, qtbot):
    tree = window.tree
    window.tree.select_paths([((0, 1),)])  # c
    tree.moved.emit(tree.selected_paths(), ((0, 0),), 1, 1)  # onto "Cut away"
    assert [s.name for s in window.document.shapes[0].b] == ["b", "c"]
    assert window.selection == [((0, 0), (1, 1))]
    assert row(tree, "c").parent().text(0) == "Cut away"
    window.undo()
    assert [s.name for s in window.document.shapes] == ["subtract1", "c"]


def test_rows_inside_a_placed_component_take_no_drops(window):
    window.document.components.new("pad")
    window.document.nodes.add(RectShape(name="p", layer="device", x0=0, y0=0, x1=5, y1=5))
    window.open_component("top")
    window.document.nodes.add_component("pad", x=100)
    placed = row(window.tree, next(s.name for s in window.document.shapes if s.kind == "ref"))
    placed.setExpanded(True)
    inside = placed.child(0)
    assert window.tree.drop_target(inside, Position.OnItem) is None


def test_enter_opens_a_placed_component(window, qtbot):
    window.document.components.new("pad")
    window.document.nodes.add(RectShape(name="p", layer="device", x0=0, y0=0, x1=5, y1=5))
    window.open_component("top")
    path = window.document.nodes.add_component("pad", x=100)
    window.tree.select_paths([path])
    item = next(i for i in rows(window.tree) if i.data(0, PATH_ROLE) == path)
    window.tree.setCurrentItem(item)
    window.tree.setFocus()
    qtbot.keyClick(window.tree, Qt.Key.Key_Return)
    assert window.document.active == "pad"


def test_enter_opens_a_component_in_the_components_panel(window, qtbot):
    window.document.components.new("pad")
    window.open_component("top")
    tree = window.components.tree
    item = next(i for i in rows(tree) if i.text(0) == "pad")
    tree.setCurrentItem(item)
    tree.setFocus()
    qtbot.keyClick(tree, Qt.Key.Key_Return)
    assert window.document.active == "pad"
