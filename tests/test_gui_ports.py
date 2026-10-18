"""Ports in the editor (#181): declared and edited in the Points panel, drawn as
arrows on the canvas (a selected placement's always, the component's own with
its points)."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox

from mems_sketch import ComponentDef, PortDef, RefShape
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
    pad = ComponentDef(
        name="pad",
        shapes=[RectShape(name="body", layer="device", x0=-10, y0=-10, x1=10, y1=10)],
        ports=[PortDef(name="signal", at="body.right", direction="right", width=4)],
    )
    w.document.edit("Add pad", lambda p: p.components.__setitem__("pad", pad))
    w.document.nodes.add(RefShape(name="p1", component="pad", x=100, rotation=90))
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


def arrows(window, style):
    return [i for i in window.canvas._ports.get(style, []) if i.toolTip()]


def test_a_selected_placement_shows_its_ports_as_arrows(window):
    window.tree.select_paths([((0, 0),)])
    [arrow] = arrows(window, "selected")
    assert arrow.toolTip() == "signal"
    assert (arrow.pos().x(), arrow.pos().y()) == pytest.approx((100, 10))
    assert arrow.rotation() == pytest.approx(-90)  # up, on a canvas whose y points up
    window.tree.select_paths([])
    assert arrows(window, "selected") == []


def test_an_arrays_ports_are_drawn_once_per_copy(window):
    window.document.modifiers.add(((0, 0),), "array", columns=3, dx=40)
    window.tree.select_paths([((0, 0),)])
    assert sorted(a.toolTip() for a in arrows(window, "selected")) == [
        "signal[0]",
        "signal[1]",
        "signal[2]",
    ]


def test_the_panel_lists_ports_and_adds_edits_and_deletes_them(window):
    window.tool_windows.open("points")
    panel = window.points
    panel.actions.buttons["Add port"].click()
    assert [p.name for p in window.document.active_definition.ports] == ["port1"]
    assert panel.selected_key() == ("port", "port1")
    panel.at_edit.setCurrentText("p1.port.signal")
    panel.at_edit.lineEdit().returnPressed.emit()
    port = window.document.results.declared_ports()["port1"]
    assert (port.x, port.y, port.angle, port.width) == pytest.approx((100, 10, 90, 4))
    panel.direction_edit.setCurrentText("left")
    panel.direction_edit.lineEdit().returnPressed.emit()
    panel.width_edit.setText("2.5")
    panel.width_edit.editingFinished.emit()
    port = window.document.results.declared_ports()["port1"]
    assert (port.angle, port.width) == (180, 2.5)
    # the component's own ports are drawn while points are shown
    assert [a.toolTip() for a in arrows(window, "declared")] == ["port1"]
    rows(panel)[("port", "port1")].setText(0, "out")
    assert [p.name for p in window.document.active_definition.ports] == ["out"]
    panel.select(("port", "out"))
    panel.actions.buttons["Delete the selected point"].click()
    assert window.document.active_definition.ports == []


def test_a_placed_port_is_re_exported_as_a_port(window):
    window.tool_windows.open("points")
    panel = window.points
    assert ("p1", "port.signal") in rows(panel)  # a point of the placement
    panel.name_port(("p1", "port.signal"))
    [port] = window.document.active_definition.ports
    assert (port.name, port.at) == ("signal", "p1.port.signal")
    assert window.document.results.declared_ports()["signal"].angle == pytest.approx(90)
    panel.export_all("p1")  # its points, not its ports: none to re-export
    assert window.document.active_definition.points == []
