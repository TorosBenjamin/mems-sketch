"""Properties polish (#215, #217, #218, #225, #238, #257): the scroll position kept
after Enter, no Apply button, multi-line descriptions, parameter names with a
"?", markdown descriptions, defaults shown in empty fields, fields that do
nothing greyed out, units, and more."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QToolButton,
)

from mems_sketch import (
    ArrayModifier,
    ComponentDef,
    MirrorModifier,
    PerforateModifier,
    PortDef,
    RefShape,
)
from mems_sketch.core.shapes import CircleShape, ConnectionShape, RectShape, RouteShape
from mems_sketch.gui import widgets
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.help import HelpButton
from mems_sketch.gui.value_edit import ValueEdit


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    return w


def select(window, shape) -> tuple:
    path = window.document.nodes.add(shape)
    window.tree.select_paths([path])
    return path


def card(window) -> QFrame:
    return window.properties.findChildren(QFrame, "modifier-card")[0]


def edits(widget) -> list[ValueEdit]:
    return widget.findChildren(ValueEdit)


# -- #215: Enter keeps the panel where it was scrolled to -----------------------


def test_applying_a_field_keeps_the_scroll_position(window, qtbot):
    window.resize(1200, 420)  # a short panel: the modifiers are below the fold
    modifiers = [ArrayModifier(columns=2, dx=10) for _ in range(4)]
    select(window, RectShape(name="r", layer="device", x0=0, y0=0, x1=4, y1=4, modifiers=modifiers))
    panel = window.properties
    bar = panel.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0)
    bar.setValue(bar.maximum())
    scrolled = bar.value()
    field = edits(window.properties.findChildren(QFrame, "modifier-card")[-1])[0]
    field.setText("3")
    field.returnPressed.emit()
    qtbot.wait(10)
    assert window.document.shapes[0].modifiers[-1].columns == 3
    assert bar.value() == scrolled


def test_another_shape_starts_at_the_top(window, qtbot):
    window.resize(1200, 420)
    modifiers = [ArrayModifier(columns=2, dx=10) for _ in range(4)]
    select(window, RectShape(name="a", layer="device", x0=0, y0=0, x1=4, y1=4, modifiers=modifiers))
    bar = window.properties.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0)
    bar.setValue(bar.maximum())
    select(window, RectShape(name="b", layer="device", x0=9, y0=0, x1=12, y1=4))
    qtbot.wait(10)
    assert bar.value() == 0


# -- #217: no Apply button; multi-line boxes apply on Ctrl+Enter -----------------


def test_a_polygons_points_apply_on_ctrl_enter_without_an_apply_button(window, qtbot):
    from mems_sketch import PolygonShape

    select(window, PolygonShape(name="p", layer="device", points=[(0, 0), (10, 0), (0, 10)]))
    names = {b.text() for b in window.properties.findChildren(QToolButton)}
    assert "Apply" not in names
    box = next(
        b
        for b in window.properties.findChildren(widgets.QPlainTextEdit)
        if "0, 0" in b.toPlainText()
    )
    box.setFocus()
    box.setPlainText("0, 0\n20, 0\n0, 20")
    qtbot.keyClick(box, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert window.document.shapes[0].points[1] == (20, 0)


def test_a_refused_value_marks_its_field_with_why(window):
    select(window, CircleShape(name="c", layer="device", radius=5))
    radius = next(e for e in edits(window.properties) if e.text() == "5")
    radius.setFocus()
    radius.setText("nan")
    radius.returnPressed.emit()
    marked = [e for e in edits(window.properties) if e.property("invalid")]
    assert len(marked) == 1 and marked[0].toolTip().startswith("Not applied:")
    assert window.document.shapes[0].radius == 5


# -- #218: the component description is a box of several lines -----------------


def test_the_component_description_is_a_multi_line_box(window, qtbot):
    window.tree.select_paths([])
    box = window.properties.description_edit
    assert isinstance(box, widgets.DescriptionEdit)
    short = box.height()
    box.setFocus()
    box.setPlainText("A plate.\n\nIt holds the fingers.\nAnd the springs.\nAnd more.")
    assert box.height() > short  # it grows with its text
    qtbot.keyClick(box, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert window.document.active_definition.description.startswith("A plate.\n\nIt holds")


def test_a_point_description_is_a_multi_line_box(window):
    name = window.document.points.add()
    window.tool_windows.open("points")
    window.points.select(("declared", name))
    assert window.points.findChildren(widgets.DescriptionEdit)


# -- #225: a placed component's parameters by name, the description behind "?" --


def test_placed_parameters_are_labelled_by_their_description_with_help(window):
    select(window, RefShape(name="plate", component="mems.perforated_plate"))
    box = next(
        b
        for b in window.properties.findChildren(QGroupBox)
        if b.title().startswith("Parameters of")
    )
    texts = {label.text() for label in box.findChildren(QLabel)}
    # The description's phrase as the label (#329); its name and all of it on hover.
    assert "Hole pitch" in texts and "pitch" not in texts
    assert box.findChildren(HelpButton)
    assert all(e.minimumWidth() >= 60 for e in edits(box))


# -- #238: markdown descriptions ------------------------------------------------


def test_markdown_code_spans_are_shown_as_code():
    shown = widgets.markdown_html("Set `stiffness` from *E*.\n\nSecond `a < b`.")
    assert "<code>stiffness</code>" in shown and "<i>E</i>" in shown
    assert shown.count("<p>") == 2 and "a &lt; b" in shown and "`" not in shown


def test_a_library_components_description_is_rendered(window):
    window.open_component("mems.accelerometer")
    window.tree.select_paths([])
    labels = [
        label
        for label in window.properties.findChildren(QLabel)
        if label.textFormat() == Qt.TextFormat.RichText and "<code>" in label.text()
    ]
    assert labels


# -- #257 ------------------------------------------------------------------------


def test_empty_optional_fields_show_their_default(window):
    select(
        window,
        RectShape(
            name="r",
            layer="device",
            x0=0,
            y0=0,
            x1=100,
            y1=100,
            modifiers=[PerforateModifier(pitch=10, hole_size=4)],
        ),
    )
    hints = {e.placeholderText() for e in edits(card(window))}
    assert {"10", "4", "2 (round)", "auto"} <= hints  # row pitch, height, corners, x/y


def test_perforate_x_and_y_share_one_row(window):
    select(
        window,
        RectShape(
            name="r", layer="device", x0=0, y0=0, x1=50, y1=50, modifiers=[PerforateModifier()]
        ),
    )
    centred = [e for e in edits(card(window)) if e.placeholderText() == "auto"]
    assert len(centred) == 2 and centred[0].parentWidget() is centred[1].parentWidget()


def test_polar_step_shows_360_over_count(window):
    from mems_sketch import PolarArrayModifier

    select(
        window,
        RectShape(
            name="r",
            layer="device",
            x0=0,
            y0=0,
            x1=5,
            y1=5,
            modifiers=[PolarArrayModifier(count=6)],
        ),
    )
    assert "60" in {e.placeholderText() for e in edits(card(window))}


def test_mirror_axis_is_explained_and_greys_out_the_unused_line(window):
    select(
        window,
        RectShape(name="r", layer="device", x0=0, y0=0, x1=5, y1=5, modifiers=[MirrorModifier()]),
    )
    axis = next(c for c in card(window).findChildren(QComboBox) if c.currentData() == "x")
    assert axis.currentText() == "x" and axis.toolTip().startswith("x: mirrors x")
    x, y = [e for e in edits(card(window)) if e.prefix in ("x", "y")]
    assert x.isEnabled() and not y.isEnabled()
    axis.setCurrentIndex(axis.findData("y"))
    assert not x.isEnabled() and y.isEnabled()
    axis.setCurrentIndex(axis.findData("both"))
    assert x.isEnabled() and y.isEnabled()


def test_a_placement_mirrors_about_the_x_axis(window):
    select(window, RefShape(name="plate", component="mems.perforated_plate"))
    texts = {label.text() for label in window.properties.findChildren(QLabel)}
    assert "Mirror about x axis" in texts


@pytest.fixture
def pads(window):
    pad = ComponentDef(
        name="pad",
        shapes=[RectShape(name="body", layer="device", x0=-10, y0=-10, x1=10, y1=10)],
        ports=[PortDef(name="signal", at="body.right", direction="right", width=4)],
    )
    window.document.edit("Add pad", lambda p: p.components.__setitem__("pad", pad))
    window.document.nodes.add(RefShape(name="a", component="pad"))
    window.document.nodes.add(RefShape(name="b", component="pad", x=200, y=80, rotation=180))
    return window


def test_route_radius_is_greyed_out_for_sharp_bends(pads):
    window = pads
    select(window, RouteShape(name="wire", start="a.port.signal", end="b.port.signal"))
    radius = next(e for e in edits(window.properties) if "radius" in e.toolTip())
    assert not radius.isEnabled()
    bend = next(c for c in window.properties.findChildren(QComboBox) if c.currentData() == "sharp")
    bend.setCurrentIndex(bend.findData("round"))
    assert radius.isEnabled()


def test_route_ends_do_not_offer_its_own_ports(pads):
    window = pads
    select(window, RouteShape(name="wire", start="a.port.signal", end="b.port.signal"))
    start = next(
        c for c in window.properties.findChildren(QComboBox) if c.currentText() == "a.port.signal"
    )
    offered = {start.itemText(i) for i in range(start.count())}
    assert "b.port.signal" in offered and not any(o.startswith("wire.") for o in offered)


def test_connection_ends_are_ports_added_from_a_list(pads):
    window = pads
    window.document.nodes.add(RefShape(name="c", component="pad", x=40, rotation=180))
    select(window, ConnectionShape(name="join", ends=["a.port.signal", "c.port.signal"]))
    ends = next(e for e in window.properties.findChildren(QLineEdit) if e.text().startswith("a."))
    assert ends.placeholderText().startswith("ports")
    add = next(
        b for b in window.properties.findChildren(QToolButton) if b.toolTip() == "Add a port"
    )
    offered = {a.text() for a in add.menu().actions()}
    assert {"a.port.signal", "b.port.signal", "c.port.signal"} <= offered
    assert not any(o.startswith("join.") for o in offered)
    entry = next(a for a in add.menu().actions() if a.text() == "b.port.signal")
    outcome = []
    window.properties.applied.connect(lambda: outcome.append("applied"))
    window.properties.error.connect(outcome.append)
    entry.trigger()
    assert outcome  # written into the field and applied (three ends may be refused)


def test_lengths_show_their_unit(window):
    select(window, CircleShape(name="c", layer="device", radius=5))
    assert {e.unit for e in edits(window.properties) if e.text() in ("0", "5")} == {"µm"}


def test_several_shapes_selected_says_how_many(window):
    a = window.document.nodes.add(CircleShape(name="c", layer="device", radius=5))
    b = window.document.nodes.add(CircleShape(name="d", layer="device", x=20, radius=5))
    window.tree.select_paths([a, b])
    texts = [label.text() for label in window.properties.findChildren(QLabel)]
    assert any(t.startswith("2 shapes selected") for t in texts)
