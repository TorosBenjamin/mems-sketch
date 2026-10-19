"""Scroll bars float over the content (they take no room), follow it, and fade
out when nothing moves; status icons are line icons at the panels' icon size."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QListWidget, QMessageBox

from mems_sketch.gui import scrollbars, theme
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.scrollbars import bars_of


@pytest.fixture
def area(qtbot):
    theme.apply(QApplication.instance(), "light")
    listing = QListWidget()
    listing.addItems([f"row {n}" for n in range(200)])
    qtbot.addWidget(listing)
    listing.resize(200, 150)
    listing.show()
    qtbot.waitExposed(listing)
    return listing


def test_the_bar_floats_over_the_content_and_takes_no_room(area, qtbot):
    vertical, horizontal = bars_of(area)
    assert vertical.orientation() == Qt.Orientation.Vertical
    qtbot.waitUntil(vertical.needed)
    assert area.viewport().width() == area.width() - 2 * area.frameWidth()  # no strip
    vertical.wake()
    assert vertical.isVisible() and vertical.width() == scrollbars.THICKNESS
    assert vertical.geometry().right() == area.viewport().geometry().right()
    assert not horizontal.needed()  # nothing to scroll sideways


def test_the_bar_follows_the_content_both_ways(area):
    vertical, _ = bars_of(area)
    area.verticalScrollBar().setValue(40)
    assert vertical.value() == 40
    vertical.setValue(10)
    assert area.verticalScrollBar().value() == 10


def test_scrolling_shows_the_bar_and_it_fades_when_idle(area, qtbot):
    vertical, _ = bars_of(area)
    vertical.fade()
    assert not vertical.shown
    pos = QPointF(50, 50)
    wheel = QWheelEvent(
        pos,
        area.viewport().mapToGlobal(pos),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(area.viewport(), wheel)
    assert vertical.shown and vertical.effect.opacity() == 1.0
    assert area.verticalScrollBar().value() > 0  # the wheel still scrolls
    vertical.idle.setInterval(10)
    vertical.idle.start()
    qtbot.waitUntil(lambda: not vertical.shown, timeout=2000)
    assert vertical.effect.opacity() == 0.0
    # Faded out, it lets clicks through to the rows under it.
    assert vertical.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def test_a_bar_turned_off_stays_off(area):
    area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    vertical, _ = bars_of(area)
    vertical.wake()
    assert not vertical.needed() and not vertical.isVisible()


def test_status_icons_are_at_the_panels_icon_size(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    size = theme.metric("icon_size")
    assert w.messages.iconSize().width() == size


def test_an_older_theme_still_loads_and_the_warning_falls_back_to_yellow():
    import json

    from mems_sketch.gui import icons

    folder = theme.user_folder()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "old.json").write_text(
        json.dumps(
            {
                "name": "Old",
                "parent": "light",
                "icons": {"on_accent": "#ffffff", "on_yellow": "#222222", "yellow": "#ffcc00"},
            }
        )
    )
    themes = theme.load()
    assert "old" in themes and "amber" in themes["light"].icons
    themes["old"].icons.pop("amber")  # as a theme without a parent may leave it out
    icons.set_theme("old")
    try:
        assert "#ffcc00" in icons.svg("warning")
    finally:
        icons.set_theme("light")
