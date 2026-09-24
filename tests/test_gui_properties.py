"""The Properties panel: title, paired fields, expression fields and parameters."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QInputDialog, QLabel, QMessageBox

from mems_sketch import ArrayModifier, RectShape
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


def test_real_key_presses_and_clicks_that_rebuild_the_panel_do_not_crash(window, qtbot):
    """Enter in a field, or a click on the eye or a card button, rebuilds the panel
    from inside that widget's own event: the old widgets must outlive the event."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QToolButton

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
    qtbot.keyClicks(editor.name_edit, "_2")
    qtbot.keyClick(editor.name_edit, Qt.Key.Key_Return)
    qtbot.wait(1)
    assert window.document.shapes[0].name.endswith("_2")
    qtbot.mouseClick(editor.enabled_toggle, Qt.MouseButton.LeftButton)
    qtbot.wait(1)
    assert window.document.shapes[0].enabled is False
    window.document.nodes.replace(
        ((0, 0),), window.document.shapes[0].model_copy(update={"modifiers": [ArrayModifier()]})
    )
    window.tree.select_paths([((0, 0),)])
    remove = next(b for b in window.properties.findChildren(QToolButton) if b.toolTip() == "Remove")
    qtbot.mouseClick(remove, Qt.MouseButton.LeftButton)
    qtbot.wait(1)
    assert window.document.shapes[0].modifiers == []
