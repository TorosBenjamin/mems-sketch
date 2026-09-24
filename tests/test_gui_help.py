"""Explanations behind a "?" instead of paragraphs in the way."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QToolTip

from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.help import HelpButton, help_html
from mems_sketch.gui.settings import PreferencesDialog


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    return w


def test_help_text_wraps_and_marks_bold():
    html = help_html("Use *Trial* to try.\n\nA < b.")
    assert "<b>Trial</b>" in html and "&lt;" in html
    assert html.count("<p>") == 2 and "width=" in html


def test_every_tool_window_explains_itself(window):
    for name in window.tool_windows.names():
        window.tool_windows.open(name)
        header = window.tool_windows._windows[name].header_buttons
        assert header.findChildren(HelpButton), name


def test_hovering_shows_the_explanation_at_once(window, qtbot):
    window.tool_windows.open("properties")
    button = window.tool_windows._windows["properties"].header_buttons.findChild(HelpButton)
    center = button.rect().center().toPointF()
    QApplication.sendEvent(button, QEnterEvent(center, center, button.mapToGlobal(center)))
    assert QToolTip.isVisible() and "dragged" in QToolTip.text()


def test_alignment_is_explained_by_a_button_not_a_paragraph(window):
    window.add_primitive("rect")
    helps = window.properties.findChildren(HelpButton)
    assert any("lands on" in h.text_ for h in helps)


def test_settings_explain_themselves(window, qtbot):
    dialog = PreferencesDialog(window.settings, [])
    qtbot.addWidget(dialog)
    assert dialog.findChildren(HelpButton)
