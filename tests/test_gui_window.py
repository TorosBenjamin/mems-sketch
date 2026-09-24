import shutil
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QInputDialog, QLineEdit, QMessageBox

from mems_sketch.gui.app import MainWindow

EXAMPLES = Path(__file__).parent.parent / "examples"


@pytest.fixture
def window(qtbot, monkeypatch):
    # Closing a modified project asks to save; answer "discard" so teardown never blocks.
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    return w


def test_add_select_and_edit_through_the_property_editor(window):
    window.add_primitive("rect")
    assert window.selection == [((0, 0),)]
    assert window.tree.topLevelItemCount() == 1
    editor = window.properties
    assert editor.path == ((0, 0),)
    editor._editors["x1"] = lambda: "2 * 60"
    editor.apply()
    assert window.document.shapes[0].x1 == "2 * 60"


def test_boolean_from_selection_and_canvas_click(window):
    window.add_primitive("rect")
    window.add_primitive("circle")
    window.tree.select_paths([((0, 0),), ((0, 1),)])
    window.wrap("subtract")
    assert window.document.shapes[0].kind == "boolean"
    assert window.tree.topLevelItemCount() == 1
    window._canvas_clicked(90, 25, False)  # inside the rectangle, outside the circle
    assert window.selection == [((0, 0),)]
    window._canvas_clicked(-500, -500, False)
    assert window.selection == []


def test_errors_are_reported_not_raised(window):
    window.add_primitive("rect")
    editor = window.properties
    editor._editors["x1"] = lambda: "nonexistent + 1"
    editor.apply()
    assert "nonexistent" in window.statusBar().currentMessage()
    assert window.document.shapes[0].x1 == 100


def test_component_parameters_show_declared_defaults(window):
    window.add_component("comb_drive")
    fields = window.properties.findChildren(QLineEdit)
    assert any(f.placeholderText() == "10" for f in fields)  # default finger count
    window.document.parameters.set("n", 4.0)
    window.properties.show_node(((0, 0),))
    window.properties._editors["params"] = lambda: {"fingers": "n"}
    window.properties.apply()
    assert window.document.shapes[0].params == {"fingers": "n"}


def test_make_component_and_switch_components(window, monkeypatch):
    window.add_primitive("rect")
    window.add_primitive("circle")
    window.tree.select_paths([((0, 0),), ((0, 1),)])
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("cell", True))
    window.make_component()
    assert window.document.shapes[0].component == "cell"
    assert window.selection == [((0, 0),)]
    window.document.set_active("top/cell")  # private to the component it came from
    assert window.tree.topLevelItemCount() == 2
    assert window.selection == []  # selection does not leak across components
    assert "editing top/cell" in window.windowTitle()


def test_open_example_project_with_library(window, tmp_path):
    shutil.copytree(
        EXAMPLES / "resonator",
        tmp_path / "resonator",
        ignore=shutil.ignore_patterns(".mems-sketch"),
    )
    shutil.copytree(
        EXAMPLES / "libraries",
        tmp_path / "libraries",
        ignore=shutil.ignore_patterns(".mems-sketch"),
    )
    window.open_project(str(tmp_path / "resonator" / "project.yaml"))
    assert window.document.project.name == "resonator"
    labels = [
        window.components.tree.topLevelItem(i).text(0)
        for i in range(window.components.tree.topLevelItemCount())
    ]
    assert labels == ["resonator", "std", "Built-in"]
    assert window.messages.item(0).text() == "No rule violations."


def test_undo_restores_tree(window):
    window.add_primitive("rect")
    window.add_primitive("circle")
    window.document.undo()
    assert window.tree.topLevelItemCount() == 1


def test_layers_panel_is_shown_on_its_own_and_every_panel_can_be_reopened(window):
    windows = window.tool_windows
    windows.open("layers")
    assert window.layers.isVisible()  # in place of Shapes, not behind it
    assert window.layers.layers.rowCount() == len(window.document.project.layers)

    windows.close("layers")
    reopen = next(a for a in window.actions_.panels.actions() if a.text() == "Layers")
    reopen.trigger()
    assert window.layers.isVisible() and reopen.isChecked()


def test_align_tool_picks_two_points_on_the_canvas(window):
    from mems_sketch.core.shapes import RectShape

    window.add_primitive("rect")  # rect1: 100 x 50 at the origin
    window._select_result(
        lambda: window.document.nodes.add(
            RectShape(name="post", layer="device", x0=300, y0=300, x1=310, y1=320)
        )
    )
    assert window.selection == [((0, 1),)]
    window.start_align()
    assert window.align_step == "own"
    window._canvas_clicked(305, 300, False)  # post.bottom
    assert window.align_step == "target"
    window._canvas_clicked(50, 50, False)  # rect1.top
    assert window.align_step is None
    align = window.document.node(((0, 1),)).align
    assert (align.point, align.to) == ("bottom", "rect1.top")
    assert "bottom at rect1.top" in window.tree.topLevelItem(1).toolTip(1)


def test_align_tool_cancels_and_needs_a_selection(window):
    window.start_align()
    assert window.align_step is None
    window.add_primitive("rect")
    window.start_align()
    assert window.align_step == "own"
    window.cancel_align()
    assert window.align_step is None


def test_property_editor_edits_the_alignment(window):
    window.add_primitive("rect")
    window.add_primitive("circle")
    editor = window.properties
    assert editor.path == ((0, 1),)
    editor._editors["align"] = lambda: {"point": "left", "to": "rect1.right", "dx": 5, "dy": 0}
    editor.apply()
    assert window.document.node(((0, 1),)).align.to == "rect1.right"
    assert window.document.active_definition.points == []


def test_canvas_is_white_by_default_and_can_be_dark(window):
    assert window.canvas.backgroundBrush().color().name() == "#ffffff"
    window.set_canvas_theme("dark")
    assert window.canvas.backgroundBrush().color().name() == "#1e1f22"
    again = MainWindow()  # the choice is remembered
    assert again.canvas.backgroundBrush().color().name() == "#1e1f22"
    again.close()


def test_switching_off_a_shape_inside_an_operation_in_the_list(window, qtbot):
    """Issue #1: unticking a shape in the Shapes list rebuilt the list while Qt was
    still ticking the item, which crashed. The change is now applied just after."""
    from PySide6.QtCore import Qt

    from mems_sketch.gui.panels import PATH_ROLE

    window.add_primitive("rect")
    window.add_primitive("circle")
    window.tree.select_paths([((0, 0),), ((0, 1),)])
    window.wrap("subtract")
    window.tree.expandAll()
    items, pending = [], [window.tree.topLevelItem(0)]
    while pending:
        item = pending.pop()
        items.append(item)
        pending.extend(item.child(i) for i in range(item.childCount()))
    inner = next(i for i in items if i.data(0, PATH_ROLE) == ((0, 0), (1, 0)))
    inner.setCheckState(0, Qt.CheckState.Unchecked)
    assert window.document.node(((0, 0), (1, 0))).enabled  # not while Qt is in the item
    qtbot.waitUntil(lambda: not window.document.node(((0, 0), (1, 0))).enabled)
