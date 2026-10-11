"""The building blocks the panels share: one header, one kind of table, one empty state."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView, QLabel, QWidget

from mems_sketch.gui import theme, widgets
from mems_sketch.gui.app import MainWindow


@pytest.fixture
def window(qtbot):
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
