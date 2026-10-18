"""Menus: shortcuts lighter than their labels, check boxes beside options, rounded
panels with a shadow, icons in the ☰ menu, a right-click menu for what was
clicked; and qualified names (mems.perforated_plate) with where they are from muted."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import QApplication, QLineEdit, QMenu, QMessageBox

from mems_sketch.core.shapes import RectShape
from mems_sketch.gui import theme
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.settings import TrimmedSpinBox
from mems_sketch.gui.style import SHADOW
from mems_sketch.gui.widgets import (
    HEADER_ROLE,
    QualifiedCombo,
    QualifiedDelegate,
    qualified_html,
    split_qualified,
)


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    w.document.nodes.add(RectShape(name="bar", layer="device", x0=0, y0=0, x1=100, y1=20))
    w.document.nodes.add(RectShape(name="pad", layer="device", x0=200, y0=0, x1=220, y1=20))
    w.canvas.zoom_to(w.canvas.content_rect())
    return w


def shown(menu: QMenu, qtbot):
    menu.popup(QPoint(100, 100))
    qtbot.waitUntil(menu.isVisible)
    image = menu.grab().toImage()
    menu.hide()
    return image


def ink(image, x0: int, x1: int) -> QColor:
    """The darkest opaque colour in columns x0..x1 of the image (its text)."""
    darkest, value = None, 999
    for x in range(x0, x1):
        for y in range(image.height()):
            color = image.pixelColor(x, y)
            if color.alpha() == 255 and color.lightness() < value:
                darkest, value = color, color.lightness()
    return darkest


def labels(menu: QMenu) -> list[str]:
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def test_a_shortcut_is_lighter_than_its_label(window, qtbot):
    menu = QMenu(window)
    action = menu.addAction("Cut")
    action.setShortcut(QKeySequence("Ctrl+X"))
    image = shown(menu, qtbot)
    half = image.width() // 2
    label, shortcut = ink(image, 0, half), ink(image, half, image.width())
    assert label.lightness() < shortcut.lightness() - 40


def test_an_option_that_is_off_shows_an_empty_check_box(window, qtbot):
    def column(checkable: bool) -> int:
        menu = QMenu(window)
        action = menu.addAction("Dark canvas")
        action.setCheckable(checkable)
        image = shown(menu, qtbot)
        left = SHADOW + 1 + 5 + 8  # the panel, its padding and the entry's margin
        border = QColor(theme.current().ui["input_border"])
        return sum(
            1
            for x in range(left, left + 16)
            for y in range(image.height())
            if abs(image.pixelColor(x, y).lightness() - border.lightness()) < 12
        )

    assert column(True) > column(False) + 10


def test_a_menu_is_a_rounded_panel_with_room_for_its_shadow(window, qtbot):
    menu = QMenu(window)
    menu.addAction("Cut")
    image = shown(menu, qtbot)
    assert menu.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert menu.contentsMargins().left() == SHADOW
    assert image.pixelColor(0, 0).alpha() == 0  # no square corner behind the panel
    middle = image.pixelColor(image.width() // 2, image.height() // 2)
    assert middle.alpha() == 255


def test_a_menu_at_the_cursor_starts_its_panel_there(window, qtbot):
    menu = QMenu(window)
    menu.addAction("Cut")
    menu.popup(QPoint(300, 300))
    qtbot.waitUntil(menu.isVisible)
    assert menu.pos() == QPoint(300 - SHADOW, 300 - SHADOW + 2)
    menu.hide()


def test_the_main_menu_has_an_icon_by_each_entry(window):
    entries = window.actions_.root.actions()
    assert len(entries) == 7 and all(not a.icon().isNull() for a in entries)


def test_right_click_on_empty_space_offers_adding_there(window):
    window.tree.select_paths([])
    menu = window.actions_.context_menu(0, 0)
    assert labels(menu) == ["Add", "Place component", "Paste", "Select all", "Fit"]


def test_right_click_on_a_selection_offers_what_can_be_done_to_it(window):
    window.select_all()
    several = labels(window.actions_.context_menu(0, 0))
    assert "Add" not in several and "Zoom to selection" in several and "Offset" in several
    assert "Rename…" not in several  # one shape at a time
    window.tree.select_paths([window.selection[0]])
    assert "Rename…" in labels(window.actions_.context_menu(0, 0))


def test_right_click_on_empty_space_clears_the_selection(window, monkeypatch):
    window.select_all()
    menus = []
    monkeypatch.setattr(window, "show_menu", lambda menu, at: menus.append(menu))
    window._context_menu(window.view, 50, 500, QPoint(0, 0))
    assert window.selection == [] and labels(menus[0])[0] == "Add"


def test_select_all_zoom_to_selection_and_rename(window, qtbot):
    window.select_all()
    assert len(window.selection) == 2
    window.tree.select_paths([window.selection[1]])
    before = window.canvas.pixels_per_um()
    window.zoom_to_selection()
    qtbot.waitUntil(lambda: window.canvas.pixels_per_um() > before * 1.3)
    window.rename_selection()
    name = window.properties.findChild(QLineEdit, "title-edit")
    assert name.hasFocus() and name.selectedText() == name.text()


def test_a_number_setting_shows_no_trailing_zeros():
    box = TrimmedSpinBox()
    box.setDecimals(3)
    box.setSuffix(" µm")
    box.setValue(2)
    assert box.text() == "2 µm"
    box.setValue(2.5)
    assert box.text() == "2.5 µm"


def test_a_qualified_name_mutes_where_it_is_from():
    assert split_qualified("mems.perforated_plate") == ("mems.", "perforated_plate")
    assert split_qualified("anchor") == ("", "anchor")
    assert split_qualified("Save as…") == ("", "Save as…")
    text = qualified_html("mems.perforated_plate")
    assert text.endswith("</span>perforated_plate") and "mems." in text


def test_a_list_of_points_is_grouped_under_each_shape(qtbot):
    combo = QualifiedCombo(short=True)
    qtbot.addWidget(combo)
    combo.add_grouped(["center", "bar.top", "bar.left", "pad.top"])
    texts = [combo.itemText(i) for i in range(combo.count())]
    assert texts == ["center", "bar", "bar.top", "bar.left", "pad", "pad.top"]
    assert combo.itemData(1, HEADER_ROLE) and not combo.model().item(1).isEnabled()
    assert isinstance(combo.itemDelegate(), QualifiedDelegate)
    combo.setCurrentText("pad.top")
    combo.show()
    combo.grab()  # the chosen one is drawn with its shape muted


def test_the_place_menu_and_the_layer_stack_mute_qualifiers(window):
    from mems_sketch.gui.style import QUALIFIED

    assert window.actions_.place.property(QUALIFIED)
    window.open_process()
    table = window.area.process_view.layers.layers
    assert isinstance(table.itemDelegateForColumn(3), QualifiedDelegate)


def test_every_command_still_has_its_shortcut_once(window):
    keys = [
        a.shortcut().toString()
        for a in window.actions_.all_actions()
        if isinstance(a, QAction) and not a.shortcut().isEmpty()
    ]
    assert len(keys) == len(set(keys))
    assert QApplication.instance() is not None
