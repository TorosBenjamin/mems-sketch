"""The dialogs look like the panels: a side column of pages, labelled fields with
their "?", lists that say when they are empty, and a keymap you can read."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QCheckBox, QLineEdit, QListWidget

from mems_sketch.export.base import options_of
from mems_sketch.export.layout_formats import GdsExporter
from mems_sketch.gui import theme, widgets
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.export_dialog import ExportOptionsDialog
from mems_sketch.gui.help import HelpButton
from mems_sketch.gui.new_project import NewProjectDialog
from mems_sketch.gui.search import Result, SearchDialog
from mems_sketch.gui.settings import LABEL_WIDTH, PreferencesDialog


@pytest.fixture
def window(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    return w


def test_an_empty_list_says_so_until_it_has_rows(qtbot):
    view = QListWidget()
    qtbot.addWidget(view)
    view.resize(300, 200)
    view.show()
    note = widgets.placeholder(view, "No libraries")
    assert note.isVisible() and note.text() == "No libraries"
    view.addItem("std")
    assert not note.isVisible()
    view.setRowHidden(0, True)  # hidden rows count as none, after a refresh
    note.refresh()
    assert note.isVisible()
    view.clear()
    assert note.isVisible()


def test_settings_pages_sit_in_a_side_column_without_a_line(window, qtbot):
    dialog = PreferencesDialog(window.settings, [])
    qtbot.addWidget(dialog)
    assert isinstance(dialog.side, widgets.SideList)
    assert dialog.pages.parentWidget() is dialog.side
    assert dialog.side.objectName() == "dialog-side"
    assert "border-right" not in theme.STYLE.split("#dialog-side")[1].split("}")[0]
    assert [dialog.pages.item(i).text() for i in range(dialog.pages.count())][-1] == "Keymap"


def test_every_settings_row_is_as_tall_as_the_others(window, qtbot):
    dialog = PreferencesDialog(window.settings, [])
    qtbot.addWidget(dialog)
    dialog.show()
    tall = theme.metric("row_height") + theme.metric("space_s")
    for row in dialog._rows.values():
        assert row[-1].minimumHeight() == tall
        if len(row) == 2:  # a label and its field
            assert row[0].width() == LABEL_WIDTH


def test_the_keymap_is_a_table_of_commands_keys_and_menus(window, qtbot):
    shortcuts = [
        ("Save", "Ctrl+S", "File"),
        ("Make component from selection", "Ctrl+K", "Edit"),
        ("Make component from selection", "Ctrl+K", "Canvas"),  # in two menus: once
        ("Rotate", "R", "Tools"),
    ]
    dialog = PreferencesDialog(window.settings, shortcuts)
    qtbot.addWidget(dialog)
    table = dialog.keymap
    assert [table.horizontalHeaderItem(c).text() for c in range(3)] == [
        "Command",
        "Shortcut",
        "Menu",
    ]
    assert table.rowCount() == 3
    assert (table.item(1, 0).text(), table.item(1, 1).text(), table.item(1, 2).text()) == (
        "Make component from selection",
        "Ctrl+K",
        "Edit",
    )
    dialog.search.setText("ctrl+s")  # a search finds keys too, and shows them
    assert [table.isRowHidden(r) for r in range(3)] == [False, True, True]
    assert dialog.pages.currentItem().text() == "Keymap"
    dialog.search.setText("nothing like this")
    assert "nothing like this" in dialog._keymap_empty.text()


def test_the_window_lists_shortcuts_with_their_menus(window, qtbot):
    window.show_settings("Keymap")
    dialog = window._preferences
    qtbot.addWidget(dialog)
    table = dialog.keymap
    rows = [tuple(table.item(r, c).text() for c in range(3)) for r in range(table.rowCount())]
    assert any(name == "Save" and menu.endswith("File") for name, _keys, menu in rows)
    assert len({row[:2] for row in rows}) == len(rows)  # no command twice
    assert not any(name.endswith(("…", "...")) for name, _k, _m in rows)


def test_new_project_libraries_say_when_there_are_none(window, qtbot, tmp_path):
    dialog = NewProjectDialog(str(tmp_path), parent=window)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.side.isVisible() and dialog.kinds.parentWidget() is dialog.side
    note = dialog.libraries.viewport().findChild(widgets._Placeholder)
    assert note.isVisible() and "bundled" in note.text()
    dialog.add_library(str(tmp_path))
    assert not note.isVisible()
    assert not dialog.remove.isEnabled()  # nothing selected yet
    dialog.libraries.setCurrentRow(0)
    dialog.remove.click()
    assert dialog.libraries.count() == 0 and note.isVisible()


def test_search_says_when_nothing_matches(qtbot):
    dialog = SearchDialog([Result("plate", "Parameters", lambda: None)])
    qtbot.addWidget(dialog)
    dialog.show()
    assert not dialog.nothing.isVisible()
    dialog.search.setText("qqq")
    assert dialog.nothing.isVisible() and "qqq" in dialog.nothing.text()


def test_export_options_explain_themselves_by_their_question_mark(qtbot):
    dialog = ExportOptionsDialog("GDS", options_of(GdsExporter), {})
    qtbot.addWidget(dialog)
    assert len(dialog.findChildren(HelpButton)) == len(options_of(GdsExporter))
    top_cell = dialog.editors["top_cell"]
    assert isinstance(top_cell, QLineEdit)
    assert top_cell.placeholderText() == "The component's name"
    boxes = [n for n, e in dialog.editors.items() if isinstance(e, QCheckBox)]
    assert boxes == ["keep_hierarchy"] and dialog.editors["keep_hierarchy"].isChecked()  # #74
