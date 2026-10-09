"""Waivers: a single rule violation accepted with a reason, kept with the
component, lapsing when the geometry around it changes (requirement DRC-13)."""

import pytest

from mems_sketch import RectShape
from mems_sketch.cli import main
from mems_sketch.editing import EditSession
from mems_sketch.process import rules
from mems_sketch.storage import load


@pytest.fixture
def doc() -> EditSession:
    session = EditSession()
    for name in ("device_anchored", "device_release"):  # only width and spacing here
        session.process.enable_rule(name, False)
    session.nodes.add(RectShape(name="plate", layer="device", x0=0, y0=0, x1=40, y1=20))
    # A test structure: a beam narrower than the 2 µm minimum, on purpose.
    session.nodes.add(RectShape(name="probe", layer="device", x0=100, y0=0, x1=101, y1=30))
    return session


def violations(doc: EditSession):
    return doc.results.check()


def narrow(doc: EditSession):
    [v] = [v for v in violations(doc) if v.rule == "device_min_width"]
    return v


def test_a_waived_violation_is_listed_but_does_not_fail(doc):
    doc.process.waive(narrow(doc), "test structure, narrow on purpose")
    [v] = violations(doc)
    assert v.waived == "test structure, narrow on purpose"
    assert not v.is_error and rules.errors([v]) == [] and rules.open_violations([v]) == []
    [waiver] = doc.project.components["top"].waivers
    assert waiver.rule == "device_min_width" and waiver.box == v.bbox_um
    doc.undo()
    assert narrow(doc).is_error


def test_a_waiver_lapses_when_the_geometry_changes(doc):
    doc.process.waive(narrow(doc), "on purpose")
    doc.nodes.add(RectShape(name="extra", layer="device", x0=103, y0=0, x1=104, y1=30))
    found = [v for v in violations(doc) if "lapsed" in v.message]
    assert found and all(v.is_error for v in found)


def test_unrelated_changes_keep_a_waiver(doc):
    doc.process.waive(narrow(doc), "on purpose")
    doc.nodes.add(RectShape(name="far", layer="device", x0=500, y0=500, x1=520, y1=520))
    assert [v.waived for v in violations(doc)] == ["on purpose"]


def test_a_waiver_matching_nothing_asks_to_be_removed(doc):
    doc.process.waive(narrow(doc), "on purpose")
    doc.nodes.remove([((0, 1),)])  # the probe
    [v] = violations(doc)
    assert v.stale_waiver and not v.is_error and "matches no violation" in v.message
    doc.process.unwaive(v.rule, v.bbox_um)
    assert violations(doc) == []
    with pytest.raises(ValueError, match="no waiver"):
        doc.process.unwaive(v.rule, v.bbox_um)


def test_waivers_need_a_reason_and_a_place(doc):
    with pytest.raises(ValueError, match="reason"):
        doc.process.waive(narrow(doc), "  ")


def test_waivers_are_saved_with_the_component(doc, tmp_path, capsys):
    doc.process.waive(narrow(doc), "test structure")
    doc.save(tmp_path / "p")
    text = (tmp_path / "p" / "components" / "top.yaml").read_text()
    assert "waivers:\n- rule: device_min_width\n" in text and "reason: test structure" in text
    assert load(tmp_path / "p").components["top"].waivers == doc.project.components["top"].waivers
    assert main(["check", str(tmp_path / "p")]) == 0
    captured = capsys.readouterr()
    assert "waived (test structure): device_min_width" in captured.out
    assert "0 error(s), 0 warning(s), 1 waived" in captured.err


# -- the GUI -----------------------------------------------------------------


@pytest.fixture
def window(qtbot, monkeypatch, doc):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QMessageBox

    from mems_sketch.gui.app import MainWindow

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.document.process.enable_rule("device_anchored", False)
    w.document.process.enable_rule("device_release", False)
    w.document.nodes.add(RectShape(name="probe", layer="device", x0=0, y0=0, x1=1, y1=30))
    return w


def message_items(window):
    return [window.messages.item(i) for i in range(window.messages.count())]


def test_waiving_from_the_messages(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    from mems_sketch.gui.panels import VIOLATION_ROLE

    assert window.problems_button.text() == "1 violation"
    [item] = [i for i in message_items(window) if i.data(VIOLATION_ROLE) is not None]
    menu = window.messages.context_menu(item)
    [action] = menu.actions()
    assert action.text() == "Waive…"
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("probe pad", True))
    action.trigger()
    assert window.problems_button.text() == "No problems"
    [item] = [i for i in message_items(window) if i.data(VIOLATION_ROLE) is not None]
    assert item.text().startswith("Waived:") and item.text().endswith("probe pad")
    [action] = window.messages.context_menu(item).actions()
    assert action.text() == "Remove the waiver"
    action.trigger()
    assert window.problems_button.text() == "1 violation"
