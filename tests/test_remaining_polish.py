"""The rest of the usability and design review issues: the rules table, the route
and align tools, menus, layout, shortcuts and labels (#297, #318, #327, #328,
#329)."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QAbstractItemView, QApplication, QMessageBox

from mems_sketch.gui.app import MainWindow


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    qtbot.waitExposed(w)
    return w


def test_a_double_click_on_a_rule_not_selected_edits_its_cell(window, qtbot):
    """#297: the first click used to rebuild the table and lose the double-click."""
    from mems_sketch.gui.rules_panel import VALUES

    window.open_process()
    table = window.area.process_view.rules.rules
    qtbot.waitUntil(table.isVisible)
    item = table.item(1, VALUES)
    where = table.visualItemRect(item).center()
    # As a platform sends it: press, release, then the double-click (no second press),
    # which Qt matches against the cell of the first press.
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=where)
    double = QMouseEvent(
        QEvent.Type.MouseButtonDblClick,
        QPointF(where),
        table.viewport().mapToGlobal(QPointF(where)),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(table.viewport(), double)
    editing = QAbstractItemView.State.EditingState
    qtbot.waitUntil(lambda: table.state() == editing, timeout=2000)
    assert table.currentItem().text() == item.text()  # that cell, not another


def test_the_route_tool_names_the_port_a_click_takes(window):
    """#297: the status bar named `comb1.fixed` (a point) where a click took a port."""
    from mems_sketch import RefShape

    window.document.nodes.add(RefShape(name="comb1", component="mems.comb_drive"))
    window.set_tool("route")
    tool = window.tool
    for name, port in tool.ports().items():
        assert tool.hover_label(port.x, port.y) == tool._port_at(port.x, port.y) == name


def test_the_align_tool_switches_to_another_shape_without_esc(window):
    """#297: with one shape being aligned, a click on another aligns that one."""
    from mems_sketch import RectShape

    nodes = window.document.nodes
    a = nodes.add(RectShape(name="a", layer="device", x0=0, y0=0, x1=10, y1=10))
    b = nodes.add(RectShape(name="b", layer="device", x0=100, y0=0, x1=140, y1=40))
    window.tree.select_paths([a])
    window.set_tool("align")
    assert window.tool.path == a and window.tool.step == "own"
    window.canvas.fit()
    window.tool.press(112, 27, Qt.KeyboardModifier.NoModifier)  # inside b, off its points
    assert window.tool.path == b and window.tool.step == "own"
    assert window.selection == [b]


def test_the_layer_list_offers_only_levels_that_exist(window, tmp_path):
    """#297: level-1 was offered on the bottom level, where there is none."""
    from PySide6.QtWidgets import QComboBox

    from mems_sketch import RectShape

    window.document.create(tmp_path / "p", "p", process="mems.surface")  # device, metal
    path = window.document.nodes.add(RectShape(name="r", layer="level", x0=0, y0=0, x1=10, y1=10))
    window.tree.select_paths([path])
    combos = window.properties.findChildren(QComboBox)
    layers = next(c for c in combos if c.findData("device") >= 0)
    offered = [layers.itemData(i) for i in range(layers.count())]
    assert "level+1" in offered and "level-1" not in offered


def texts(menu) -> list[str]:
    return [a.text().partition("\t")[0] for a in menu.actions() if not a.isSeparator()]


def test_each_command_has_one_place_in_the_menus(window):
    """#328: make and unpack in Operations only; checking in its own menu."""
    actions = window.actions_
    taking_apart = {"Make component from selection…", "Unpack component", "Unpack operation"}
    assert not taking_apart & set(texts(actions.edit))
    assert taking_apart <= set(texts(actions.operations_menu))
    assert "Cancel" not in texts(actions.edit)
    assert texts(actions.check) == ["Recompile and check", "Process and rules"]
    assert not {"Recompile and check", "Process"} & set(texts(actions.view))


def test_esc_still_cancels_with_no_menu_entry(window):
    cancel = next(a for a in window.actions() if a.text() == "Cancel")
    assert cancel.shortcut().toString() == "Esc"


def test_a_placements_unset_level_is_named(window, tmp_path):
    """#328: the level list's first entry was blank."""
    from PySide6.QtWidgets import QComboBox

    from mems_sketch import RefShape
    from mems_sketch.gui.properties import DEFAULT_LEVEL

    window.document.create(tmp_path / "p", "p", process="mems.surface")
    path = window.document.nodes.add(RefShape(name="c", component="mems.comb_drive"))
    window.tree.select_paths([path])
    combos = window.properties.findChildren(QComboBox)
    level = next(c for c in combos if c.findText("level+1") >= 0 and c.isEditable())
    assert level.itemText(0) == DEFAULT_LEVEL and level.currentText() == DEFAULT_LEVEL
    window.properties.apply()
    assert window.document.node(path).level is None  # still unset


def test_an_empty_messages_panel_is_only_as_tall_as_its_line(window, qtbot):
    """#318: "No rule violations." took 140 px."""
    from mems_sketch.gui.toolwindows import DEFAULT_SIZES

    bottom = window.tool_windows.center.sizes()[1]
    assert 0 < bottom < DEFAULT_SIZES["center"][1]
    assert bottom >= window.messages.content_height()


def test_a_bottom_panel_dragged_keeps_its_height(window):
    tool_windows = window.tool_windows
    total = sum(tool_windows.center.sizes())
    tool_windows.center.setSizes([total - 300, 300])
    tool_windows._dragged("center")  # as the splitter reports a drag
    window.messages.show_messages([], [], component="top")
    assert tool_windows.center.sizes()[1] == 300
    assert tool_windows.state()["bottom_dragged"]


def test_properties_fade_at_the_bottom_while_there_is_more_below(window, qtbot):
    """#318: a panel that scrolls showed no cue."""
    from mems_sketch import ArrayModifier, RectShape

    path = window.document.nodes.add(
        RectShape(name="r", layer="device", x0=0, y0=0, x1=9, y1=9, modifiers=[ArrayModifier()])
    )
    window.resize(1000, 500)  # short: the form scrolls
    window.tree.select_paths([path])
    editor = window.properties
    bar = editor.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0)
    assert editor.fade.isVisible()
    bar.setValue(bar.maximum())
    assert not editor.fade.isVisible()


def test_the_git_panel_opens_taller_than_the_messages(window):
    """#318: the Git panel's details were cut off at the bottom panel's 140 px."""
    tool_windows = window.tool_windows
    messages = tool_windows.center.sizes()[1]
    tool_windows.open("git")
    assert tool_windows.center.sizes()[1] >= 240 > messages
    tool_windows.open("messages")
    assert tool_windows.center.sizes()[1] == messages


def test_ctrl_k_commits_as_in_jetbrains(window, qtbot):
    """#327: Ctrl+K opens the Commit panel with its message to type."""
    actions = window.actions_
    assert actions.commit.shortcut().toString() == "Ctrl+K"
    assert actions.make.shortcut().toString() == "Ctrl+Alt+M"
    assert actions.unpack.shortcut().toString() == "Ctrl+Alt+N"
    window.activateWindow()
    actions.commit.trigger()
    assert window.tool_windows.is_open("commit")
    qtbot.waitUntil(lambda: window.commit_panel.message.hasFocus())


def test_the_keymap_lists_every_command(window):
    """#327: Shift Shift, a command's every key and those without one."""
    window.show_settings("Keymap")
    table = window._preferences.keymap
    rows = {table.item(r, 0).text(): table.item(r, 1).text() for r in range(table.rowCount())}
    assert rows["Search everywhere"] == "Shift Shift"
    assert {"Ctrl+Y", "Ctrl+Shift+Z"} <= set(rows["Redo"].split(", "))  # (the platform's too)
    assert rows["Rectangle"] == "B"
    assert rows["Remove alignment"] == ""  # no key, but listed
    assert rows["Cancel"] == "Esc"


def test_parameters_are_labelled_by_their_descriptions(window):
    """#329: Properties showed `fingers` where the guide says "Moving fingers"."""
    from PySide6.QtWidgets import QLabel

    from mems_sketch import RefShape
    from mems_sketch.gui.properties import parameter_label

    path = window.document.nodes.add(RefShape(name="comb", component="mems.comb_drive"))
    window.tree.select_paths([path])
    labels = {lab.text() for lab in window.properties.findChildren(QLabel)}
    assert "Moving fingers" in labels and "fingers" not in labels
    assert parameter_label("anchored", "1: on an anchor; 0: none") == "anchored"  # no phrase
    assert parameter_label("overlap", "Initial finger overlap: the travel… (µm)") == (
        "Initial finger overlap"
    )


def test_an_empty_component_says_how_to_start(window):
    """#329: "Select a shape" where there were none."""
    from PySide6.QtWidgets import QLabel

    window.tree.select_paths([])
    texts = " ".join(lab.text() for lab in window.properties.findChildren(QLabel))
    assert "to start" in texts and "Select a shape" not in texts
