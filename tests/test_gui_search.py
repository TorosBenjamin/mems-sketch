"""Search Everywhere: components, shapes, parameters and commands in one popup."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from mems_sketch.core.shapes import RectShape
from mems_sketch.gui.app import MainWindow


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    w.document.parameters.set("pitch", 13.0)
    w.document.nodes.add(RectShape(name="finger", layer="device", x0=0, y0=0, x1=2, y1=30))
    w.document.nodes.add(RectShape(name="plate", layer="device", x0=0, y0=0, x1=40, y1=20))
    return w


def shown(window) -> list[str]:
    return [item.text(0) for item in window._find_dialog._visible()]


def test_all_finds_each_kind_of_thing_with_headings(window):
    window.search()
    dialog = window._find_dialog
    dialog.search.setText("p")
    headings = [
        dialog.list.topLevelItem(i).text(0)
        for i in range(dialog.list.topLevelItemCount())
        if dialog.list.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) is None
    ]
    assert headings == ["Components", "Shapes", "Parameters", "Actions"]
    assert {"top", "plate", "pitch"} <= set(shown(window))


def test_names_starting_with_the_text_come_first(window):
    window.search("Shapes")
    window._find_dialog.search.setText("late")
    assert shown(window) == ["plate"]
    window._find_dialog.search.setText("f")
    assert shown(window)[0] == "finger"


def test_choosing_a_shape_selects_it(window):
    window.search("Shapes")
    dialog = window._find_dialog
    dialog.search.setText("plate")
    dialog._run(dialog.list.currentItem())
    assert window.selection == [((0, 1),)]


def test_choosing_a_component_opens_it(window):
    window.search("Components")
    dialog = window._find_dialog
    dialog.search.setText("comb")
    assert shown(window) == ["comb_drive"]
    dialog._run(dialog.list.currentItem())
    assert window.view.component == "comb_drive"


def test_choosing_a_parameter_shows_it_in_its_panel(window):
    window.search("Parameters")
    dialog = window._find_dialog
    dialog.search.setText("pitch")
    dialog._run(dialog.list.currentItem())
    assert window.tool_windows.is_open("parameters")
    rows = window.parameters.table.selectionModel().selectedRows()
    assert [window.parameters.table.item(r.row(), 0).text() for r in rows] == ["pitch"]


def test_tab_goes_to_the_next_group(window):
    window.search()
    dialog = window._find_dialog
    QTest.keyClick(dialog.search, Qt.Key.Key_Tab)
    assert dialog.scope == "Components"


def test_find_action_opens_on_actions(window):
    window.find_action()
    assert window._find_dialog.scope == "Actions"


def test_shift_twice_opens_search(window):
    handle = window.windowHandle()
    QTest.keyClick(handle, Qt.Key.Key_Shift)
    assert not hasattr(window, "_find_dialog")
    QTest.keyClick(handle, Qt.Key.Key_Shift)
    assert window._find_dialog.isVisible() and window._find_dialog.scope == "All"


def test_shift_then_a_letter_then_shift_does_not(window):
    handle = window.windowHandle()
    QTest.keyClick(handle, Qt.Key.Key_Shift)
    QTest.keyClick(handle, Qt.Key.Key_A, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClick(handle, Qt.Key.Key_Shift)
    assert not hasattr(window, "_find_dialog")
