import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QLineEdit, QMessageBox  # noqa: E402

from mems_sketch.gui.app import MainWindow  # noqa: E402


@pytest.fixture
def window(qtbot, monkeypatch):
    # Closing a modified design asks to save; answer "discard" so teardown never blocks.
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
    # x1 is the third numeric field after x0, y0.
    editor._editors["x1"] = lambda: "2 * 60"
    editor.apply()
    assert window.document.design.shapes[0].x1 == "2 * 60"


def test_boolean_from_selection_and_canvas_click(window):
    window.add_primitive("rect")
    window.add_primitive("circle")
    window.tree.select_paths([((0, 0),), ((0, 1),)])
    window.wrap("subtract")
    assert window.document.design.shapes[0].kind == "boolean"
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
    assert window.document.design.shapes[0].x1 == 100


def test_component_parameters_are_editable(window):
    window.add_component("comb_drive")
    fields = window.properties.findChildren(QLineEdit)
    assert any(f.placeholderText() == "10" for f in fields)  # default finger count
    window.document.set_variable("n", 4.0)
    window.properties._editors["params"] = lambda: {"fingers": "n"}
    window.properties.apply()
    assert window.document.design.shapes[0].params == {"fingers": "n"}


def test_undo_restores_tree_and_view_mode_switch(window):
    window.add_primitive("rect")
    window.add_primitive("circle")
    window.document.undo()
    assert window.tree.topLevelItemCount() == 1
    window.mode_box.setCurrentIndex(1)
    assert window.view_mode == "etched"
