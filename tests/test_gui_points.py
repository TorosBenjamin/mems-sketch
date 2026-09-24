"""The Points panel: points as objects, and points on the canvas only when needed."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from mems_sketch.core.shapes import RectShape
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.points_panel import GROUP_ROLE, KEY_ROLE


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    w.document.nodes.add(RectShape(name="beam", layer="device", x0=0, y0=0, x1=40, y1=4))
    w.document.points.add(at="beam.right")  # beam_right
    w.tool_windows.open("points")
    return w


def rows(panel):
    tree = panel.tree
    result = {}
    for i in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(i)
        if item.data(0, GROUP_ROLE) is None:
            result[item.data(0, KEY_ROLE)] = item
        for j in range(item.childCount()):
            result[item.child(j).data(0, KEY_ROLE)] = item.child(j)
    return result


def markers(window, style):
    return [i for i in window.canvas._points.get(style, []) if i.toolTip()]


def test_the_list_holds_declared_default_and_shape_points(window):
    found = rows(window.points)
    assert ("declared", "beam_right") in found
    assert ("default", "center") in found and ("beam", "top_left") in found
    assert found[("declared", "beam_right")].text(1) == "40, 2"
    assert found[("declared", "beam_right")].flags() & Qt.ItemFlag.ItemIsEditable
    assert not found[("default", "center")].flags() & Qt.ItemFlag.ItemIsEditable
    groups = [window.points.tree.topLevelItem(i).text(0) for i in range(1, 3)]
    assert groups == ["Default", "beam"]


def test_hovering_a_point_shows_it_on_the_canvas(window):
    item = rows(window.points)[("default", "top_right")]
    window.points.tree.itemEntered.emit(item, 0)
    assert [m.toolTip() for m in markers(window, "focus")] == ["top_right"]
    QApplication.sendEvent(window.points.tree.viewport(), QEvent(QEvent.Type.Leave))
    assert markers(window, "focus") == []


def test_clicking_a_point_pans_to_it_and_edits_it(window):
    window.canvas.set_view_state(4, -300, -300)
    rows(window.points)[("declared", "beam_right")].setSelected(True)
    zoom, x, y = window.canvas.view_state()
    assert zoom == pytest.approx(4)
    assert (x, y) == pytest.approx((40, 2), abs=0.5)
    panel = window.points
    assert panel.at_edit.currentText() == "beam.right"
    panel.y_edit.setText("3")
    panel.y_edit.returnPressed.emit()
    assert window.document.results.declared_points()["beam_right"] == (40, 5)
    assert panel.selected_key() == ("declared", "beam_right")  # still selected after the edit


def test_renaming_in_the_list(window):
    rows(window.points)[("declared", "beam_right")].setText(0, "tip")
    assert [p.name for p in window.document.active_definition.points] == ["tip"]
    assert window.points.selected_key() == ("declared", "tip")


def test_name_this_point(window):
    window.points.name_point(("beam", "top_left"))
    point = window.document.active_definition.points[-1]
    assert (point.name, point.at) == ("beam_top_left", "beam.top_left")
    assert window.points.selected_key() == ("declared", "beam_top_left")
    window.points.name_point(("default", "bottom_right"))
    assert window.document.active_definition.points[-1].at == "bottom_right"


def test_points_show_only_while_the_points_panel_is_open(window):
    window.tree.select_paths([((0, 0),)])
    assert [m.toolTip() for m in markers(window, "declared")] == ["beam_right"]
    assert markers(window, "selected")
    window.tool_windows.open("properties")  # the same side: Points closes
    assert markers(window, "declared") == [] and markers(window, "selected") == []
    window.settings.set("canvas/always_show_points", True)
    assert markers(window, "declared") and markers(window, "selected")


def test_re_exporting_all_points_of_a_placed_part(window):
    spring = window.document.active  # the fixture's component becomes a part of a new top
    window.document.components.new("top2")
    window.document.set_active("top2")
    window.document.nodes.add_component(spring)
    window.points.refresh()
    group = next(g for g, _ in window.points._positions if g not in ("declared", "default"))
    window.points.export_all(group)
    assert [p.at for p in window.document.active_definition.points] == [f"{group}.beam_right"]
    assert window.document.active_definition.points[0].name == "beam_right"
