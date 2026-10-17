"""The toolbar is the window's title bar: minimize, maximize and close in it."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QMessageBox

from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.title_bar import edges_at


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    return w


def frameless(window) -> bool:
    return bool(window.windowFlags() & Qt.WindowType.FramelessWindowHint)


def test_the_system_title_bar_is_replaced_by_buttons_in_the_toolbar(window):
    assert frameless(window)
    buttons = window.title_bar.buttons
    assert buttons.isVisible()
    assert [b.toolTip() for b in (buttons.minimize, buttons.maximize, buttons.close)] == [
        "Minimize",
        "Maximize",
        "Close",
    ]


def test_maximize_toggles_and_close_closes(window, qtbot):
    buttons = window.title_bar.buttons
    buttons.maximize.click()
    qtbot.waitUntil(window.isMaximized)
    assert buttons.maximize.toolTip() == "Restore"
    buttons.maximize.click()
    qtbot.waitUntil(lambda: not window.isMaximized())
    buttons.close.click()
    assert not window.isVisible()


def test_the_setting_brings_the_system_title_bar_back(window, qtbot):
    window.settings.set("appearance/custom_title_bar", False)
    assert not frameless(window) and window.isVisible()
    assert not window.title_bar.buttons.isVisible()
    window.settings.set("appearance/custom_title_bar", True)
    assert frameless(window) and window.title_bar.buttons.isVisible()


def test_edges_resize_near_the_border():
    frame = QRect(100, 100, 400, 300)
    assert edges_at(frame, QPoint(102, 250)) == Qt.Edge.LeftEdge
    assert edges_at(frame, QPoint(498, 397)) == Qt.Edge.RightEdge | Qt.Edge.BottomEdge
    assert edges_at(frame, QPoint(300, 250)) == Qt.Edge(0)
    assert edges_at(frame, QPoint(50, 50)) == Qt.Edge(0)
