"""The Properties panel: title, paired fields, expression fields and parameters."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QGroupBox,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QToolButton,
)

from mems_sketch import ArrayModifier, PolygonShape, RectShape
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.properties import ElidedLabel
from mems_sketch.gui.value_edit import ValueEdit


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    w.document.parameters.set("pitch", 13.0)
    w.document.nodes.add(RectShape(name="bar", layer="device", x0=0, y0=0, x1="pitch * 2", y1=4))
    w.tree.select_paths([((0, 0),)])
    return w


def fields(window) -> dict[str, ValueEdit]:
    """The value fields by prefix and row: e.g. 'From x', 'To y'."""
    editor = window.properties
    result = {}
    for edit in editor.findChildren(ValueEdit):
        row = edit.parentWidget()
        form_label = None
        for label in editor.findChildren(QLabel):
            if label.buddy() is row or label.buddy() is edit:
                form_label = label.text()
        result[f"{form_label} {edit.prefix}".strip()] = edit
    return result


def test_the_name_is_the_title_and_no_values_sit_beside_fields(window):
    editor = window.properties
    assert editor.name_edit.text() == "bar"
    assert not [lab for lab in editor.findChildren(QLabel) if lab.text().startswith("= ")]
    editor.name_edit.setText("beam")
    editor.name_edit.returnPressed.emit()
    assert window.document.shapes[0].name == "beam"


def test_x_and_y_share_a_row(window):
    edits = fields(window)
    assert {"From x", "From y", "To x", "To y"} <= set(edits)
    assert edits["From x"].parentWidget() is edits["From y"].parentWidget()


def test_expressions_are_marked_and_show_their_value(window):
    to_x, from_x = fields(window)["To x"], fields(window)["From x"]
    assert to_x.property("expression") and to_x._hint == "26"
    assert not from_x.property("expression") and from_x._hint == ""
    to_x.setText("pitch + nothing")
    assert to_x.property("invalid") and "cannot evaluate" in to_x.toolTip()


def test_names_are_completed(window):
    edit = fields(window)["From y"]
    edit.setText("pi")
    edit.setCursorPosition(2)
    edit._suggest()
    assert edit._completer.completionCount() >= 1
    assert edit._completer.currentCompletion() == "pitch"
    edit._complete("pitch")
    assert edit.text() == "pitch"
    edit.setText("2*mass")
    assert "i" in edit._names() and "pitch" in edit._names()


def test_the_parameter_button_uses_or_makes_parameters(window, monkeypatch):
    edit = fields(window)["To y"]
    menu = edit.parameter_menu()
    texts = [a.text() for a in menu.actions()]
    assert "pitch  = 13" in texts and "Make a parameter from this value…" in texts
    next(a for a in menu.actions() if a.text() == "pitch  = 13").trigger()  # applies it
    assert window.document.shapes[0].y1 == "pitch"

    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("height", True))
    edit = fields(window)["From x"]
    edit.setText("7")
    edit.parameter_menu()  # the menu offers it; the action calls this
    edit._make_parameter()
    assert window.document.shapes[0].x0 == "height"
    assert window.document.active_definition.parameter("height").default == 7


def test_use_the_value_replaces_an_expression_by_its_number(window):
    edit = fields(window)["To x"]
    edit._use(edit._hint)
    assert window.document.shapes[0].x1 == 26


def test_enabled_is_an_eye_in_the_title_row_and_acts_at_once(window):
    editor = window.properties
    assert not [lab for lab in editor.findChildren(QLabel) if lab.text() == "Enabled"]
    assert editor.enabled_toggle.isChecked()
    editor.enabled_toggle.click()
    assert window.document.shapes[0].enabled is False
    assert not window.properties.enabled_toggle.isChecked()  # the panel was rebuilt
    window.document.undo()
    assert window.document.shapes[0].enabled is True


def test_long_names_are_cut_and_do_not_widen_the_panel(window):
    long = "comb_finger_overlap_length_of_the_left_rotor"
    window.document.parameters.set(long, 12.0)
    bar = window.document.shapes[0]
    window.document.nodes.replace(
        ((0, 0),),
        bar.model_copy(
            update={
                "name": "interdigitated_comb_finger_left_side",
                "x0": f"{long} + {long}",
                "modifiers": [ArrayModifier(rows=long, dy=long)],
            }
        ),
    )
    window.tree.select_paths([((0, 0),)])
    body = window.properties.widget()
    assert body.minimumSizeHint().width() < 320
    name = window.properties.name_edit
    assert name._elided() and name.toolTip().startswith("interdigitated_comb_finger_left_side")
    assert not [lab for lab in body.findChildren(QLabel) if lab.text() == "Extent"]
    from_x = fields(window)["From x"]
    assert from_x._elided() and from_x.toolTip().startswith(f"{long} + {long}")
    summary = next(lab for lab in body.findChildren(ElidedLabel) if long in lab.text())
    assert QLabel.text(summary).endswith("…")  # shown cut; text() is all of it
    from_x.setFocus()
    assert not from_x._elided()  # all of it while editing
    name.setFocus()  # the name too (giving it focus used to raise)
    assert not name._elided()


def test_internal_parameters_are_locked_and_not_offered_where_placed(window):
    doc = window.document
    doc.components.new("pad")
    doc.parameters.set("size", 20.0)
    doc.parameters.set("inner", "size / 2")
    doc.nodes.add(RectShape(layer="device", x0=0, y0=0, x1="size", y1="inner"))
    panel = window.parameters
    panel.refresh()
    panel.table.selectRow(1)
    panel.actions.buttons["Make the selected parameters internal (or public)"].click()
    assert doc.active_definition.parameter("inner").internal
    panel.refresh()
    assert panel.table.item(1, 0).toolTip().startswith("Internal")  # and a lock
    assert panel.table.item(0, 0).toolTip().startswith("Public")
    doc.set_active("top")
    path = doc.nodes.add_component("pad")
    window.tree.select_paths([path])
    labels = [lab.text() for lab in window.properties.findChildren(QLabel)]
    assert "size" in labels and "inner" not in labels


def test_limits_are_typed_as_expressions_and_exclusive_with_a_mark(window):
    doc = window.document
    doc.components.new("pad")
    doc.parameters.set("size", 20.0)
    doc.parameters.set("enclosure", 2.0)
    panel = window.parameters
    panel.refresh()
    panel.table.item(0, 2).setText("> 0")
    panel.table.item(1, 3).setText("< size / 2")
    size, enclosure = doc.active_definition.parameters
    assert (size.min, size.min_exclusive) == (0, True)
    assert (enclosure.max, enclosure.max_exclusive) == ("size / 2", True)
    panel.refresh()
    assert panel.table.item(1, 3).text() == "< size / 2"
    assert panel.table.item(1, 3).toolTip().startswith("size / 2 = 10")
    panel.table.item(1, 3).setText("")
    assert doc.active_definition.parameter("enclosure").max is None


def test_shapes_and_placements_offer_relative_layers_and_levels(window):
    from PySide6.QtWidgets import QComboBox

    from mems_sketch import ComponentDef, RefShape

    doc = window.document
    layer = next(
        c for c in window.properties.findChildren(QComboBox) if c.currentData() == "device"
    )
    items = [layer.itemData(k) for k in range(layer.count())]
    # level-1 is left out: the component is on the bottom level, nothing is below (#297)
    assert items[:3] == ["level", "level+1", "level.anchor"]
    assert "metal" in items
    doc.project.define_component(
        ComponentDef(name="post", shapes=[RectShape(x0=0, y0=0, x1=5, y1=5)])
    )
    path = doc.nodes.add(RefShape(name="p", component="post"))
    window.tree.select_paths([path])
    level = next(
        c for c in window.properties.findChildren(QComboBox) if c.toolTip().startswith("The level")
    )
    level.setCurrentText("metal")
    window.properties.apply()
    assert doc.node(path).level == "metal"
    assert set(doc.results.geometry("top").layer_names()) >= {"metal"}
    doc.components.set_level("post", "device")
    assert doc.project.components["post"].level == "device"


def shown(widget) -> bool:
    """Whether a widget of the panel is still there and shown: the panel is built again
    when a background build finishes, deleting the widgets it had."""
    import shiboken6

    return shiboken6.isValid(widget) and widget.isVisibleTo(widget.window())


def test_a_rebuild_while_the_panel_is_replaced_keeps_the_newer_one(window):
    """Hiding the old content can make its focused field apply itself, which builds
    the panel again from inside the rebuild: the panel's fields are then the inner
    rebuild's, so its content must be the one shown (with the fonts on CI the name
    field did this, and the eye clicked was already deleted)."""
    editor = window.properties
    take, nested = editor.takeWidget, []

    def take_widget():
        old = take()
        if not nested:  # as the old name field's focus-out would
            nested.append(True)
            editor.show_node(((0, 0),))
        return old

    editor.takeWidget = take_widget
    editor.show_node(((0, 0),))
    assert nested and shown(editor.enabled_toggle) and shown(editor.name_edit)
    assert editor.widget().isAncestorOf(editor.enabled_toggle)


def test_real_key_presses_and_clicks_that_rebuild_the_panel_do_not_crash(window, qtbot):
    """Enter in a field, or a click on the eye or a card button, rebuilds the panel
    from inside that widget's own event: the old widgets must outlive the event."""
    from PySide6.QtCore import Qt

    from mems_sketch import ArrayModifier

    editor = window.properties
    for text, field in (("3", "From x"), ("pitch", "To y")):
        edit = fields(window)[field]
        window.activateWindow()
        qtbot.mouseClick(edit, Qt.MouseButton.LeftButton)  # focus, as a user gives it
        edit.selectAll()
        qtbot.keyClicks(edit, text)
        qtbot.keyClick(edit, Qt.Key.Key_Return)
        qtbot.wait(1)
    bar = window.document.shapes[0]
    assert (bar.x0, bar.y1) == (3, "pitch")
    qtbot.waitUntil(lambda: not window.view.updating)  # a slow machine builds later
    qtbot.keyClicks(editor.name_edit, "_2")
    qtbot.keyClick(editor.name_edit, Qt.Key.Key_Return)
    qtbot.wait(1)
    assert window.document.shapes[0].name.endswith("_2")
    # On a slow machine the design builds in the background, and the panel is built
    # again when it is done: take the eye of the panel as it is then.
    qtbot.waitUntil(lambda: not window.view.updating)
    qtbot.waitUntil(lambda: shown(editor.enabled_toggle))
    qtbot.mouseClick(editor.enabled_toggle, Qt.MouseButton.LeftButton)
    qtbot.wait(1)
    assert window.document.shapes[0].enabled is False
    window.document.nodes.replace(
        ((0, 0),), window.document.shapes[0].model_copy(update={"modifiers": [ArrayModifier()]})
    )
    window.tree.select_paths([((0, 0),)])
    qtbot.waitUntil(lambda: not window.view.updating)

    def remove():
        buttons = window.properties.findChildren(QToolButton)
        return next((b for b in buttons if shown(b) and b.toolTip() == "Remove"), None)

    qtbot.waitUntil(lambda: remove() is not None)
    qtbot.mouseClick(remove(), Qt.MouseButton.LeftButton)
    qtbot.wait(1)
    assert window.document.shapes[0].modifiers == []


# -- dragging values ------------------------------------------------------------------

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from mems_sketch.gui.value_edit import dragged_value

SHIFT, CTRL = Qt.KeyboardModifier.ShiftModifier, Qt.KeyboardModifier.ControlModifier


def test_how_far_a_drag_moves_a_value():
    assert dragged_value(10, 8, integer=True) == 11  # one whole step per 8 px
    assert dragged_value(10, -800, integer=True, minimum=1) == 1
    assert dragged_value(100, 10) == pytest.approx(120)  # 2 per px at this size
    assert dragged_value(100, 10, SHIFT) == pytest.approx(102)  # ten times finer
    assert dragged_value(100, 10, CTRL) == pytest.approx(100)  # round steps of 100
    assert dragged_value(0.5, 10) == pytest.approx(0.52)


def mouse(widget, kind, x, buttons=Qt.MouseButton.LeftButton):
    pos = QPointF(x, 8)
    event = QMouseEvent(
        kind,
        pos,
        widget.mapToGlobal(pos),
        Qt.MouseButton.LeftButton,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)


def drag(widget, xs):
    mouse(widget, QEvent.Type.MouseButtonPress, xs[0])
    for x in xs[1:]:
        mouse(widget, QEvent.Type.MouseMove, x)
    mouse(widget, QEvent.Type.MouseButtonRelease, xs[-1], Qt.MouseButton.NoButton)


def test_dragging_a_count_previews_live_and_applies_once(window):
    doc = window.document
    path = doc.nodes.add_component("mems.comb_drive")
    window.tree.select_paths([path])
    fingers = next(e for e in window.properties.findChildren(ValueEdit) if e.integer)
    shown = []
    window.properties.previewed.connect(lambda node: shown.append(node.params["fingers"]))
    undo_steps = len(doc._undo)
    mouse(fingers, QEvent.Type.MouseButtonPress, 20)
    for x in range(22, 70, 8):
        mouse(fingers, QEvent.Type.MouseMove, x)
    assert shown and shown[-1] > 10  # previewed, from the default (10)
    assert doc.node(path).params == {}  # nothing applied yet
    mouse(fingers, QEvent.Type.MouseButtonRelease, 70, Qt.MouseButton.NoButton)
    assert doc.node(path).params["fingers"] == shown[-1]
    assert len(doc._undo) == undo_steps + 1


def test_a_click_without_dragging_edits_the_text(window):
    edit = fields(window)["From x"]
    drag(edit, [20, 21])
    assert edit.hasFocus() and edit.selectedText() == "0"
    assert window.document.shapes[0].x0 == 0


def test_an_expression_is_not_dragged(window):
    edit = fields(window)["To x"]  # pitch * 2
    drag(edit, [20, 60])
    assert window.document.shapes[0].x1 == "pitch * 2"


def test_dragging_a_default_in_the_parameters_panel(window):
    panel = window.parameters
    panel.refresh()
    table = panel.table
    rect = table.visualItemRect(table.item(0, 1))  # pitch's default: 13
    viewport = table.viewport()
    y = rect.center().y()

    def at(kind, x, buttons=Qt.MouseButton.LeftButton):
        pos = QPointF(rect.left() + x, y)
        event = QMouseEvent(
            kind,
            pos,
            viewport.mapToGlobal(pos),
            Qt.MouseButton.LeftButton,
            buttons,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(viewport, event)

    at(QEvent.Type.MouseButtonPress, 5)
    at(QEvent.Type.MouseMove, 30)
    assert window.document.trials.get("top", {}).get("pitch") not in (None, 13)  # live
    assert window.document.active_definition.parameter("pitch").default == 13
    at(QEvent.Type.MouseButtonRelease, 30, Qt.MouseButton.NoButton)
    assert window.document.active_definition.parameter("pitch").default != 13
    assert "pitch" not in window.document.trials.get("top", {})


def test_dragging_with_a_real_mouse(window, qtbot):
    # Through the window, as a user's mouse: Qt focuses the field on the press,
    # before the field sees it, which must not turn the drag into a click.
    edit = fields(window)["From x"]
    y = edit.height() // 2
    qtbot.mousePress(edit, Qt.MouseButton.LeftButton, pos=QPoint(20, y))
    for x in range(24, 80, 6):
        qtbot.mouseMove(edit, QPoint(x, y))
    qtbot.mouseRelease(edit, Qt.MouseButton.LeftButton, pos=QPoint(80, y))
    assert window.document.shapes[0].x0 > 0
    assert not edit.hasFocus()


def test_a_real_click_edits_the_text(window, qtbot):
    edit = fields(window)["From x"]
    qtbot.mouseClick(edit, Qt.MouseButton.LeftButton, pos=QPoint(20, edit.height() // 2))
    assert edit.hasFocus() and edit.selectedText() == "0"


def test_dragging_a_default_with_a_real_mouse(window, qtbot):
    panel = window.parameters
    panel.refresh()
    table = panel.table
    rect = table.visualItemRect(table.item(0, 1))  # pitch's default: 13
    viewport, y = table.viewport(), rect.center().y()
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, pos=QPoint(rect.left() + 5, y))
    for x in range(10, 40, 5):
        qtbot.mouseMove(viewport, QPoint(rect.left() + x, y))
    qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=QPoint(rect.left() + 40, y))
    assert window.document.active_definition.parameter("pitch").default != 13


def test_there_is_no_apply_button_even_with_modifiers(window):
    bar = window.document.shapes[0]
    window.document.nodes.replace(
        ((0, 0),), bar.model_copy(update={"modifiers": [ArrayModifier()]})
    )
    window.tree.select_paths([((0, 0),)])
    texts = {b.text() for b in window.properties.findChildren(QAbstractButton)}
    assert "Apply" not in texts and "Add modifier" in texts


def test_choosing_from_a_list_applies_at_once(window):
    layer = next(
        c for c in window.properties.findChildren(QComboBox) if c.currentText() == "device"
    )
    layer.addItem("oxide", "oxide")
    layer.setCurrentText("oxide")
    layer.activated.emit(layer.currentIndex())
    assert window.document.shapes[0].layer == "oxide"


def test_the_layer_is_picked_from_a_list_with_the_layers_colours(window):
    layer = next(
        c for c in window.properties.findChildren(QComboBox) if c.currentData() == "device"
    )
    assert not layer.isEditable()  # chosen, not typed
    items = {layer.itemData(i): layer.itemText(i) for i in range(layer.count())}
    # Relative layers say where they land in this tab; named ones are themselves.
    assert items["level"] == "level · device" and items["level+1"] == "level+1 · metal"
    assert items["level.anchor"] == "level.anchor · anchor" and items["metal"] == "metal"
    assert all(not layer.itemIcon(i).isNull() for i in range(layer.count()))
    level_up = layer.findData("level+1")
    layer.setCurrentIndex(level_up)
    layer.activated.emit(level_up)
    assert window.document.shapes[0].layer == "level+1"


def test_the_empty_panel_has_the_islands_colour(window):
    window.tree.select_paths([])
    assert window.properties.widget().objectName() == "properties-body"


def test_the_alignment_points_are_lists_that_open_on_a_click(window, qtbot):
    window.document.nodes.add(RectShape(name="anchor", layer="device", x0=50, y0=0, x1=60, y1=10))
    window.tree.select_paths([((0, 0),)])
    point, target = [
        c
        for c in window.properties.findChildren(QComboBox)
        if c.placeholderText() or not c.isEditable()
    ][-2:]
    assert not point.isEditable() and not target.isEditable()  # a click anywhere opens them
    assert point.currentText() == "center"
    assert target.currentText() == "" and target.placeholderText() == "choose a point"
    assert "anchor.left" in [target.itemText(i) for i in range(target.count())]
    section = next(
        b for b in window.properties.findChildren(QGroupBox) if b.title().startswith("Align")
    )
    section.setChecked(True)  # switched on: waits for the point to align to
    target.setCurrentText("anchor.left")
    target.activated.emit(target.currentIndex())
    assert window.document.shapes[0].align.to == "anchor.left"


def _focus(qtbot, widget):
    widget.window().activateWindow()
    widget.setFocus()
    qtbot.waitUntil(widget.hasFocus)


def test_leaving_a_renamed_title_applies_it(window, qtbot):
    editor = window.properties
    _focus(qtbot, editor.name_edit)
    editor.name_edit.setText("beam")
    _focus(qtbot, window.canvas)  # clicked out of it, without Enter
    qtbot.waitUntil(lambda: window.document.shapes[0].name == "beam")
    window.tree.select_paths([])  # the form goes; the name stays
    assert window.document.shapes[0].name == "beam"


def test_an_edit_is_kept_when_another_shape_is_picked(window, qtbot):
    window.document.nodes.add(RectShape(name="other", layer="device", x0=50, y0=0, x1=60, y1=4))
    window.tree.select_paths([((0, 0),)])
    y1 = fields(window)["To y"]
    _focus(qtbot, y1)
    y1.setText("9")
    _focus(qtbot, window.canvas)
    window.tree.select_paths([((1, 0),)])  # another shape: the form is built again
    qtbot.waitUntil(lambda: window.document.shapes[0].y1 == 9)


def test_moving_to_the_next_field_applies_and_keeps_the_focus_there(window, qtbot):
    edits = fields(window)
    _focus(qtbot, edits["To x"])
    edits["To x"].setText("30")
    _focus(qtbot, edits["To y"])
    qtbot.waitUntil(lambda: window.document.shapes[0].x1 == 30)
    focused = window.focusWidget()
    assert focused is fields(window)["To y"]  # the new form's field, at the same place


def test_leaving_an_unchanged_field_adds_no_undo_step(window, qtbot):
    window.document._undo.clear()
    edits = fields(window)
    _focus(qtbot, edits["To x"])
    _focus(qtbot, window.canvas)
    qtbot.wait(20)
    assert not window.document.can_undo()


def test_leaving_a_field_that_cannot_be_evaluated_says_why(window, qtbot):
    messages = []
    window.properties.error.connect(messages.append)
    edits = fields(window)
    _focus(qtbot, edits["To x"])
    edits["To x"].setText("nonexistent + 1")
    _focus(qtbot, window.canvas)
    qtbot.wait(20)
    assert messages and window.document.shapes[0].x1 == "pitch * 2"


def test_a_refused_value_goes_back_and_does_not_block_the_next_edit(window, qtbot):
    """#247: the refused text stays to be corrected (#325), and leaving the field
    puts the stored value back, so the next edit (another field) applies."""
    messages = []
    window.properties.error.connect(messages.append)
    edits = fields(window)
    _focus(qtbot, edits["To x"])
    edits["To x"].setText("nonexistent + 1")
    qtbot.keyClick(edits["To x"], Qt.Key.Key_Return)
    assert messages and fields(window)["To x"].text() == "nonexistent + 1"
    to_y = fields(window)["To y"]
    _focus(qtbot, to_y)
    assert fields(window)["To x"].text() == "pitch * 2"
    to_y = fields(window)["To y"]
    to_y.setText("9")
    qtbot.keyClick(to_y, Qt.Key.Key_Return)
    assert window.document.shapes[0].y1 == 9 and len(messages) == 1


def test_enter_keeps_the_focus_on_the_field(window, qtbot):
    edits = fields(window)
    _focus(qtbot, edits["To y"])
    edits["To y"].setText("7")
    qtbot.keyClick(edits["To y"], Qt.Key.Key_Return)
    assert window.document.shapes[0].y1 == 7
    assert QApplication.focusWidget() is fields(window)["To y"]  # not another panel's button


def test_ticking_align_without_a_point_does_not_block_edits(window, qtbot):
    section = next(
        b for b in window.properties.findChildren(QGroupBox) if b.title().startswith("Align")
    )
    section.setChecked(True)
    to_y = fields(window)["To y"]
    to_y.setText("6")
    qtbot.keyClick(to_y, Qt.Key.Key_Return)
    assert window.document.shapes[0].y1 == 6 and window.document.shapes[0].align is None


def test_nan_and_inf_are_refused_with_a_reason(window, qtbot):
    messages = []
    window.properties.error.connect(messages.append)
    for text in ("nan", "inf", "1e999"):
        to_y = fields(window)["To y"]
        to_y.setText(text)
        qtbot.keyClick(to_y, Qt.Key.Key_Return)
        assert "not a finite number" in messages[-1]
    assert window.document.shapes[0].y1 == 4


def test_leaving_a_list_of_points_applies_it(window, qtbot):
    triangle = PolygonShape(name="tri", layer="device", points=[(0, 0), (9, 0), (0, 9)])
    window.tree.select_paths([window.document.nodes.add(triangle)])
    box = window.properties.findChild(QPlainTextEdit)
    assert box.tabChangesFocus()
    _focus(qtbot, box)
    box.setPlainText("0, 0\n20, 0\n0, 20")
    _focus(qtbot, window.canvas)
    qtbot.waitUntil(lambda: window.document.shapes[1].points[1] == (20, 0))


def test_a_layer_mapping_is_checked_and_takes_the_arrow_it_shows(window, qtbot):
    """#253: the label's → is accepted; unknown layers, empty sides and a source
    mapped twice are refused."""
    from mems_sketch import LayerMapShape

    messages = []
    window.properties.error.connect(messages.append)
    mapped = LayerMapShape(name="m", mapping={"device": "device"}, children=[])
    window.tree.select_paths([window.document.nodes.add(mapped)])

    def enter(text):
        box = window.properties.findChild(QPlainTextEdit)
        _focus(qtbot, box)
        box.setPlainText(text)
        window.properties.apply()

    enter("device → anchor")
    assert window.document.shapes[1].mapping == {"device": "anchor"}
    for text, why in (
        ("device -> nolayer", "there is no layer 'nolayer'"),
        ("device ->", "write each mapping as 'source -> target'"),
        ("device -> anchor\ndevice -> metal", "'device' is mapped twice"),
    ):
        enter(text)
        assert why in messages[-1]
    assert window.document.shapes[1].mapping == {"device": "anchor"}
