"""The window's layout: tool windows, toolbar, status bar, canvas overlays and menus."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QToolBar, QToolButton

from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.find_action import menu_actions


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    return w


# -- tool windows ------------------------------------------------------------


def test_a_first_start_opens_the_usual_tool_windows(window):
    windows = window.tool_windows
    opened = [name for name in windows.names() if windows.is_open(name)]
    assert opened == ["components", "shapes", "messages", "properties"]


def test_stripe_buttons_open_and_close_tool_windows(window):
    windows = window.tool_windows
    windows.button("parameters").click()
    assert windows.is_open("parameters") and not windows.is_open("properties")  # one per anchor
    assert windows.button("parameters").isChecked()
    assert not windows.button("properties").isChecked()
    windows.button("parameters").click()
    assert not windows.is_open("parameters")
    assert not window.parameters.isVisible()


def test_the_left_side_splits_between_its_two_anchors(window):
    windows = window.tool_windows
    windows.open("layers")
    assert windows.is_open("components") and windows.is_open("layers")
    assert not windows.is_open("shapes")
    assert window.components.isVisible() and window.layers.isVisible()
    windows.close("components")
    windows.close("layers")
    assert not windows.left_side.isVisible()


def test_open_tool_windows_are_remembered(window, qtbot):
    window.tool_windows.open("points")
    window.tool_windows.close("messages")
    window.close()
    again = MainWindow()
    qtbot.addWidget(again)
    assert again.tool_windows.is_open("points")
    assert not again.tool_windows.is_open("messages")


def test_a_damaged_layout_setting_falls_back_to_the_defaults(qtbot):
    from mems_sketch.gui.settings import Settings

    Settings().set_value("layout/tool_windows", "{not json")
    w = MainWindow()
    qtbot.addWidget(w)
    assert w.tool_windows.is_open("components") and w.tool_windows.is_open("properties")


def test_the_problem_count_opens_messages(window):
    window.tool_windows.close("messages")
    window.problems_button.click()
    assert window.tool_windows.is_open("messages")


# -- toolbar, menus and status bar -----------------------------------------------


def test_one_toolbar_row_and_no_menu_bar(window):
    toolbars = [
        b
        for b in window.findChildren(QToolBar)
        if b.isVisible() and b.orientation() == Qt.Orientation.Horizontal
    ]
    assert len(toolbars) == 1
    assert window.menuBar().actions() == []
    texts = [b.text() for b in toolbars[0].findChildren(QToolButton)]
    assert {"Add", "Place", "Operations"} <= set(texts)
    assert window.project_label.text() == window.document.project.name


def test_the_main_menu_holds_every_menu(window):
    titles = [a.text() for a in window.actions_.root.actions()]
    assert titles == ["&File", "&Edit", "&Tools", "&Insert", "&Operations", "&View", "&Help"]


def test_every_menu_shortcut_is_registered_on_the_window(window):
    registered = set(window.actions())
    listed = {id(a): a for _, a in menu_actions(window.actions_.root)}.values()  # once each
    with_keys = [a for a in listed if not a.shortcut().isEmpty()]
    assert len(with_keys) > 20
    assert all(a in registered for a in with_keys)
    keys = [a.shortcut().toString() for a in with_keys]
    assert len(keys) == len(set(keys))  # no shortcut is ambiguous


def test_a_shortcut_works_without_the_menu_bar(window, qtbot):
    window.canvas.setFocus()
    qtbot.keyClick(window.canvas, Qt.Key.Key_D)  # the Measure tool
    assert window.tool.name == "measure"


def test_add_starts_the_drawing_tool_and_adds_an_arc(window):
    add = {a.text(): a for a in window.actions_.add.actions()}
    add["Rectangle"].trigger()
    assert window.tool.name == "rect"
    add["Arc / ring"].trigger()
    assert [s.kind for s in window.document.shapes] == ["arc"]


def test_booleans_are_under_combine(window):
    operations = window.actions_.operations_menu
    combine = next(a.menu() for a in operations.actions() if a.text() == "Combine")
    assert [a.text() for a in combine.actions()] == ["Union", "Subtract", "Intersect", "XOR"]
    assert window.make_action in operations.actions()


def test_the_status_bar_shows_the_tool_options_and_snapping(window):
    window.set_tool("path")
    assert window.tool_status.isVisible() and window.width_box.isVisible()
    assert window.tool_name.text().strip() == "Path"
    window.set_tool("select")
    assert not window.width_box.isVisible() and not window.layer_box.isVisible()
