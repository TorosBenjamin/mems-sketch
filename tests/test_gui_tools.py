import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from mems_sketch.core.shapes import RectShape, RefShape, TransformShape
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.editor_state import state_path

EXAMPLES = Path(__file__).parent.parent / "examples"
NONE = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    return w


def mouse(canvas, kind, x, y, modifiers=NONE):
    pos = QPointF(canvas.mapFromScene(QPointF(x, y)))
    buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else LEFT
    event = QMouseEvent(kind, pos, canvas.viewport().mapToGlobal(pos), LEFT, buttons, modifiers)
    QApplication.sendEvent(canvas.viewport(), event)


def click(window, x, y, modifiers=NONE):
    mouse(window.canvas, QEvent.Type.MouseButtonPress, x, y, modifiers)
    mouse(window.canvas, QEvent.Type.MouseButtonRelease, x, y, modifiers)


def hover(window, x, y, modifiers=NONE):
    pos = QPointF(window.canvas.mapFromScene(QPointF(x, y)))
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        pos,
        window.canvas.viewport().mapToGlobal(pos),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        modifiers,
    )
    QApplication.sendEvent(window.canvas.viewport(), event)


def drag(window, start, end, modifiers=NONE):
    canvas = window.canvas
    mouse(canvas, QEvent.Type.MouseButtonPress, *start, modifiers)
    for k in range(1, 6):
        t = k / 5
        p = (start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t)
        mouse(canvas, QEvent.Type.MouseMove, *p, modifiers)
    mouse(canvas, QEvent.Type.MouseButtonRelease, *end, modifiers)


def rect(name, x0, y0, x1, y1):
    return RectShape(name=name, layer="device", x0=x0, y0=y0, x1=x1, y1=y1)


def two_rects(window):
    window.document.nodes.add(rect("base", 0, 0, 100, 20))
    window.document.nodes.add(rect("post", 150, 50, 160, 60))
    window.canvas.zoom_to(window.canvas.content_rect())


# -- tools -------------------------------------------------------------------


def test_tools_switch_by_action_and_esc_goes_back_to_select(window):
    assert window.tool.name == "select"
    window.tool_actions["measure"].trigger()
    assert window.tool.name == "measure" and window.tool_actions["measure"].isChecked()
    window.escape()
    assert window.tool.name == "select"
    window.set_tool("move")
    again = MainWindow()  # the active tool is remembered
    assert again.tool.name == "move"
    again.close()


def test_hand_tool_pans_instead_of_selecting(window):
    two_rects(window)
    window.set_tool("hand")
    before = window.canvas.view_state()
    drag(window, (50, 10), (0, 10))
    after = window.canvas.view_state()
    assert after[1] > before[1] + 20  # the view moved right as the content was dragged left
    assert window.selection == []


def test_space_pans_in_any_tool(window):
    two_rects(window)
    canvas = window.canvas
    QApplication.sendEvent(canvas, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, NONE))
    before = canvas.view_state()
    drag(window, (50, 10), (0, 10))
    QApplication.sendEvent(canvas, QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Space, NONE))
    assert canvas.view_state()[1] > before[1] + 20
    assert window.document.node(((0, 0),)).x0 == 0  # nothing was moved


def test_box_selection(window):
    two_rects(window)
    drag(window, (-10, -10), (120, 30))
    assert window.selection == [((0, 0),)]
    drag(window, (140, 40), (170, 70), Qt.KeyboardModifier.ShiftModifier)
    assert set(window.selection) == {((0, 0),), ((0, 1),)}


def test_move_tool_places_a_base_point_exactly(window):
    two_rects(window)
    window.tree.select_paths([((0, 1),)])
    window.set_tool("move")
    click(window, 150.4, 49.6)  # base: the post's bottom-left corner (snaps)
    hover(window, 100.3, 20.2)
    click(window, 100.3, 20.2)  # destination: the base's top-right corner (snaps)
    post = window.document.node(((0, 1),))
    assert (post.x0, post.y0) == pytest.approx((100, 20))
    assert window.tool.name == "move"  # tools stay active


def test_move_by_typed_amount(window, monkeypatch):
    two_rects(window)
    window.tree.select_paths([((0, 1),)])
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("10, -2.5", True))
    window.move_by()
    assert (window.document.node(((0, 1),)).x0, window.document.node(((0, 1),)).y0) == (160, 47.5)


def test_rotate_tool_snaps_to_15_degrees(window):
    window.document.nodes.add(RefShape(name="pad", component="anchor", x=0, y=0))
    window.canvas.zoom_to(window.canvas.content_rect())
    window.tree.select_paths([((0, 0),)])
    window.set_tool("rotate")
    click(window, 0, 0)  # pivot: the centre
    hover(window, 1, 18)  # about 87°
    click(window, 1, 18)
    assert window.document.node(((0, 0),)).rotation == 90


def test_rotate_and_mirror_buttons(window):
    window.document.nodes.add(rect("r", 0, 0, 20, 10))
    window.tree.select_paths([((0, 0),)])
    window.rotate_selection(90)
    node = window.document.node(((0, 0),))
    assert isinstance(node, TransformShape) and node.rotation == 90
    box = window.document.results.highlight([((0, 0),)]).layers["device"].bbox()
    assert (box.width(), box.height()) == (10000, 20000)
    assert box.center().x == 10000 and box.center().y == 5000  # about its own centre
    window.mirror_selection(True)
    assert window.document.node(((0, 0),)).mirror_x


def test_measure_tool_snaps_and_keeps_rulers(window):
    two_rects(window)
    window.set_tool("measure")
    click(window, 100.2, 19.8)  # base.top_right
    hover(window, 150.3, 50.2)
    click(window, 150.3, 50.2)  # post.bottom_left
    assert window.rulers["top"] == [pytest.approx((100, 20, 150, 50))]
    window.clear_rulers()
    assert "top" not in window.rulers


def test_measure_works_on_read_only_tabs(window):
    window.open_component("anchor")
    window.set_tool("measure")
    click(window, -20, -20)
    click(window, 20, -20)
    assert window.rulers["anchor"] == [pytest.approx((-20, -20, 20, -20))]


# -- editor state ------------------------------------------------------------


@pytest.fixture
def example(window, tmp_path):
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
    window.open_project(str(tmp_path / "resonator"))
    return tmp_path / "resonator"


def test_editor_state_is_saved_in_the_project_and_restored(window, example, qtbot):
    w = window
    w.open_component("suspension")
    w.tree.select_paths([((0, 1),)])
    w.canvas.set_view_state(7.5, 12, 34)
    w.mode_box.setCurrentIndex(w.mode_box.findData("etched"))
    w.split_view()
    w.open_component("std.perforated_plate")
    w.document.set_trial("pitch", 30)
    w.set_tool("measure")
    click(w, -80, -80)
    click(w, 80, -80)
    w.layers.visibility_changed.emit("anchor", False)
    w.layers.visible["anchor"] = False
    w.components.collapsed.add("Built-in")
    w.save_editor_state()

    path = state_path(example)
    assert path.exists()
    assert (path.parent / ".gitignore").read_text().strip().endswith("*")

    other = MainWindow()
    qtbot.addWidget(other)
    other.resize(1200, 800)
    other.show()
    other.open_project(str(example))
    tabs = [
        [v.component for v in (p.widget(i) for i in range(p.count()))] for p in other.area.panes
    ]
    assert tabs == [["top", "suspension"], ["suspension", "std.perforated_plate"]]
    assert other.area.current.component == "std.perforated_plate"
    left = other.area.find("suspension", other.area.panes[0])
    assert left.view_mode == "etched" and left.selection == [((0, 1),)]
    zoom, x, y = left.canvas.view_state()
    assert zoom == pytest.approx(7.5) and (x, y) == pytest.approx((12, 34), abs=1)
    assert other.document.trials == {"std.perforated_plate": {"pitch": 30}}
    assert other.rulers["std.perforated_plate"] == [pytest.approx((-80, -80, 80, -80))]
    assert other.layers.visible == {"anchor": False}
    assert "Built-in" in other.components.collapsed
    other.close()


def test_damaged_or_foreign_state_is_ignored(window, example):
    path = state_path(example)
    path.parent.mkdir(exist_ok=True)
    path.write_text("{ not json")
    window.open_project(str(example))
    assert [v.component for v in window.area.views()] == ["top"]
    path.write_text(json.dumps({"version": 1, "panes": [{"tabs": [{"component": 3}]}]}))
    window.open_project(str(example))
    assert [v.component for v in window.area.views()] == ["top"]


# -- drawing tools -----------------------------------------------------------

SHIFT = Qt.KeyboardModifier.ShiftModifier


def canvas_area(window):
    window.canvas.set_view_state(4, 45, 40)  # 4 px per µm: a 5 µm grid
    assert 5 % window.canvas.grid_step() == 0  # the coordinates below lie on the grid


def key(window, which):
    event = QKeyEvent(QEvent.Type.KeyPress, which, NONE)
    QApplication.sendEvent(window.canvas, event)


def double_click(window, x, y):
    mouse(window.canvas, QEvent.Type.MouseButtonPress, x, y)
    mouse(window.canvas, QEvent.Type.MouseButtonRelease, x, y)
    mouse(window.canvas, QEvent.Type.MouseButtonDblClick, x, y)
    mouse(window.canvas, QEvent.Type.MouseButtonRelease, x, y)


def drawn(window):
    return window.document.shapes[-1]


def test_rectangle_by_dragging_or_two_clicks(window):
    canvas_area(window)
    window.set_tool("rect")
    drag(window, (0.2, -0.3), (40.1, 20.2))  # snaps to the grid
    rect_ = drawn(window)
    assert isinstance(rect_, RectShape) and rect_.layer == "device"
    assert (rect_.x0, rect_.y0, rect_.x1, rect_.y1) == (0, 0, 40, 20)
    assert rect_.name == "rect1" and window.selection == [((0, 0),)]
    click(window, 100, 50)  # opposite corners, in either order
    hover(window, 60, 10)
    click(window, 60, 10)
    assert (drawn(window).x0, drawn(window).y0, drawn(window).x1) == (60, 10, 100)
    assert window.tool.name == "rect"  # stays active for the next one


def test_rectangle_with_shift_is_a_square_and_snaps_to_corners(window):
    canvas_area(window)
    window.set_tool("rect")
    click(window, 0, 0)
    click(window, 30, 10, SHIFT)
    square = drawn(window)
    assert (square.x1 - square.x0, square.y1 - square.y0) == (30, 30)
    click(window, 30.4, 29.7)  # the square's corner, a little off
    click(window, 50, 50)
    assert (drawn(window).x0, drawn(window).y0) == (30, 30)


def test_empty_rectangle_is_refused(window, monkeypatch):
    errors = []
    monkeypatch.setattr(window, "report_error", errors.append)
    canvas_area(window)
    window.set_tool("rect")
    click(window, 10, 10)
    click(window, 10, 40)
    assert window.document.shapes == [] and errors == ["the rectangle has no area"]


def test_circle_by_centre_and_radius(window):
    canvas_area(window)
    window.set_tool("circle")
    click(window, 20, 20)
    hover(window, 45.2, 20)
    click(window, 45.2, 20)
    circle = drawn(window)
    assert (circle.kind, circle.x, circle.y, circle.radius) == ("circle", 20, 20, 25)


def test_polygon_closes_on_the_first_point_and_backspace_takes_back(window):
    canvas_area(window)
    window.set_tool("polygon")
    for x, y in [(0, 0), (60, 0), (80, 80), (30, 50)]:
        click(window, x, y)
    key(window, Qt.Key.Key_Backspace)  # takes back (30, 50)
    assert window.tool.placed == [(0, 0), (60, 0), (80, 80)]
    click(window, 0.3, 0.4)  # on the first point: closes
    polygon = drawn(window)
    assert polygon.kind == "polygon" and polygon.points == [(0, 0), (60, 0), (80, 80)]
    assert not window.tool.busy


def test_polygon_with_shift_keeps_45_degrees_and_enter_finishes(window):
    canvas_area(window)
    window.set_tool("polygon")
    click(window, 0, 0)
    click(window, 50, 3, SHIFT)  # horizontal
    click(window, 80, 21, SHIFT)  # (80, 20) on the grid, pulled onto the 45° line
    key(window, Qt.Key.Key_Return)
    assert drawn(window).points == [(0, 0), (50, 0), (75, 25)]


def test_path_by_clicks_and_double_click_with_the_chosen_width(window):
    canvas_area(window)
    window.set_tool("path")
    window.width_box.setValue(3.5)
    click(window, 0, 0)
    click(window, 100, 0)
    double_click(window, 100, 60)
    path = drawn(window)
    assert path.kind == "path" and path.width == 3.5
    assert path.points == [(0, 0), (100, 0), (100, 60)]


def test_drawing_layer_from_the_toolbar_or_layers_panel(window):
    canvas_area(window)
    window.layer_box.setCurrentText("metal")
    window.set_tool("rect")
    drag(window, (0, 0), (20, 20))
    assert drawn(window).layer == "metal"
    window.layers.layers.setCurrentCell(1, 0)  # the anchor layer
    assert window.draw_layer == "anchor" and window.layer_box.currentText() == "anchor"
    assert window.editor_state()["drawing"] == {"layer": "anchor", "path_width": 2.0}


def test_drawing_is_refused_on_read_only_tabs(window, monkeypatch):
    errors = []
    monkeypatch.setattr(window, "report_error", errors.append)
    window.open_component("comb_drive")
    assert window.document.read_only
    canvas_area(window)
    window.set_tool("rect")
    click(window, 0, 0)
    assert not window.tool.busy and errors and "read-only" in errors[0]


def test_esc_cancels_drawing_then_leaves_the_tool(window):
    canvas_area(window)
    window.set_tool("polygon")
    click(window, 0, 0)
    click(window, 50, 0)
    window.escape()
    assert window.tool.name == "polygon" and not window.tool.busy
    window.escape()
    assert window.tool.name == "select"
    assert window.document.shapes == []


def test_switching_tabs_drops_a_shape_being_drawn(window):
    canvas_area(window)
    first = window.area.current
    window.set_tool("polygon")
    for x, y in [(0, 0), (50, 0), (50, 40)]:
        click(window, x, y)
    hover(window, 20, 40)
    assert first.canvas._sketch_item is not None and first.canvas._drag_items
    window.open_component("comb_drive")
    assert first.canvas._sketch_item is None and not first.canvas._drag_items
    assert not window.tool.busy
