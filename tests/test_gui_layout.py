"""The window's layout: tool windows, toolbar, status bar, canvas overlays and menus."""

import shutil
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QContextMenuEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QToolBar, QToolButton

from mems_sketch.core.shapes import RectShape
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.find_action import menu_actions


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    return w


# -- tool windows ------------------------------------------------------------


def test_a_first_start_opens_the_usual_tool_windows(window):
    windows = window.tool_windows
    opened = [name for name in windows.names() if windows.is_open(name)]
    assert opened == ["components", "shapes", "messages", "properties"]


def test_stripe_buttons_open_and_close_tool_windows(window):
    windows = window.tool_windows
    windows.button("parameters").click()
    assert windows.is_open("parameters") and not windows.is_open("properties")  # one per anchor
    assert windows.button("parameters").isChecked()
    assert not windows.button("properties").isChecked()
    windows.button("parameters").click()
    assert not windows.is_open("parameters")
    assert not window.parameters.isVisible()


def test_the_left_side_splits_between_its_two_anchors(window):
    windows = window.tool_windows
    windows.open("layers")
    assert windows.is_open("components") and windows.is_open("layers")
    assert not windows.is_open("shapes")
    assert window.components.isVisible() and window.layers.isVisible()
    windows.close("components")
    windows.close("layers")
    assert not windows.left_side.isVisible()


def test_open_tool_windows_are_remembered(window, qtbot):
    window.tool_windows.open("points")
    window.tool_windows.close("messages")
    window.close()
    again = MainWindow()
    qtbot.addWidget(again)
    assert again.tool_windows.is_open("points")
    assert not again.tool_windows.is_open("messages")


def test_a_damaged_layout_setting_falls_back_to_the_defaults(qtbot):
    from mems_sketch.gui.settings import Settings

    Settings().set_value("layout/tool_windows", "{not json")
    w = MainWindow()
    qtbot.addWidget(w)
    assert w.tool_windows.is_open("components") and w.tool_windows.is_open("properties")


def test_the_problem_count_opens_messages(window):
    window.tool_windows.close("messages")
    window.problems_button.click()
    assert window.tool_windows.is_open("messages")


# -- toolbar, menus and status bar -----------------------------------------------


def test_one_toolbar_row_and_no_menu_bar(window):
    toolbars = [
        b
        for b in window.findChildren(QToolBar)
        if b.isVisible() and b.orientation() == Qt.Orientation.Horizontal
    ]
    assert len(toolbars) == 1
    assert window.menuBar().actions() == []
    texts = [b.text() for b in toolbars[0].findChildren(QToolButton)]
    assert {"Add ▾", "Place ▾", "Operations ▾"} <= set(texts)
    assert window.project_label.text() == window.document.project.name


def test_the_main_menu_holds_every_menu(window):
    titles = [a.text() for a in window.actions_.root.actions()]
    assert titles == ["&File", "&Edit", "&Tools", "&Insert", "&Operations", "&View", "&Help"]


def test_every_menu_shortcut_is_registered_on_the_window(window):
    registered = set(window.actions())
    listed = {id(a): a for _, a in menu_actions(window.actions_.root)}.values()  # once each
    with_keys = [a for a in listed if not a.shortcut().isEmpty()]
    assert len(with_keys) > 20
    assert all(a in registered for a in with_keys)
    keys = [a.shortcut().toString() for a in with_keys]
    assert len(keys) == len(set(keys))  # no shortcut is ambiguous


def test_a_shortcut_works_without_the_menu_bar(window, qtbot):
    window.canvas.setFocus()
    qtbot.keyClick(window.canvas, Qt.Key.Key_D)  # the Measure tool
    assert window.tool.name == "measure"


def test_add_starts_the_drawing_tool_and_adds_an_arc(window):
    add = {a.text(): a for a in window.actions_.add.actions()}
    add["Rectangle"].trigger()
    assert window.tool.name == "rect"
    add["Arc / ring"].trigger()
    assert [s.kind for s in window.document.shapes] == ["arc"]


def test_booleans_are_under_combine(window):
    operations = window.actions_.operations_menu
    combine = next(a.menu() for a in operations.actions() if a.text() == "Combine")
    assert [a.text() for a in combine.actions()] == ["Subtract", "Intersect", "XOR"]
    assert window.make_action in operations.actions()


def test_the_status_bar_shows_the_tool_options_and_snapping(window):
    window.set_tool("path")
    assert window.tool_status.isVisible() and window.width_box.isVisible()
    assert window.tool_name.text().strip() == "Path"
    window.set_tool("select")
    assert not window.width_box.isVisible() and not window.layer_box.isVisible()


# -- the canvas: modes, view mode and the right-click menu ------------------------


def right_click(canvas, pos: QPointF, drag: QPointF | None = None) -> None:
    """Press the right button at ``pos`` (viewport pixels), maybe drag, and release."""
    viewport = canvas.viewport()
    end = pos + (drag or QPointF(0, 0))
    right, none = Qt.MouseButton.RightButton, Qt.MouseButton.NoButton
    for kind, at, buttons in (
        (QEvent.Type.MouseButtonPress, pos, right),
        (QEvent.Type.MouseMove, end, right),
        (QEvent.Type.MouseButtonRelease, end, none),
    ):
        button = none if kind == QEvent.Type.MouseMove else right
        event = QMouseEvent(
            kind, at, viewport.mapToGlobal(at), button, buttons, Qt.KeyboardModifier.NoModifier
        )
        QApplication.sendEvent(viewport, event)


@pytest.fixture
def menus(window, monkeypatch):
    """The menus the window pops up: each as {text: action}, with its submenus' actions too."""
    opened = []

    def record(menu, at):
        entries = {}
        for action in menu.actions():
            entries[action.text()] = action
            if action.menu() is not None:
                entries.update({a.text(): a for a in action.menu().actions()})
        opened.append(entries)

    monkeypatch.setattr(window, "show_menu", record)
    return opened


def test_right_click_opens_the_menu_and_right_drag_pans(window, menus):
    canvas = window.canvas
    centre = QPointF(canvas.viewport().rect().center())
    right_click(canvas, centre)
    assert len(menus) == 1 and {"Add", "Place component", "Rectangle"} <= set(menus[0])
    before = canvas.mapToScene(centre.toPoint())
    right_click(canvas, centre, QPointF(40, 0))
    assert len(menus) == 1  # a drag pans instead
    assert canvas.mapToScene(centre.toPoint()) != before


def test_right_click_selects_the_shape_under_the_cursor_and_offers_what_applies(window, menus):
    window.document.nodes.add(RectShape(name="plate", layer="device", x0=0, y0=0, x1=100, y1=50))
    window.document.nodes.add(RectShape(name="post", layer="device", x0=200, y0=0, x1=210, y1=50))
    canvas = window.canvas
    canvas.zoom_to(QRectF(-50, -100, 300, 250))
    right_click(canvas, QPointF(canvas.mapFromScene(QPointF(50, 25))))
    assert window.selection == [((0, 0),)]
    entries = menus[0]
    assert entries["Offset"].isEnabled() and entries["Duplicate"].isEnabled()
    assert not entries["Subtract"].isEnabled()  # combining needs two shapes
    assert not entries["Unpack component"].isEnabled()  # not a component reference


def test_right_click_add_rectangle_starts_at_the_click(window, menus):
    window.settings.set("snapping/points", False)
    window.settings.set("snapping/grid", False)
    canvas = window.canvas
    at = QPointF(canvas.viewport().rect().center()) + QPointF(30, -20)
    right_click(canvas, at)
    expected = canvas.mapToScene(at.toPoint())
    menus[0]["Rectangle"].trigger()
    assert window.tool.name == "rect"
    (x, y) = window.tool.placed[0]
    assert x == pytest.approx(expected.x(), abs=1e-3) and y == pytest.approx(expected.y(), abs=1e-3)
    assert canvas.mapToScene(at.toPoint()) == expected  # the view did not jump


def test_the_mode_palette_switches_tools(window):
    buttons = {
        b.defaultAction().text(): b for b in window.canvas.mode_palette.findChildren(QToolButton)
    }
    assert list(buttons) == ["Select", "Hand", "Move", "Rotate", "Align", "Measure"]
    assert all(b.isVisible() for b in buttons.values())
    palette = window.canvas.mode_palette
    assert palette.height() >= 6 * 24  # sized to hold its buttons
    buttons["Measure"].click()
    assert window.tool.name == "measure" and buttons["Measure"].isChecked()


def test_the_caption_changes_the_tabs_view_mode(window):
    menu = window.canvas.mode_button.menu()
    etched = next(a for a in menu.actions() if a.text() == "As etched")
    etched.trigger()
    assert window.view.view_mode == "etched"
    assert window.canvas.mode_button.text() == "As etched ▾"


def test_the_menu_key_opens_the_menu_at_the_centre(window, menus):
    canvas = window.canvas
    centre = canvas.viewport().rect().center()
    event = QContextMenuEvent(
        QContextMenuEvent.Reason.Keyboard, centre, canvas.viewport().mapToGlobal(centre)
    )
    QApplication.sendEvent(canvas, event)
    assert len(menus) == 1


# -- the Process tab ------------------------------------------------------------------


EXAMPLES = Path(__file__).parent.parent / "examples"


@pytest.fixture
def project(tmp_path):
    for name in ("resonator", "libraries"):
        shutil.copytree(
            EXAMPLES / name, tmp_path / name, ignore=shutil.ignore_patterns(".mems-sketch")
        )
    return tmp_path / "resonator"


def process_item(window):
    tree = window.components.tree
    items = [tree.topLevelItem(0).child(i) for i in range(tree.topLevelItem(0).childCount())]
    return next(item for item in items if item.data(0, window.components.PROCESS_ROLE))


def headers(table):
    return [table.horizontalHeaderItem(i).text() for i in range(table.columnCount())]


def test_the_process_tab_opens_from_the_tree_and_the_menu_once(window):
    window.components.tree.itemDoubleClicked.emit(process_item(window), 0)
    view = window.area.process_view
    assert view is not None and window.area.panes[0].currentWidget() is view
    assert window.view.component == "top"  # the panels keep showing the component
    assert window.area.views() == [window.view]  # the Process tab is not a component view
    menu_entry = next(a for a in window.actions_.view.actions() if a.text() == "Process")
    menu_entry.trigger()
    assert window.area.process_view is view and window.area.panes[0].count() == 2
    window.area.close_view(view)
    assert window.area.process_view is None and window.area.panes[0].count() == 1


def test_the_process_tab_edits_constants_with_undo_and_is_remembered(window, project, qtbot):
    window.open_project(str(project))
    window.open_process()
    table = window.area.process_view.constants.constants
    row = next(r for r in range(table.rowCount()) if table.item(r, 0).text() == "min_gap")
    table.item(row, 1).setText("2.5")
    assert window.document.project.process.constants["min_gap"] == 2.5
    window.document.undo()
    assert window.document.project.process.constants["min_gap"] == 2
    assert float(table.item(row, 1).text()) == 2  # the tab shows the undone value
    window.save_editor_state()

    again = MainWindow()
    qtbot.addWidget(again)
    again.open_project(str(project))
    assert again.area.process_view is not None


def test_the_layers_window_shows_layers_and_the_process_tab_defines_them(window):
    assert headers(window.layers.layers) == ["Layer", "GDS"]
    window.open_process()
    definitions = window.area.process_view.layers.layers
    assert "Undercut µm" in headers(definitions)
    column = headers(definitions).index("Undercut µm")
    definitions.item(0, column).setText("0.5")
    first = next(iter(window.document.project.layers.values()))
    assert first.undercut == 0.5


def test_a_tool_window_header_carries_the_panels_own_buttons(window):
    buttons = window.components.header_buttons
    assert buttons and all(b.isVisible() for b in buttons)  # Components is open
    window.tool_windows.close("components")
    window.tool_windows.open("components")
    assert all(b.isVisible() for b in buttons)
    from mems_sketch.gui.theme import HEADER_HEIGHT

    header = buttons[0].parentWidget().parentWidget()
    assert header.height() == HEADER_HEIGHT  # lines up with the editor tabs


def test_the_process_item_has_its_own_right_click_entry(window):
    menu = window.components.menu_for(process_item(window))
    assert [a.text() for a in menu.actions()] == ["Open the process"]
    menu.actions()[0].trigger()
    assert window.area.process_view is not None
