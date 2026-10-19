"""The status bar in three parts with fields that keep their width (#122), and
rounded corners with nothing square behind them: combo box lists and the two
panes of a split view (#110)."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QComboBox, QLabel, QMessageBox

from mems_sketch.core.shapes import Align, RectShape
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.statusbar import HINT_LEAST_PX, StatusBar


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1300, 800)
    w.show()
    qtbot.waitExposed(w)
    return w


def test_the_hint_shows_after_the_tool_not_over_the_fields(window):
    bar = window.statusBar()
    assert isinstance(bar, StatusBar)
    window.prompt("Click somewhere")
    assert bar.currentMessage() == "Click somewhere" and bar.hint.text() == "Click somewhere"
    assert window.coordinates.isVisible() and window.grid_label.isVisible()  # not covered
    bar.showMessage("for a moment", 10)
    bar.clearMessage()
    assert bar.currentMessage() == ""


def test_the_fields_keep_their_width(window):
    widths = (window.grid_label.width(), window.zoom_label.width(), window.coordinates.width())
    window._cursor_moved(-999.123, 12345.678)
    window.canvas.zoom_by(8)
    window._show_zoom()
    assert (window.grid_label.width(), window.zoom_label.width(), window.coordinates.width()) == (
        widths
    )


def test_the_point_to_snap_to_is_apart_from_the_position(window):
    window._cursor_moved(1, 2)
    assert window.coordinates.text() == "x 1.000  y 2.000"


def test_a_hint_with_too_little_room_is_left_out(window):
    hint = window.statusBar().hint
    window.prompt("Click to select, drag to move")
    hint.resize(HINT_LEAST_PX - 10, hint.height())
    assert hint.toolTip() == "Click to select, drag to move"
    assert QLabel.text(hint) == ""  # what is shown: nothing like "Cli…"


def test_the_tool_name_is_not_a_heading(window):
    assert window.tool_name.objectName() == "status-tool"


def test_a_combo_boxs_list_is_a_translucent_rounded_window(window, qtbot):
    doc = window.document
    doc.nodes.add(RectShape(name="bar", layer="device", x0=0, y0=0, x1=100, y1=20))
    doc.nodes.add(
        RectShape(
            name="post",
            layer="device",
            x0=0,
            y0=0,
            x1=10,
            y1=30,
            align=Align(point="bottom", to="bar.top"),
        )
    )
    window.tool_windows.open("properties")
    window.tree.select_paths([sorted(doc.results.inspection())[1]])
    combo = [c for c in window.properties.findChildren(QComboBox) if c.isVisible()][-1]
    combo.showPopup()
    container = combo.view().window()
    assert container.property("mems_translucent")
    image = container.grab().toImage()
    assert image.pixelColor(0, 0).alpha() == 0  # the corner: nothing behind it
    assert image.pixelColor(image.width() // 2, image.height() // 2).alpha() == 255
    combo.hidePopup()


def test_the_panes_of_a_split_view_are_islands_of_their_own(window):
    window.split_view()
    area = window.area
    assert area.split and area.objectName() == "islands"
    islands = [pane.parentWidget() for pane in area.panes]
    assert all(i.objectName() == "island" for i in islands)
    assert islands[0].geometry().right() < islands[1].geometry().left()  # the frame between
