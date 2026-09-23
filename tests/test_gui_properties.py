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
