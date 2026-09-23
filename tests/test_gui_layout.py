"""The window's layout: tool windows, toolbar, status bar, canvas overlays and menus."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox

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
