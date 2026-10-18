"""Placing components: the Place popup (Shift+A), the component following the cursor
until a click puts it down, the Components tab of Search Everywhere (by source,
folding, a preview) and a component's description, written in Properties."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QSize, Qt
from PySide6.QtGui import QColor, QKeyEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from mems_sketch.core.shapes import RefShape
from mems_sketch.gui import component_picker
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.component_picker import (
    BUILT_IN,
    ENTRY_ROLE,
    HEADING_ROLE,
    THIS_PROJECT,
    thumbnail,
)
from mems_sketch.gui.search import RESULT_ROLE

NONE = Qt.KeyboardModifier.NoModifier
CTRL = Qt.KeyboardModifier.ControlModifier


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    w.document.components.new("widget")
    w.open_component(w.document.project.top or "top")
    return w


def key(widget, which, modifiers=NONE, text=""):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, which, modifiers, text))


def headings(items) -> list[str]:
    def data(item):
        return (
            item.data(0, HEADING_ROLE) if hasattr(item, "columnCount") else item.data(HEADING_ROLE)
        )

    return [h[0] for h in map(data, items) if h is not None]


def popup_items(popup):
    return [popup.list.item(i) for i in range(popup.list.count())]


def placed(window) -> list[str]:
    return [s.component for s in window.document.shapes if isinstance(s, RefShape)]


# -- the popup ----------------------------------------------------------------------


def test_the_popup_lists_recent_components_then_each_source(window):
    component_picker.remember("anchor")
    popup = window.place_popup(near=QPoint(100, 100))
    assert headings(popup_items(popup)) == ["Recent", THIS_PROJECT, BUILT_IN]
    names = [e.name for e in (i.data(ENTRY_ROLE) for i in popup_items(popup)) if e]
    assert names[0] == "anchor"  # recent first
    assert window.document.active not in names  # not inside itself
    assert "widget" in names


def test_typing_ranks_the_matches_in_one_list(window):
    popup = window.place_popup(near=QPoint(100, 100))
    popup.search.setText("serp")
    rows = popup.rows()
    assert [r.data(ENTRY_ROLE).name for r in rows] == ["serpentine_spring"]
    popup.search.setText("zzz")
    assert not popup.rows() and popup.nothing.isVisible()


def test_typing_in_the_list_searches(window):
    popup = window.place_popup(near=QPoint(100, 100))
    popup.list.setFocus()
    key(popup.list, Qt.Key.Key_A, text="a")
    assert popup.search.text() == "a"


def test_enter_puts_the_component_under_the_cursor_and_a_click_places_it(window):
    popup = window.place_popup(near=QPoint(100, 100))
    popup.search.setText("anchor")
    key(popup.search, Qt.Key.Key_Return)
    tool = window.tool
    assert tool.name == "place" and tool.component == "anchor"
    assert window.canvas.ghost is not None
    tool.move(41, 19, NONE, False)
    assert window.canvas.ghost.pos().x() == pytest.approx(40)  # snapped to the grid
    tool.press(41, 19, NONE)
    assert placed(window) == ["anchor"]
    ref = window.document.shapes[-1]
    assert (ref.x, ref.y) == pytest.approx((40, 20))
    assert window.tool.name == "select" and window.canvas.ghost is None
    assert component_picker.recent()[0] == "anchor"


def test_esc_lets_the_component_go(window):
    window.start_placing("anchor")
    window.escape()
    assert window.tool.name == "select"
    assert window.canvas.ghost is None and placed(window) == []


def test_from_a_right_click_it_lands_where_the_click_was(window):
    popup = window.place_popup(at=(100.0, -50.0), near=QPoint(100, 100))
    popup.search.setText("anchor")
    key(popup.search, Qt.Key.Key_Return)
    ref = window.document.shapes[-1]
    assert (ref.component, ref.x, ref.y) == ("anchor", 100, -50)
    assert window.tool.name == "select"


def test_shift_a_and_the_right_click_menu_offer_the_popup(window):
    action = window.actions_.place
    assert action.text() == "Place component…" and action.shortcut().toString() == "Shift+A"
    menu = window.actions_.context_menu(0, 0)
    assert "Place component…" in [a.text() for a in menu.actions()]


# -- Search Everywhere --------------------------------------------------------------


def components_tab(window):
    window.search("Components")
    return window._find_dialog


def tab_items(dialog):
    return [dialog.list.topLevelItem(i) for i in range(dialog.list.topLevelItemCount())]


def test_the_components_tab_lists_them_by_source_with_a_preview(window):
    dialog = components_tab(window)
    assert headings(tab_items(dialog)) == [THIS_PROJECT, BUILT_IN]
    assert dialog.preview.isVisible() and dialog.source.isVisible()
    dialog.search.setText("comb")
    current = dialog.list.currentItem().data(0, RESULT_ROLE)
    assert current.title == "comb_drive"
    assert "comb" in dialog.preview.description.text().lower()
    assert "fingers" in dialog.preview.details.text()  # its parameters


def test_a_source_folds_away_and_stays_folded(window):
    dialog = components_tab(window)
    built_in = next(
        i for i in tab_items(dialog) if (i.data(0, HEADING_ROLE) or ("",))[0] == BUILT_IN
    )
    dialog.list.itemClicked.emit(built_in, 0)
    rows = [i.data(0, RESULT_ROLE).title for i in tab_items(dialog) if i.data(0, RESULT_ROLE)]
    assert "anchor" not in rows
    dialog.close()
    again = components_tab(window)
    rows = [i.data(0, RESULT_ROLE).title for i in tab_items(again) if i.data(0, RESULT_ROLE)]
    assert "anchor" not in rows  # remembered
    again.search.setText("anch")  # searching shows what matches, folded or not
    rows = [i.data(0, RESULT_ROLE).title for i in tab_items(again) if i.data(0, RESULT_ROLE)]
    assert rows == ["anchor"]


def test_one_source_can_be_chosen(window):
    dialog = components_tab(window)
    dialog.source.setCurrentText(BUILT_IN)
    assert headings(tab_items(dialog)) == [BUILT_IN]


def test_enter_places_and_ctrl_enter_opens(window):
    dialog = components_tab(window)
    dialog.search.setText("anchor")
    key(dialog.search, Qt.Key.Key_Return)
    assert window.tool.name == "place" and window.tool.component == "anchor"
    window.escape()
    dialog = components_tab(window)
    dialog.search.setText("widget")
    key(dialog.search, Qt.Key.Key_Return, CTRL)
    assert window.document.active == "widget"


def test_the_all_tab_still_opens_a_component(window):
    window.search()
    dialog = window._find_dialog
    dialog.search.setText("widget")
    key(dialog.search, Qt.Key.Key_Return)
    assert window.document.active == "widget"


# -- a component's picture and description ------------------------------------------


def test_a_components_picture_shows_its_geometry(window):
    picture = thumbnail(window.document.results.geometry("anchor"), {}, QSize(120, 80))
    image = picture.toImage()
    assert not picture.isNull()
    centre = image.pixelColor(image.width() // 2, image.height() // 2)
    assert centre.alpha() > 0 or any(
        image.pixelColor(x, image.height() // 2).alpha() > 0 for x in range(image.width())
    )
    assert isinstance(centre, QColor)


def test_a_component_is_described_in_properties(window):
    window.tool_windows.open("properties")
    window.tree.select_paths([])
    edit = window.properties.description_edit
    edit.setText("Holds the  device   down")
    edit.editingFinished.emit()
    active = window.document.active
    assert window.document.project.components[active].description == "Holds the device down"
    window.undo()
    assert window.document.project.components[active].description == ""


def test_the_description_is_shown_where_it_is_placed_from(window):
    window.document.components.set_description("widget", "A test widget. More words.")
    found = {e.name: e for e in component_picker.entries(window.document)}
    assert found["widget"].description == "A test widget"  # its first sentence
