"""The building blocks the panels share: one header, one kind of table, one empty state."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView, QLabel, QTableWidgetItem, QWidget

from mems_sketch.gui import theme, widgets
from mems_sketch.gui.app import MainWindow


@pytest.fixture
def window(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    # Closing after an edit asks about the unsaved changes: answer it.
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    return w


def test_a_table_fits_its_panel_with_one_column_taking_the_room(qtbot):
    table = widgets.make_table(("Name", "Default", "Min"), stretch=1)
    qtbot.addWidget(table)
    header = table.horizontalHeader()
    assert header.sectionResizeMode(1) == QHeaderView.ResizeMode.Stretch
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.ResizeToContents
    assert table.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    # Titles sit over their columns' text, not in the middle of a wide column.
    assert header.defaultAlignment() & Qt.AlignmentFlag.AlignLeft
    assert table.verticalHeader().defaultSectionSize() == theme.metric("row_height")
    assert table.textElideMode() == Qt.TextElideMode.ElideRight


def test_a_long_context_is_cut_off_and_shown_whole_as_its_tooltip(qtbot):
    label = widgets.header_context()
    qtbot.addWidget(label)
    label.resize(60, 20)
    label.setText("a_component_with_a_very_long_name · read-only, trial values work")
    assert label.text().startswith("a_component")  # the whole text
    assert QLabel.text(label).endswith("…")  # what is drawn
    assert label.toolTip() == label.text()
    label.setText("top")
    assert QLabel.text(label) == "top" and label.toolTip() == ""


def test_an_empty_state_has_a_message_and_an_optional_action(qtbot):
    pressed = []
    state = widgets.EmptyState("Nothing here")
    qtbot.addWidget(state)
    state.show()
    assert state.text() == "Nothing here" and not state.button.isVisible()
    state.set_action("Do it", lambda: pressed.append(True))
    assert state.button.isVisible() and state.button.text() == "Do it"
    state.button.click()
    assert pressed == [True]
    state.set_action(None)
    assert not state.button.isVisible()


@pytest.mark.parametrize(
    ("name", "context"), [("parameters", "top"), ("points", "top"), ("history", "")]
)
def test_tool_windows_show_what_they_show_and_their_actions_in_the_header(window, name, context):
    window.tool_windows.open(name)
    panel = window.tool_windows._windows[name].widget
    header = window.tool_windows._windows[name].header_buttons
    assert panel.header_context.parentWidget() is header  # next to the title
    assert panel.header_context.text().startswith(context)
    assert all(b.parentWidget() is header for b in panel.header_buttons)
    # No second header row inside the panel.
    assert not [
        label
        for label in panel.findChildren(QLabel)
        if label.objectName() == "muted" and label.parentWidget() is panel
    ]


def test_add_and_remove_actions_are_neutral_where_history_keeps_its_colours():
    from mems_sketch.gui import icons

    green = theme.get("light").icons["green"]
    assert green not in icons.svg("plus") and green in icons.svg("add")


def test_the_layers_panel_gives_layer_names_the_room(window):
    window.tool_windows.open("layers")
    table = window.layers.layers
    assert table.horizontalHeader().sectionResizeMode(0) == QHeaderView.ResizeMode.Stretch


def test_the_parameters_panel_does_not_scroll_sideways(window):
    window.tool_windows.open("parameters")
    table = window.parameters.table
    assert table.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    stretch = table.horizontalHeader().sectionResizeMode(1)
    assert stretch == QHeaderView.ResizeMode.Stretch  # Default gives way, cut off with "…"


def test_a_themes_metrics_size_the_panels(window, tmp_path):
    import json

    folder = tmp_path / "themes"
    folder.mkdir()
    (folder / "roomy.json").write_text(
        json.dumps({"name": "Roomy", "parent": "light", "metrics": {"row_height": 34}})
    )
    theme.load()
    window.settings.set("appearance/ui_theme", "roomy")
    assert theme.metric("row_height") == 34
    table = widgets.make_table(("A",))
    assert table.verticalHeader().defaultSectionSize() == 34
    window.settings.set("appearance/ui_theme", "light")


def test_a_bad_metric_leaves_the_theme_out(tmp_path):
    import json

    folder = tmp_path / "themes"
    folder.mkdir()
    (folder / "tiny.json").write_text(
        json.dumps({"name": "Tiny", "parent": "light", "metrics": {"header_height": 2}})
    )
    assert "tiny" not in theme.load()
    assert "metrics: header_height" in theme.problems()[0]


def test_the_history_panel_explains_why_there_is_no_history_in_its_middle(qtbot, tmp_path):
    from mems_sketch.editing import EditSession
    from mems_sketch.gui.history_panel import HistoryPanel

    panel = HistoryPanel(EditSession())  # never saved: no history
    qtbot.addWidget(panel)
    panel.show()
    panel.reload()
    assert panel.note.isVisible() and isinstance(panel.note, QWidget)
    assert panel.note.label.alignment() & Qt.AlignmentFlag.AlignHCenter


def test_parts_of_a_panel_are_divided_by_a_hairline_not_a_bar(window):
    from PySide6.QtWidgets import QSplitter

    window.open_process()
    window.tool_windows.open("points")
    window.tool_windows.open("history")
    splitters = window.findChildren(QSplitter)
    inner = [s for s in splitters if s.objectName() != "islands"]
    assert {s.objectName() for s in inner} == {"panel-split"}
    assert len(inner) >= 3  # Points, History, the editor area
    line = theme.color("border_strong").name()
    for split in (s for s in inner if s.count() > 1):  # a 1 px line in the handle's middle
        image = split.handle(1).grab().toImage()
        x, y = image.width() // 2, image.height() // 2
        assert image.pixelColor(x, y).name() == line
        if split.orientation() == Qt.Orientation.Vertical:  # parts above each other
            assert image.pixelColor(x, y - 1).name() != line  # 1 px thick
        else:  # side by side
            assert image.pixelColor(x - 1, y).name() != line
    assert sum(s.objectName() == "islands" for s in splitters) == 3  # the gaps between islands


def test_the_process_page_is_cards_at_a_readable_width(window):
    window.open_process()
    view = window.area.process_view
    cards = [c for c in view.findChildren(QWidget) if c.objectName() == "card"]
    assert [c.title.text() for c in cards] == ["Constants", "Layers", "Rules"]
    page = view.page.widget().findChild(QWidget, "page")
    assert page.maximumWidth() <= 1100
    # The process picker is in the page's header, above the cards.
    assert view.rules.process.parentWidget() is not view.rules
    # Each card's actions are in its own header, by its title.
    assert all(b.parentWidget() is cards[2] for b in view.rules.actions)
    # Tables are as tall as their rows: the page scrolls, not the tables.
    table = view.layers.layers
    assert table.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert table.height() >= table.rowCount() * table.verticalHeader().defaultSectionSize()


def test_a_rule_with_one_value_shows_and_takes_just_the_value(window):
    window.open_process()
    rules = window.area.process_view.rules.rules
    row = [rules.item(r, 0).text() for r in range(rules.rowCount())].index("device_min_width")
    assert rules.item(row, 3).text() == "2"
    rules.item(row, 3).setText("1.5")
    assert window.document.project.process.rules["device_min_width"].values == {"value": 1.5}
    assert rules.item(row, 4).icon().cacheKey() != 0  # the severity has its icon


def test_a_rules_note_and_origin_fold_out_under_it(window):
    window.open_process()
    panel = window.area.process_view.rules
    rules = panel.rules
    assert panel.details is None and rules.columnCount() == 5  # the row: what is scanned for
    row = [rules.item(r, 0).text() if rules.item(r, 0) else None for r in range(rules.rowCount())]
    rules.selectRow(row.index("device_anchored"))
    details = panel.details
    assert details is not None and rules.cellWidget(row.index("device_anchored") + 1, 0) is details
    # The project's own process: nothing to give a reason for (a library's is in test_gui_layout).
    assert details.source.text() == "project" and details.reason.isHidden()
    details.note.setText("anchors come later")
    details.note.editingFinished.emit()
    assert window.document.project.process.rules["device_anchored"].message == "anchors come later"
    names = [rules.item(r, 0).text() if rules.item(r, 0) else None for r in range(rules.rowCount())]
    assert names.count(None) == 1  # one rule open at a time, the table as tall as its rows
    assert rules.height() >= sum(rules.rowHeight(r) for r in range(rules.rowCount()))


def test_columns_fitting_their_contents_do_not_cut_off_their_longest_text(window):
    window.open_process()
    rules = window.area.process_view.rules.rules
    names = [rules.item(r, 0).text() for r in range(rules.rowCount())]
    widest = max(rules.fontMetrics().horizontalAdvance(n) for n in names)
    assert rules.columnWidth(0) >= widest + 13 + 2 * widgets.CELL_PADDING  # check box too


# -- editing long values, visibility, cell editors ------------------------------


def _field_in_panel(qtbot, text):
    from PySide6.QtWidgets import QFormLayout, QScrollArea

    from mems_sketch.gui.value_edit import ValueEdit

    area = QScrollArea()
    area.setWidgetResizable(True)
    body = QWidget()
    form = QFormLayout(body)
    field = ValueEdit(text, scope={"width": 10, "rim_width": 3})
    form.addRow(QLabel("A rather long label"), field)
    area.setWidget(body)
    area.resize(320, 200)
    qtbot.addWidget(area)
    area.show()
    qtbot.waitExposed(area)
    return area, field


def test_editing_a_value_too_long_for_its_field_opens_a_wider_editor(qtbot):
    from PySide6.QtCore import Qt as _Qt

    area, field = _field_in_panel(qtbot, "abs(width - rim_width) + abs(width + rim_width) / 2")
    applied = []
    field.returnPressed.connect(lambda: applied.append(field.text()))
    field.setFocus()
    qtbot.waitUntil(lambda: field.expanded is not None)
    expanded = field.expanded
    assert expanded.width() > field.width()  # as wide as the panel, not the field
    assert expanded.width() >= area.viewport().width() - 8
    assert expanded.height() >= field.height()
    expanded.setPlainText("abs(width - rim_width)")
    qtbot.keyClick(expanded, _Qt.Key.Key_Return)
    assert applied == ["abs(width - rim_width)"] and field.expanded is None
    assert not expanded.isVisible()


def test_esc_leaves_the_field_as_it_was_and_short_values_stay_in_the_field(qtbot):
    from PySide6.QtCore import Qt as _Qt

    long = "abs(width - rim_width) + abs(width + rim_width) / 2"
    _area, field = _field_in_panel(qtbot, long)
    field.setFocus()
    qtbot.waitUntil(lambda: field.expanded is not None)
    field.expanded.setPlainText("width")
    qtbot.keyClick(field.expanded, _Qt.Key.Key_Escape)
    assert field.text() == long and field.expanded is None
    _area, short = _field_in_panel(qtbot, "12")
    short.setFocus()
    qtbot.wait(20)
    assert short.expanded is None  # fits: no second editor
    qtbot.keyClick(short, _Qt.Key.Key_Return, _Qt.KeyboardModifier.ShiftModifier)
    assert short.expanded is not None  # Shift+Enter opens it anyway
    short.expanded.finish()


def test_the_wider_editor_completes_names(qtbot):
    _area, field = _field_in_panel(qtbot, "abs(width - rim_width) + abs(width + rim_width) / 2")
    field.setFocus()
    qtbot.waitUntil(lambda: field.expanded is not None)
    expanded = field.expanded
    expanded.setPlainText("abs(wid")
    cursor = expanded.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    expanded.setTextCursor(cursor)
    expanded._complete("width")
    assert expanded.toPlainText() == "abs(width"
    expanded.finish(keep=False)


def test_shapes_and_layers_show_an_eye_for_visibility(window):
    assert window.tree.property("visibility") is True
    assert window.layers.layers.property("visibility") is True
    window.open_process()  # a rule's check box is on/off, not visibility: it stays a box
    assert not window.area.process_view.rules.rules.property("visibility")
    style = QApplication_style_sheet()
    assert '[visibility="true"]::indicator:checked' in style and "eye" in style


def QApplication_style_sheet():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance().styleSheet()


def test_editing_a_cell_is_a_flat_field_filling_the_cell(window, qtbot):
    from PySide6.QtWidgets import QLineEdit, QTableWidgetItem

    table = widgets.make_table(("Name", "Value"), stretch=1)  # the window's style sheet
    qtbot.addWidget(table)
    table.setRowCount(1)
    table.setItem(0, 1, QTableWidgetItem("abs(width - rim_width)"))
    table.resize(300, 120)
    table.show()
    qtbot.waitExposed(table)
    table.editItem(table.item(0, 1))
    editor = next(e for e in table.viewport().findChildren(QLineEdit) if e.isVisible())
    qtbot.wait(20)
    image = editor.grab().toImage()
    background = theme.color("editor").name()
    # No border: the corners and edges are the field's own background.
    for x, y in ((0, 0), (image.width() - 1, 0), (0, image.height() - 1)):
        assert image.pixelColor(x, y).name() == background


def test_fields_in_a_rules_fold_out_keep_their_border(window, qtbot):
    window.open_process()
    panel = window.area.process_view.rules
    details = panel.open_details("device_anchored")
    assert details is not None
    qtbot.wait(20)
    image = details.note.grab().toImage()
    edge = image.pixelColor(image.width() // 2, 0).name()
    assert edge != theme.color("editor").name()  # a border, unlike a cell being edited


def test_selecting_a_row_for_the_user_ignores_keys_held_down(qtbot):
    from PySide6.QtCore import Qt as _Qt

    # Shift+Enter opening the wider editor (a popup) leaves Shift held in QTest,
    # as a user may hold it while the panel selects a row for them.
    _area, field = _field_in_panel(qtbot, "12")
    field.setFocus()
    qtbot.wait(20)
    qtbot.keyClick(field, _Qt.Key.Key_Return, _Qt.KeyboardModifier.ShiftModifier)
    field.expanded.finish()
    table = widgets.make_table(("Name",))
    qtbot.addWidget(table)
    table.setRowCount(4)
    for row in range(4):
        table.setItem(row, 0, QTableWidgetItem(f"p{row}"))
    table.show()
    qtbot.waitExposed(table)
    table.selectRow(2)
    selected = [r.row() for r in table.selectionModel().selectedRows()]
    assert selected != [2]  # selectRow follows the held Shift
    widgets.select_row(table, 3)
    assert [r.row() for r in table.selectionModel().selectedRows()] == [3]
    assert table.currentRow() == 3


def test_clicking_out_of_the_wider_editor_applies_it(qtbot):
    _area, field = _field_in_panel(qtbot, "abs(width - rim_width) + abs(width + rim_width) / 2")
    applied = []
    field.returnPressed.connect(lambda: applied.append(field.text()))
    field.setFocus()
    qtbot.waitUntil(lambda: field.expanded is not None)
    field.expanded.setPlainText("width * 2")
    field.expanded.hide()  # what clicking elsewhere does to the popup
    assert applied == ["width * 2"] and field.expanded is None
