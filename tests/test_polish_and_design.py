"""Polish from usability testing and the design reviews: canvas colours and
violation markers, the violation list, canvas labels, perforation and route
widths, panels and icons, keyboard and accessibility, feedback, shortcuts and
menus, export (#297–#329)."""

import pytest

from mems_sketch import ComponentDef, Layer, PerforateModifier, Project, RectShape, RefShape
from mems_sketch.core.process import Rule
from mems_sketch.core.shapes import Align, PathShape
from mems_sketch.process import rules


def project(*shapes, rule_list=()) -> Project:
    p = Project(name="p")
    for name, gds in (("device", 1), ("anchor", 2)):
        p.add_layer(Layer(name, gds))
    for rule in rule_list:
        p.process.add_rule(rule)
    p.components["top"] = ComponentDef(name="top", shapes=list(shapes))
    p.top = "top"
    return p


# -- rules (no window) ----------------------------------------------------------------


def test_a_gap_names_the_shapes_on_either_side():
    """#300: a min-space violation lies between shapes, on none of them."""
    a = RectShape(name="left", layer="device", x0=0, y0=0, x1=10, y1=10)
    b = RectShape(name="right", layer="device", x0=11, y0=0, x1=21, y1=10)
    p = project(a, b, rule_list=[Rule("space", "min_space", ["device"], {"value": 2})])
    found = rules.check(p, component="top")
    [gap] = rules.name_shapes(found, p, "top", rules.records_of(p, "top"))
    assert set(gap.shapes) == {"left", "right"}


def test_a_ring_does_not_name_what_lies_inside_it():
    """#300: the frame ring's violation is on the ring, not on the parts inside."""
    from mems_sketch.core.shapes import BooleanShape

    ring = BooleanShape(
        name="ring",
        op="subtract",
        a=[RectShape(layer="device", x0=-100, y0=-100, x1=100, y1=100)],
        b=[RectShape(layer="device", x0=-90, y0=-90, x1=90, y1=90)],
    )
    part = RectShape(name="part", layer="device", x0=-5, y0=-5, x1=5, y1=5)
    p = project(ring, part, rule_list=[Rule("anch", "anchored", ["device", "anchor"], {})])
    found = rules.check(p, component="top")
    named = rules.name_shapes(found, p, "top", rules.records_of(p, "top"))
    on_ring = [v for v in named if v.bbox_um and v.bbox_um[2] - v.bbox_um[0] > 150]
    assert on_ring and on_ring[0].shapes == ("ring",)


def test_unmeasured_gaps_are_worded_like_measured_ones():
    """#300: more gaps than are measured one by one read 'gap < 2 µm' too."""
    thin = [
        PathShape(name=f"p{i}", layer="device", points=[(i * 3.0, 0.0), (i * 3.0, 9.0)], width=2)
        for i in range(80)
    ]
    p = project(*thin, rule_list=[Rule("space", "min_space", ["device"], {"value": 2})])
    messages = {v.message.split(" µm gap")[-1] for v in rules.check(p, component="top")}
    assert not any("spacing" in m for m in messages)


# -- the window -------------------------------------------------------------------------

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QToolButton

from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.panels import GROUP_ROLE, VIOLATION_ROLE


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    qtbot.waitExposed(w)
    return w


def messages(window) -> list:
    return [window.messages.item(i) for i in range(window.messages.count())]


def test_errors_and_warnings_are_counted_apart(window):
    """#298."""
    window._show_problem_count(2, 3)
    assert window.problems_button.text() == "2 errors, 3 warnings"
    window._show_problem_count(0, 1)
    assert window.problems_button.text() == "1 warning"


def test_many_violations_of_a_rule_on_one_shape_are_one_line(window):
    """#300: a perforation's webs, one line; right-click lists them."""
    from mems_sketch.process.rules import Violation

    many = [
        Violation(
            "space",
            "device",
            "1.5 µm gap < 2 µm",
            (i, 0, i + 1, 1),
            measured=1.5,
            shapes=("plate",),
        )
        for i in range(10)
    ]
    window.messages.show_messages([], many, component="top")
    groups = [i for i in messages(window) if i.data(GROUP_ROLE) is not None]
    assert len(groups) == 1 and "10 places on plate, smallest 1.5 µm" in groups[0].text()
    menu = window.messages.context_menu(groups[0])
    texts = [a.text() for a in menu.actions()]
    assert "List the 10 one by one" in texts and "Waive these 10…" in texts
    menu.actions()[0].trigger()
    assert len([i for i in messages(window) if isinstance(i.data(VIOLATION_ROLE), Violation)]) == 10


def test_all_waived_says_so(window):
    from mems_sketch.process.rules import Violation

    waived = Violation("space", "device", "gap", (0, 0, 1, 1), waived="ok")
    window.messages.show_messages([], [waived], component="top")
    assert messages(window)[0].text() == "No open rule violations; 1 waived"


def test_several_violations_are_waived_in_one_step(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    doc = window.document
    for i in range(3):  # three floating plates (anchored: a warning in the default process)
        doc.nodes.add(
            RectShape(name=f"p{i}", layer="device", x0=i * 50, y0=0, x1=i * 50 + 20, y1=20)
        )
    violations = [v for v in window.view.violations if not v.waived and v.bbox_um]
    if len(violations) < 2:
        pytest.skip("the default process reports fewer violations here")
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("test structures", True))
    window.waive(violations)
    assert doc.undo_text().startswith("Waive")
    assert all(
        v.waived for v in window.view.violations if v.bbox_um in [w.bbox_um for w in violations]
    )


def test_an_aligned_placement_greys_out_its_position(window):
    """#304."""
    from mems_sketch.gui.value_edit import ValueEdit

    window.document.nodes.add(RectShape(name="pad", layer="device", x0=0, y0=0, x1=10, y1=10))
    path = window.document.nodes.add(
        RefShape(
            name="pp",
            component="mems.perforated_plate",
            align=Align(point="center", to="pad.center"),
        )
    )
    window.tree.select_paths([path])
    disabled = [e for e in window.properties.findChildren(ValueEdit) if not e.isEnabled()]
    assert any("alignment places it" in e.toolTip() for e in disabled)


def test_a_perforation_that_will_not_release_says_so(window, tmp_path):
    """#301."""
    window.document.create(tmp_path / "p", "p", process="mems.surface")
    path = window.document.nodes.add(
        RectShape(
            name="plate", layer="device", x0=0, y0=0, x1=100, y1=60, modifiers=[PerforateModifier()]
        )
    )
    window.tree.select_paths([path])
    notes = [w for w in window.properties.findChildren(QLabel) if w.objectName() == "warning-note"]
    assert notes and "will not release" in notes[0].text()


def test_internal_parameters_are_not_canvas_labels(window):
    """#299."""
    path = window.document.nodes.add(RefShape(name="f", component="mems.folded_flexure"))
    labels = {d.expression for d in window.document.dimensions.of(path)}
    component = window.document.engine.component("mems.folded_flexure")
    assert not labels & set(component.internal)


def test_alt_numbers_show_the_panels(window):
    """#324."""
    actions = {a.text(): a for a in window.actions_.panels.actions()}
    assert actions["Parameters"].shortcut().toString() == "Alt+4"


def test_a_command_without_a_selection_hints_instead_of_failing(window):
    """#326."""
    notes = []
    window.notify = lambda text, kind="info", *a, **k: notes.append((text, kind))
    window.rename_selection()
    assert notes == [("Select one shape to rename", "info")]


def test_delete_says_what_it_did_with_undo(window):
    """#326."""
    notes = []
    window.notify = lambda text, *a, action=None, **k: notes.append((text, action))
    path = window.document.nodes.add(RectShape(name="r", layer="device", x0=0, y0=0, x1=5, y1=5))
    window.tree.select_paths([path])
    window.delete()
    text, action = notes[-1]
    assert text == "Deleted 1 shape" and action[0] == "Undo"


def test_the_add_menu_holds_the_shapes_and_their_keys(window):
    """#327, #328."""
    texts = [a.text() for a in window.actions_.insert.actions() if not a.isSeparator()]
    assert "Rectangle\tB" in texts and "Place component…" in texts
    assert window.actions_.insert.title() == "&Add"


def test_redo_also_has_ctrl_shift_z(window):
    """#327."""
    keys = [k.toString() for k in window.actions_.redo.shortcuts()]
    assert "Ctrl+Shift+Z" in keys


def test_icon_buttons_have_accessible_names(window):
    """#323."""
    unnamed = [
        b.toolTip()
        for b in window.findChildren(QToolButton)
        if b.isVisible() and b.toolTip() and not b.accessibleName()
    ]
    assert unnamed == []


def test_keyboard_focus_shows_a_ring_but_a_click_does_not(window, qtbot):
    """#323."""
    button = next(
        b
        for b in window.findChildren(QToolButton)
        if b.isVisible() and b.focusPolicy() != Qt.FocusPolicy.NoFocus
    )
    button.setFocus(Qt.FocusReason.TabFocusReason)
    QApplication.processEvents()
    assert button.property("keyfocus") is True
    button.clearFocus()
    button.setFocus(Qt.FocusReason.MouseFocusReason)
    QApplication.processEvents()
    assert not button.property("keyfocus")


def test_export_asks_before_using_trial_values(window, monkeypatch):
    """#305."""
    from PySide6.QtWidgets import QFileDialog

    asked = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Cancel,
    )
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: pytest.fail("no dialog"))
    window.document.parameters.set("w", 3)
    window.document.set_trial("w", 5)
    window.export_file()
    assert asked and "w = 5" in asked[0]
