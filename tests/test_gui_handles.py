"""The Select tool drags the selected shape's handles (#222)."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QMessageBox
from test_gui_tools import drag, hover, mouse, two_rects

from mems_sketch.gui.app import MainWindow

BASE = ((0, 0),)


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    return w


def select_base(window):
    two_rects(window)
    window.select_click(BASE, False)


def handle(window, key):
    return next((x, y) for h, x, y in window.edit_handles if h.key == key)


def test_the_selected_shape_shows_its_handles(window):
    select_base(window)
    assert len(window.canvas.edit_handles) == 8  # 4 sides, 4 corners
    assert handle(window, "x1") == (100, 10)
    window.select_click(((0, 1),), True)  # two shapes: none
    assert window.canvas.edit_handles == []
    window.select_click(BASE, False)
    window.set_tool("move")  # only the Select tool drags them
    assert window.canvas.edit_handles == []


def test_dragging_a_side_resizes_the_shape(window):
    select_base(window)
    drag(window, (100, 10), (120, 10))
    base = window.document.node(BASE)
    assert (base.x0, base.x1) == (0, 120)
    assert handle(window, "x1") == (120, 10)  # the handles follow
    window.document.undo()
    assert window.document.node(BASE).x1 == 100


def test_dragging_a_corner_changes_both_sides(window):
    select_base(window)
    drag(window, (100, 20), (110, 40))
    base = window.document.node(BASE)
    assert (base.x1, base.y1) == (110, 40)


def test_esc_drops_a_handle_being_dragged(window):
    select_base(window)
    canvas = window.canvas
    mouse(canvas, QEvent.Type.MouseButtonPress, 100, 10)
    mouse(canvas, QEvent.Type.MouseMove, 130, 10)
    assert window.tool.busy and canvas.cursor_label.startswith("x1  100 → 130")
    window.escape()
    mouse(canvas, QEvent.Type.MouseButtonRelease, 130, 10)
    assert window.document.node(BASE).x1 == 100
    assert window.tool.name == "select" and not window.tool.busy


def test_the_cursor_shows_which_way_a_handle_drags(window, qtbot):
    select_base(window)
    viewport = window.canvas.viewport()

    def over(x, y):
        hover(window, x, y)
        qtbot.wait(40)  # (moves reach the tool at most once a frame)
        return viewport.cursor().shape()

    assert over(100, 10) == Qt.CursorShape.SizeHorCursor
    assert over(50, 20) == Qt.CursorShape.SizeVerCursor
    assert over(100, 20) == Qt.CursorShape.SizeBDiagCursor
    assert over(50, 10) == Qt.CursorShape.SizeAllCursor  # inside: moving it, as before


def test_dragging_inside_still_moves_the_shape(window):
    select_base(window)
    drag(window, (50, 10), (60, 10))
    base = window.document.node(BASE)
    assert (base.x0, base.x1) == (10, 110)


# -- phase 2: choosing a handle, adding and removing vertices, typed values ----------------


def keys(window, text):
    """Type ``text`` on the canvas; ``\n`` is Enter."""
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    for ch in text:
        key = Qt.Key.Key_Return if ch == "\n" else Qt.Key(ord(ch))
        event = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, ch.strip())
        QApplication.sendEvent(window.canvas, event)


def click_at(window, x, y):
    mouse(window.canvas, QEvent.Type.MouseButtonPress, x, y)
    mouse(window.canvas, QEvent.Type.MouseButtonRelease, x, y)


def plate(window):
    from mems_sketch.core.shapes import PolygonShape

    window.document.nodes.add(
        PolygonShape(name="plate", layer="device", points=[(0, 0), (100, 0), (100, 60), (0, 60)])
    )
    window.canvas.zoom_to(window.canvas.content_rect())
    window.select_click(BASE, False)
    return BASE


def test_a_clicked_vertex_is_chosen_and_delete_removes_it(window):
    path = plate(window)
    click_at(window, 100, 60)  # point 3
    assert window.tool.chosen == (path, "point.2")
    assert window.statusBar().currentMessage().startswith("Point 3 chosen: Delete removes it")
    active = window.canvas._active_handle
    assert window.canvas.edit_handles[active][:2] == (100, 60)  # drawn filled
    window.delete()
    assert window.document.node(path).points == [(0, 0), (100, 0), (0, 60)]  # the shape stays
    assert window.selection == [path] and window.tool.chosen is None
    window.delete()  # nothing chosen: Delete removes the shape, as before
    assert window.document.shapes == []


def test_dragging_a_sides_middle_adds_a_vertex(window):
    path = plate(window)
    drag(window, (50, 0), (50, -20))  # the middle of the bottom side
    assert window.document.node(path).points == [(0, 0), (50, -20), (100, 0), (100, 60), (0, 60)]
    assert window.tool.chosen is None  # (a + is not chosen)
    window.document.undo()
    assert len(window.document.node(path).points) == 4


def test_typing_a_value_for_a_chosen_side(window):
    select_base(window)
    click_at(window, 100, 10)  # the right side
    assert window.tool.chosen == (BASE, "x1")
    keys(window, "125")
    assert window.canvas.cursor_label == "x1 = 125▏"
    keys(window, "\n")
    assert window.document.node(BASE).x1 == 125
    assert window.tool.chosen == (BASE, "x1")  # still chosen: type again
    keys(window, "130\n")
    assert window.document.node(BASE).x1 == 130


def test_typing_while_dragging_wins_over_the_mouse(window):
    path = plate(window)
    canvas = window.canvas
    mouse(canvas, QEvent.Type.MouseButtonPress, 100, 60)
    mouse(canvas, QEvent.Type.MouseMove, 120, 80)
    keys(window, "110, 70\n")
    mouse(canvas, QEvent.Type.MouseButtonRelease, 120, 80)  # (the drag already ended)
    assert window.document.node(path).points[2] == (110, 70)


def test_esc_drops_what_is_typed_then_lets_go_of_the_handle(window):
    select_base(window)
    click_at(window, 100, 10)
    keys(window, "1")
    window.escape()
    assert window.tool.entry is None and window.tool.chosen == (BASE, "x1")
    window.escape()
    assert window.tool.chosen is None
    keys(window, "5\n")  # nothing chosen: ignored
    assert window.document.node(BASE).x1 == 100


def test_a_value_short_of_what_a_handle_takes_is_reported(window, monkeypatch):
    select_base(window)
    reported = []
    monkeypatch.setattr(window, "report_error", reported.append)
    click_at(window, 100, 20)  # a corner takes x, y
    keys(window, "5\n")
    assert window.document.node(BASE).x1 == 100
    assert reported and "x, y" in reported[-1]


def test_delete_while_typing_removes_nothing(window):
    path = plate(window)
    click_at(window, 100, 60)
    keys(window, "1")
    window.delete()
    assert len(window.document.node(path).points) == 4 and window.document.shapes


# -- phase 3: guides --------------------------------------------------------------------------


def test_a_guides_end_is_dragged_with_a_line_drawn_meanwhile(window):
    from mems_sketch.core.shapes import GuideShape

    two_rects(window)
    window.document.nodes.add(GuideShape(name="axis", x0=50, y0=-20, x1=50, y1=80))
    axis = ((0, 2),)
    window.select_click(axis, False)
    assert [h.key for h, _, _ in window.edit_handles] == ["x0 y0", "x1 y1"]
    canvas = window.canvas
    mouse(canvas, QEvent.Type.MouseButtonPress, 50, 80)
    mouse(canvas, QEvent.Type.MouseMove, 70, 80)
    assert canvas._sketch_item is not None  # the guide as it would be
    mouse(canvas, QEvent.Type.MouseButtonRelease, 70, 80)
    guide = window.document.node(axis)
    assert (guide.x1, guide.y1) == (70, 80)
    assert canvas._sketch_item is None


# -- phase 4: Alt changes the parameter -------------------------------------------------------


def test_alt_drag_changes_the_parameter_the_side_names(window):
    from mems_sketch.core.shapes import RectShape

    window.document.parameters.set("size", 100)
    window.document.nodes.add(RectShape(name="base", layer="device", x0=0, y0=0, x1="size", y1=20))
    window.canvas.zoom_to(window.canvas.content_rect())
    window.select_click(BASE, False)
    alt = Qt.KeyboardModifier.AltModifier
    canvas = window.canvas
    mouse(canvas, QEvent.Type.MouseButtonPress, 100, 10, alt)
    mouse(canvas, QEvent.Type.MouseMove, 120, 10, alt)
    assert canvas.cursor_label == "size  100 → 120"
    mouse(canvas, QEvent.Type.MouseButtonRelease, 120, 10, alt)
    assert window.document.node(BASE).x1 == "size"
    assert window.document.active_definition.parameters[0].default == 120
    assert handle(window, "x1") == (120, 10)


# -- parameters on the canvas (#266) ----------------------------------------------------------


def sized_base(window):
    from mems_sketch.core.shapes import Align, RectShape

    window.document.parameters.set("size", 100)
    window.document.nodes.add(RectShape(name="base", layer="device", x0=0, y0=0, x1="size", y1=20))
    window.document.nodes.add(
        RectShape(
            name="cap",
            layer="device",
            x0=0,
            y0=0,
            x1=10,
            y1=20,
            align=Align(point="left", to="base.right", dx=5),
        )
    )
    window.canvas.zoom_to(window.canvas.content_rect())


def label_at(window, index=0):
    """Where dimension ``index``'s label is, in µm."""
    box = window.canvas._dimension_boxes[index]
    at = window.canvas.mapToScene(box.center().toPoint())
    return at.x(), at.y()


def test_the_selected_shape_shows_its_parameters_as_dimensions(window, qtbot):
    sized_base(window)
    window.select_click(BASE, False)
    assert [d.label for d in window.canvas.dimensions] == ["size = 100"]
    qtbot.wait(20)  # (drawn: where its label is)
    assert window.canvas._dimension_boxes
    window.select_click(((0, 1),), False)  # numbers only: none
    assert window.canvas.dimensions == []
    window.select_click(BASE, False)
    window.set_tool("move")  # only with the Select tool, like the handles
    assert window.canvas.dimensions == []


def test_hovering_a_dimension_outlines_what_its_parameter_moves(window, qtbot):
    sized_base(window)
    window.select_click(BASE, False)
    window.canvas.viewport().repaint()
    x, y = label_at(window)
    hover(window, x, y)
    qtbot.wait(40)
    canvas = window.canvas
    assert canvas._hovered_dimension == 0
    assert canvas.hover_label == "size  used by base · moves cap (aligned)"
    assert canvas._hover_item is not None
    hover(window, 50, -200)  # away: back to normal
    qtbot.wait(40)
    assert canvas._hovered_dimension is None and canvas.hover_label is None


def text_in(window, text):
    """Type ``text`` (letters too) on the canvas; ``\n`` is Enter."""
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    for ch in text:
        if ch == "\n":
            event = QKeyEvent(
                QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier
            )
        else:
            event = QKeyEvent(
                QEvent.Type.KeyPress, Qt.Key.Key_unknown, Qt.KeyboardModifier.NoModifier, ch
            )
        QApplication.sendEvent(window.canvas, event)


def click_label(window, index=0):
    window.canvas.viewport().repaint()
    click_at(window, *label_at(window, index))


def size_of(window):
    return next(p.default for p in window.document.active_definition.parameters if p.name == "size")


def test_clicking_a_dimension_and_typing_sets_its_parameter(window):
    sized_base(window)
    window.select_click(BASE, False)
    click_label(window)
    assert window.tool.dimension == (BASE, "x1", 0)
    assert window.selection == [BASE]  # the click chose the dimension, not a shape
    text_in(window, "120")
    assert window.canvas._editing_dimension == (0, "120")
    assert window.canvas.cursor_label == "size  100 → 120   moves base, cap"
    assert window.canvas._drag_items  # previewed
    text_in(window, "\n")
    assert size_of(window) == 120 and window.document.node(BASE).x1 == "size"
    assert window.tool.dimension is None and not window.canvas._drag_items


def test_esc_or_a_click_elsewhere_drops_what_is_typed_for_a_dimension(window):
    sized_base(window)
    window.select_click(BASE, False)
    click_label(window)
    text_in(window, "7")
    window.escape()
    assert window.tool.dimension is None and size_of(window) == 100
    assert window.canvas._editing_dimension is None
    click_label(window)
    text_in(window, "7")
    click_at(window, 50, 10)  # on the shape
    assert window.tool.dimension is None and size_of(window) == 100


def test_letters_typed_for_a_dimension_are_not_shortcuts(window):
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    sized_base(window)
    window.select_click(BASE, False)
    click_label(window)
    override = QKeyEvent(
        QEvent.Type.ShortcutOverride, Qt.Key.Key_M, Qt.KeyboardModifier.NoModifier, "m"
    )
    QApplication.sendEvent(window.canvas, override)
    assert override.isAccepted()  # (so the Move tool's M does not fire)
    text_in(window, "span = 90\n")
    assert window.document.node(BASE).x1 == "span"
    assert window.tool.name == "select"


def test_name_equals_value_typed_for_a_chosen_handle_makes_a_parameter(window):
    two_rects(window)
    window.select_click(BASE, False)
    click_at(window, 100, 10)  # the right side, chosen
    assert window.tool.takes_text()
    text_in(window, "len = 110\n")
    assert window.document.node(BASE).x1 == "len"
    assert any(
        p.name == "len" and p.default == 110 for p in window.document.active_definition.parameters
    )


def test_a_placements_values_are_labels_to_click_and_type_in(window, qtbot):
    from mems_sketch import ComponentDef, Instance, ParamDef
    from mems_sketch.core.shapes import RectShape

    project = window.document.project
    project.define_component(
        ComponentDef(
            name="comb",
            parameters=[ParamDef(name="length", default=50), ParamDef(name="fingers", default=3)],
            shapes=[RectShape(name="spine", layer="device", x0=0, y0=0, x1="length", y1=4)],
        )
    )
    window.document.nodes.add(Instance("c1", "comb", {"length": 80}))
    window.canvas.zoom_to(window.canvas.content_rect())
    window.select_click(BASE, False)
    assert [d.label for d in window.canvas.dimensions] == ["length = 80", "fingers = 3"]
    window.canvas.viewport().repaint()
    hover(window, *label_at(window, 1))
    qtbot.wait(40)
    assert window.canvas.hover_label == "fingers of c1 (comb)"
    click_label(window, 1)
    text_in(window, "8\n")
    assert window.document.node(BASE).params == {"length": 80, "fingers": 8}
