"""Bugs found in usability testing before the release: Search from its action,
--help, Esc in a field, type-ahead in trees, alignment, refused values, edits
that break a slow design, waivers, process libraries and files, the Commit
panel and process switching, the comparison bar, and mirroring aligned parts
(#284–#325)."""

import dataclasses
import time

import pytest

from mems_sketch import ComponentDef, Layer, Project, RectShape, RefShape
from mems_sketch.core.shapes import Align
from mems_sketch.core.shapes.modifiers import MirrorModifier
from mems_sketch.editing.session import EditSession
from mems_sketch.engine import Engine


def _boxes(region) -> list[tuple[float, float, float, float]]:
    out = []
    for polygon in region.each_merged():
        xs = [q[0] / 1000 for q in polygon.hull.tolist()]
        ys = [q[1] / 1000 for q in polygon.hull.tolist()]
        out.append((round(min(xs), 3), round(max(xs), 3), round(min(ys), 3), round(max(ys), 3)))
    return sorted(out)


# -- engine and editing (no window) -------------------------------------------------


def test_a_mirror_on_an_aligned_shape_mirrors_where_it_is_placed():
    """#289: the image lands across the axis from the aligned shape, not from where it
    was before it was aligned."""
    p = Project(name="p")
    p.add_layer(Layer("device", 1))
    shapes = [
        RectShape(name="mass", layer="device", x0=-50, y0=-20, x1=50, y1=20),
        RectShape(
            name="comb",
            layer="device",
            x0=300,
            y0=300,
            x1=320,
            y1=310,
            align=Align(point="left", to="mass.right", dx=10, dy=0),
            modifiers=[MirrorModifier(axis="x", x="mass.center.x")],
        ),
    ]
    p.components["top"] = ComponentDef(name="top", shapes=shapes)
    p.top = "top"
    region = Engine().load(p).build("top").geometry.layers["device"]
    assert _boxes(region) == [(-80, -60, -5, 5), (-50, 50, -20, 20), (60, 80, -5, 5)]


def test_mirroring_an_aligned_shape_mirrors_its_offset():
    """#289: a copy aligned 3 µm below a point and mirrored up-down sits 3 µm above."""
    s = EditSession()
    s.nodes.add(RectShape(name="shuttle", layer="device", x0=-20, y0=-10, x1=20, y1=10))
    path = s.nodes.add(
        RectShape(
            name="spring",
            layer="device",
            x0=0,
            y0=0,
            x1=4,
            y1=30,
            align=Align(point="bottom", to="shuttle.top", dx=0, dy=-3),
        )
    )
    s.moves.mirror([path], left_right=False, center=(0.0, 0.0))
    assert s.node(path).align.dy == 3


def test_an_offset_written_as_an_expression_is_mirrored_too():
    s = EditSession()
    s.parameters.set("overlap", 3)
    s.nodes.add(RectShape(name="shuttle", layer="device", x0=-20, y0=-10, x1=20, y1=10))
    path = s.nodes.add(
        RectShape(
            name="spring",
            layer="device",
            x0=0,
            y0=0,
            x1=4,
            y1=30,
            align=Align(point="bottom", to="shuttle.top", dx=0, dy="-overlap"),
        )
    )
    s.moves.mirror([path], left_right=False, center=(0.0, 0.0))
    assert s.node(path).align.dy == "-(-overlap)"


def test_a_waiver_matches_the_geometry_the_check_sees(tmp_path):
    """#286: waived on the check's own geometry (on the grid), not the editor's,
    which differs where curves are: the waiver holds."""
    from mems_sketch.core.shapes import CircleShape

    s = EditSession()
    s.create(tmp_path / "p", "p", process="mems.surface")
    s.nodes.add(RefShape(name="frame", component="std.die_frame"))
    s.nodes.add(CircleShape(name="c", layer="device", x=100.3, y=50.7, radius=33.3))
    used = []
    real = s.results.checked
    s.results.checked = lambda component=None: used.append(component) or real(component)
    violation = max(
        (v for v in s.results.check() if v.bbox_um and v.rule == "device_anchored"),
        key=lambda v: v.bbox_um[2] - v.bbox_um[0],
    )
    used.clear()
    s.process.waive(violation, "cleared by dicing")
    assert used  # the waiver's fingerprint is of the checked geometry
    [after] = [v for v in s.results.check() if v.bbox_um == violation.bbox_um]
    assert after.waived == "cleared by dicing"


def test_a_malformed_rule_names_the_file_rule_and_key(tmp_path):
    """#295."""
    from mems_sketch.cli import main
    from mems_sketch.storage import load

    assert main(["new-process", str(tmp_path / "fab"), "--from", "mems.surface"]) == 0
    process = item_file(tmp_path / "fab" / "processes" / "fab", "process.yaml")
    text = process.read_text()
    text = text.replace(
        "device_min_width: {kind: min_width, layers: [device], value: 2}",
        "device_min_width: {kind: min_width, layers: [device], value: 2, 5}",
    )
    process.write_text(text)
    with pytest.raises(ValueError) as error:
        load(tmp_path / "fab")
    message = str(error.value)
    assert "fab.msketch" in message and "device_min_width" in message and "'5'" in message


def test_rule_changes_are_local_changes(tmp_path):
    """#290: the Commit panel lists a changed rule and a change of process."""
    from mems_sketch.core.diff import PROCESS

    s = EditSession()
    s.create(tmp_path / "p", "p", process="mems.surface")
    before = copy_project(s)
    name, rule = next(iter(s.project.process.rules.items()))
    s.process.set_rule(name, dataclasses.replace(rule, values={**rule.values, "value": 3.0}))
    texts = [c.text for c in diff(before, s) if c.group == PROCESS]
    assert f"rule {name}: value 2 → 3" in texts
    s.process.use("mems.polysilicon")
    assert any(c.what == "process" for c in diff(before, s))


def copy_project(session):
    import copy

    return copy.deepcopy(session.project)


def diff(before, session):
    from mems_sketch.core.diff import diff_projects

    return diff_projects(before, session.project)


def test_switching_process_counts_the_changes_it_drops(tmp_path):
    """#291."""
    s = EditSession()
    s.create(tmp_path / "p", "p", process="mems.surface")
    assert s.process.changes_lost_by_switching() == 0
    name, rule = next(iter(s.project.process.rules.items()))
    s.process.set_rule(name, dataclasses.replace(rule, values={**rule.values, "value": 3.0}))
    assert s.process.changes_lost_by_switching() == 1


def test_gui_help_prints_usage(capsys):
    """#294."""
    from mems_sketch.gui.app import main

    assert main(["mems-sketch", "--help"]) == 0
    assert "usage: mems-sketch [PROJECT]" in capsys.readouterr().out
    assert main(["mems-sketch", "--bogus"]) == 2


# -- the window ---------------------------------------------------------------------

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QGroupBox, QMessageBox

from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.value_edit import ValueEdit
from mems_sketch.storage.project_files import item_file


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    qtbot.waitExposed(w)
    return w


def select(window, shape):
    path = window.document.nodes.add(shape)
    window.tree.select_paths([path])
    return path


def field(window, text: str) -> ValueEdit:
    return next(e for e in window.properties.findChildren(ValueEdit) if e.text() == text)


def test_search_everywhere_opens_from_its_action(window):
    """#284: Qt's checked flag no longer lands in search(scope)."""
    window.actions_.search.trigger()
    assert window._find_dialog is not None and window._find_dialog.isVisible()


def test_selecting_a_placement_keeps_the_focus_and_opens_no_window(window, qtbot):
    """#320."""
    window.document.nodes.add(RefShape(name="pp", component="mems.perforated_plate"))
    window.document.nodes.add(RectShape(name="r", layer="device", x0=0, y0=0, x1=5, y1=5))
    window.tree.setFocus()
    shown = []
    for _ in range(3):
        qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Down)
        shown += [
            w for w in QApplication.topLevelWidgets() if isinstance(w, QGroupBox) and w.isVisible()
        ]
    assert shown == []
    assert QApplication.focusWidget() is not None


def test_esc_puts_the_stored_value_back_then_goes_to_the_canvas(window, qtbot):
    """#321."""
    select(window, RectShape(name="r", layer="device", x0=0, y0=0, x1=10, y1=4))
    edit = field(window, "4")
    qtbot.mouseClick(edit, Qt.MouseButton.LeftButton)
    qtbot.keyClicks(edit, "45")
    qtbot.keyClick(edit, Qt.Key.Key_Escape)
    assert edit.text() == "4" and edit.hasFocus()
    qtbot.keyClick(edit, Qt.Key.Key_Escape)
    assert QApplication.focusWidget() is window.canvas
    assert window.document.undo_text() == "Add r"  # nothing applied


def test_a_letter_typed_in_the_components_tree_is_not_a_tool_shortcut(window, qtbot):
    """#322."""
    tree = window.components.tree
    tree.setFocus()
    qtbot.keyClick(tree, "c")
    assert window.tool.name == "select"


def test_choosing_point_before_to_keeps_the_choice(window):
    """#288."""
    from PySide6.QtWidgets import QComboBox

    window.document.nodes.add(RectShape(name="a", layer="device", x0=0, y0=0, x1=10, y1=10))
    select(window, RectShape(name="b", layer="device", x0=20, y0=0, x1=30, y1=10))
    box = next(g for g in window.properties.findChildren(QGroupBox) if g.isCheckable())
    box.setChecked(True)
    own = box.findChildren(QComboBox)[0]
    own.setCurrentIndex(own.findText("left"))
    own.activated.emit(own.currentIndex())
    box = next(g for g in window.properties.findChildren(QGroupBox) if g.isCheckable())
    assert box.isChecked() and box.findChildren(QComboBox)[0].currentText() == "left"


def test_a_refused_value_stays_to_be_corrected_and_is_not_a_problem(window, qtbot):
    """#325."""
    notes = []
    window.notify = lambda text, *a, **k: notes.append(text)
    select(window, RectShape(name="r", layer="device", x0=0, y0=0, x1=10, y1=4))
    edit = field(window, "4")
    edit.setFocus()
    edit.setText("1/0")
    edit.returnPressed.emit()
    focused = QApplication.focusWidget()
    assert focused.text() == "1/0" and focused.property("invalid") is True
    assert notes and "error" not in window.problems_button.text()
    window.canvas.setFocus()  # left uncorrected: the stored value again, no second note
    qtbot.wait(10)
    assert field(window, "4") is not None and len(notes) == 1


def test_an_edit_that_breaks_a_slow_design_is_taken_back(window, monkeypatch):
    """#287: built without the check (a slow design), then taken back."""
    from mems_sketch.gui import app

    monkeypatch.setattr(app, "BACKGROUND_BUILD_S", -1.0)
    notes = []
    window.notify = lambda text, *a, **k: notes.append(text)
    window.document.parameters.set("n", 10)
    window.document.nodes.add(
        RefShape(name="c", component="mems.comb_drive", params={"fingers": "n"})
    )

    def settle(until):
        deadline = time.monotonic() + 20
        while not until() and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)

    settle(lambda: not window.document.check_builds)
    window.document.parameters.set("n", 12.5)
    settle(lambda: any(n.startswith("Not applied") for n in notes))
    defaults = {p.name: p.default for p in window.document.active_definition.parameters}
    assert defaults["n"] == 10
    assert any("must be an integer" in n for n in notes)


def test_a_library_of_processes_only_opens_its_process(window, tmp_path):
    """#285."""
    from mems_sketch.cli import main

    assert main(["new-process", str(tmp_path / "fab"), "--from", "mems.surface"]) == 0
    assert window.open_project(str(tmp_path / "fab"))
    assert window.area.process_view is not None
    assert "None" not in window.windowTitle()
    window.document.process.set_constant("undercut", 3)
    assert window.document.project.process.constants["undercut"] == 3


def test_the_comparison_bar_does_not_widen_the_window(window, tmp_path):
    """#314."""
    window.resize(1000, 700)
    QApplication.processEvents()
    view = window.open_comparison()
    view._set_what("a long description of what is compared " * 6)
    QApplication.processEvents()
    assert window.width() <= 1000
    assert len(view.what.text()) < 60 and view.what.toolTip().startswith("a long description")
