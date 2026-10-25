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
    assert labels(menu) == ["Add", "Place component…", "Paste", "Select all", "Fit"]


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


def test_the_layer_stack_mutes_qualifiers(window):
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


def test_keys_in_another_panel_leave_the_shapes_alone(window, qtbot):
    """Delete, Ctrl+D and F2 act on the shapes in the editor and the Shapes panel,
    never from another panel that has the focus (#242)."""
    window.tree.select_paths([min(window.document.results.inspection())])  # bar
    assert len(window.selection) == 1
    window.components.setFocus()
    qtbot.waitUntil(window.components.hasFocus)
    for key in (Qt.Key.Key_Delete, Qt.Key.Key_F2):
        qtbot.keyClick(QApplication.focusWidget(), key)
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
    assert [s.name for s in window.document.shapes] == ["bar", "pad"]
    window.canvas.setFocus()
    qtbot.waitUntil(window.canvas.hasFocus)
    qtbot.keyClick(window.canvas, Qt.Key.Key_Delete)
    assert [s.name for s in window.document.shapes] == ["pad"]


def test_clicking_a_layer_draws_on_that_layer(window):
    """The Layers panel lists the stack top first; the row clicked is the layer
    drawn on, and the drawing layer chosen elsewhere is highlighted (#241)."""
    window.tool_windows.open("layers")
    table = window.layers.layers
    for row in range(table.rowCount()):
        name = table.item(row, 0).text()
        table.setCurrentCell(row, 0)
        assert window.draw_layer == name
    window.layer_box.setCurrentIndex(window.layer_box.findText("device"))
    assert window.draw_layer == "device"
    assert table.item(table.currentRow(), 0).text() == "device"


def test_commands_on_the_wrong_selection_say_why(window, monkeypatch):
    """#253: nothing selected, or too many: the command says what it needs."""
    errors = []  # a hint now, not an error (#326)
    monkeypatch.setattr(window, "ask_selection", errors.append)
    window.tree.select_paths([])
    for command in (
        window.duplicate,
        window.zoom_to_selection,
        window.rename_selection,
        window.unwrap,
        window.unpack,
        window.remove_alignment,
    ):
        command()
    assert len(errors) == 6 and all(e.startswith("Select ") for e in errors)


def test_duplicate_copies_every_selected_shape_in_one_step(window):
    window.select_all()
    window.duplicate()
    assert [s.name for s in window.document.shapes] == ["bar", "pad", "bar1", "pad1"]
    assert len(window.selection) == 2
    window.undo()
    assert [s.name for s in window.document.shapes] == ["bar", "pad"]


def test_removing_an_alignment_that_is_not_there_adds_no_step(window):
    window.tree.select_paths([min(window.document.results.inspection())])
    window.document._undo.clear()
    window.remove_alignment()
    assert not window.document.can_undo()


def test_the_edit_menu_greys_out_what_does_not_apply(window):
    actions = window.actions_
    window.tree.select_paths([])
    actions.edit.aboutToShow.emit()
    assert not actions.delete.isEnabled() and not actions.duplicate.isEnabled()
    actions.edit.aboutToHide.emit()
    assert actions.delete.isEnabled()  # its key says why, once the menu is closed
    window.tree.select_paths([min(window.document.results.inspection())])
    actions.edit.aboutToShow.emit()
    assert actions.delete.isEnabled() and actions.rename.isEnabled()
    assert not actions.unpack.isEnabled() and not actions.remove_alignment.isEnabled()
    actions.edit.aboutToHide.emit()


def test_deleting_the_top_component_asks_first(window, monkeypatch):
    """#260: it is the whole design; No keeps it."""
    asked = []

    def answer(*args, **kwargs):
        asked.append(args[2])
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", answer)
    window.components._delete("top")
    assert asked and "top component" in asked[0]
    assert window.document.project.top == "top"


def test_the_place_button_says_why_it_places_nothing(window):
    errors = []
    window.components.error.connect(errors.append)
    window.components.tree.clearSelection()
    window.components._place()
    window.components.select_component("top")
    window.components._place()
    assert errors == ["select a component to place", "'top' cannot be placed in itself"]


def test_undo_says_what_each_step_did(window):
    doc = window.document
    doc.nodes.set_enabled(((0, 0),), False)
    assert doc.undo_text() == "Hide bar"
    window.tree.select_paths([((0, 1),)])
    window.cut()
    assert doc.undo_text() == "Cut pad"
    doc.nodes.add(RectShape(name="c", layer="device", x0=0, y0=50, x1=5, y1=55))
    doc.nodes.remove([((0, 0),), ((0, 1),)])
    assert doc.undo_text() == "Delete 2 shapes"


def test_a_submenu_opens_level_with_its_entry(window, qtbot):
    """#229: File's first entry is level with File, however the window was put."""
    from PySide6.QtCore import QPoint

    from mems_sketch.gui.style import line_up_submenu

    root, file = window.actions_.root, window.actions_.file
    root.popup(window.mapToGlobal(QPoint(200, 100)))
    qtbot.waitUntil(root.isVisible)
    entry = root.actionGeometry(file.menuAction())
    qtbot.mouseMove(root, entry.center())
    qtbot.waitUntil(file.isVisible, timeout=2000)
    file.move(file.pos() + QPoint(0, 9))  # as a platform might put it

    def first_top():
        return file.mapToGlobal(file.actionGeometry(file.actions()[0]).topLeft()).y()

    line_up_submenu(file)
    assert first_top() == root.mapToGlobal(entry.topLeft()).y()
    root.hide()
