"""Feedback on Properties: no tooltip that only repeats a readable value; defaults
read as values; the perforation explained, with an even rim; and "Make a
parameter from this value" that works everywhere and never overwrites one."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QCheckBox, QFrame, QInputDialog, QMessageBox

from mems_sketch import ComponentDef, Layer, PerforateModifier, Project, RectShape, RefShape
from mems_sketch.engine import Engine
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.help import HelpButton
from mems_sketch.gui.value_edit import ValueEdit


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 900)
    w.show()
    qtbot.waitExposed(w)
    return w


def select(window, shape):
    path = window.document.nodes.add(shape)
    window.tree.select_paths([path])
    return path


def edits(window) -> list[ValueEdit]:
    return window.properties.findChildren(ValueEdit)


def make(window, monkeypatch, edit, name: str) -> list[str]:
    errors: list[str] = []
    window.properties.error.connect(errors.append)
    window.points.error.connect(errors.append)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: (name, True))
    menu = edit.parameter_menu()
    next(a for a in menu.actions() if a.text().startswith("Make a parameter")).trigger()
    return errors


def parameters(window) -> dict:
    return {p.name: p.default for p in window.document.active_definition.parameters}


# -- tooltips -----------------------------------------------------------------------


def test_a_readable_number_has_no_tooltip_but_an_expression_says_its_value(window):
    window.document.parameters.set("w", 20.0)
    select(window, RectShape(name="r", layer="device", x0=0, y0=0, x1="w * 2", y1=4))
    by_text = {e.text(): e for e in edits(window)}
    assert by_text["4"].toolTip() == ""
    assert by_text["w * 2"].toolTip() == "w * 2 = 40"


def test_a_field_with_an_explanation_keeps_it(window):
    from mems_sketch.core.shapes import FilletShape

    select(window, FilletShape(name="f", radius=2, children=[]))
    radius = next(e for e in edits(window) if e.text() == "2")
    assert radius.toolTip().startswith("For the corners that turn out")


# -- defaults ----------------------------------------------------------------------


def test_a_default_reads_as_a_value_marked_default(window):
    select(
        window,
        RectShape(
            name="r", layer="device", x0=0, y0=0, x1=100, y1=60, modifiers=[PerforateModifier()]
        ),
    )
    hole_height = next(e for e in edits(window) if e.placeholderText() == "4")
    assert hole_height.text() == "" and hole_height.property("default") is True
    assert "empty: the default (4)" in hole_height.toolTip()


# -- the perforation ------------------------------------------------------------------


def test_every_perforation_field_is_explained(window):
    select(
        window,
        RectShape(
            name="r", layer="device", x0=0, y0=0, x1=100, y1=60, modifiers=[PerforateModifier()]
        ),
    )
    card = window.properties.findChildren(QFrame, "modifier-card")[0]
    helps = card.findChildren(HelpButton)
    assert len(helps) >= 11  # every field, Even rim among them
    assert any("pitch − hole size" in h.text_ for h in helps)


def test_even_rim_greys_out_the_margin_and_the_hole_position(window):
    select(
        window,
        RectShape(
            name="r", layer="device", x0=0, y0=0, x1=100, y1=60, modifiers=[PerforateModifier()]
        ),
    )
    margin = next(e for e in edits(window) if e.text() == "2")
    even = window.properties.findChildren(QFrame, "modifier-card")[0].findChildren(QCheckBox)[0]
    assert margin.isEnabled()
    even.setChecked(True)
    assert not margin.isEnabled()


def hole_edges(even: bool) -> tuple[list[float], list[float]]:
    p = Project(name="p")
    p.add_layer(Layer("device", 1))
    plate = RectShape(
        name="plate",
        layer="device",
        x0=0,
        y0=0,
        x1=103,
        y1=57,
        modifiers=[PerforateModifier(pitch=10, hole_size=6, corner_radius=0, even_rim=even)],
    )
    p.components["top"] = ComponentDef(name="top", shapes=[plate])
    p.top = "top"
    region = Engine().load(p).build("top").geometry.layers["device"]
    xs, ys = set(), set()
    for polygon in region.each_merged():
        for hole in polygon.holes:
            for x, y in hole.tolist():
                xs.add(round(x / 1000, 3))
                ys.add(round(y / 1000, 3))
    return sorted(xs), sorted(ys)


def test_an_even_rim_is_as_wide_as_the_webs():
    xs, ys = hole_edges(even=True)
    rim_left, web = xs[0], xs[2] - xs[1]
    assert rim_left == pytest.approx(web, abs=0.002)
    assert 103 - xs[-1] == pytest.approx(web, abs=0.002)  # the right rim too
    assert ys[0] == pytest.approx(ys[2] - ys[1], abs=0.002)  # and along y


def test_without_it_the_rim_is_the_margin_and_what_is_left_over():
    xs, _ = hole_edges(even=False)
    assert xs[0] != pytest.approx(xs[2] - xs[1], abs=0.01)


# -- making a parameter from a value ------------------------------------------------


def test_a_parameter_made_from_a_default_starts_at_it(window, monkeypatch):
    select(window, RefShape(name="pp", component="mems.perforated_plate"))
    pitch = next(e for e in edits(window) if e.placeholderText() == "10")
    assert make(window, monkeypatch, pitch, "plate_pitch") == []
    assert parameters(window)["plate_pitch"] == 10
    assert window.document.active_definition.shapes[0].params == {"pitch": "plate_pitch"}


def test_a_name_already_taken_is_refused_not_overwritten(window, monkeypatch):
    window.document.parameters.set("w", 20.0)
    select(window, RectShape(name="r", layer="device", x0=0, y0=0, x1="w", y1=4))
    height = next(e for e in edits(window) if e.text() == "4")
    errors = make(window, monkeypatch, height, "w")
    assert errors and "already has a parameter 'w'" in errors[0]
    assert parameters(window)["w"] == 20  # unchanged
    assert window.document.active_definition.shapes[0].y1 == 4


def test_the_name_offered_is_the_fields(window, monkeypatch):
    offered = []
    monkeypatch.setattr(
        QInputDialog, "getText", lambda *a, **k: offered.append(k["text"]) or ("", False)
    )
    select(window, RectShape(name="r", layer="device", x0=0, y0=0, x1=20, y1=4))
    edit = next(e for e in edits(window) if e.text() == "20")
    next(a for a in edit.parameter_menu().actions() if a.text().startswith("Make")).trigger()
    assert offered == ["x1"]


def test_a_point_field_makes_a_parameter_too(window, monkeypatch):
    name = window.document.points.add()
    window.tool_windows.open("points")
    window.points.select(("declared", name))
    x = window.points.x_edit
    x.setText("7")
    x.returnPressed.emit()
    x = window.points.x_edit
    assert make(window, monkeypatch, x, "tip_x") == []
    assert parameters(window)["tip_x"] == 7
    point = next(p for p in window.document.active_definition.points if p.name == name)
    assert point.x == "tip_x"
