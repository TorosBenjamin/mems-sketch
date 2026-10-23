"""Feedback and motion: notes saying what happened, the canvas gliding to a place,
a shape picked in a panel glowing, and Reduce motion turning it all off."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QRectF
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QMessageBox

from mems_sketch.core.shapes import RectShape
from mems_sketch.gui import motion
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.toast import MOST


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    w.document.nodes.add(RectShape(name="near", layer="device", x0=0, y0=0, x1=20, y1=20))
    w.document.nodes.add(RectShape(name="far", layer="device", x0=900, y0=0, x1=920, y1=20))
    return w


@pytest.fixture
def moving(monkeypatch):
    """Animations run, as for the user (the tests otherwise jump to their ends)."""
    monkeypatch.setattr(motion, "_held_off", False)
    motion.set_enabled(True)
    yield
    motion.set_enabled(True)


def path_of(window, name):
    """The shape ``near`` (at the origin) or ``far`` (at x = 900 µm)."""
    for path, _geometry in window.view.node_regions:
        x0 = min(x for x, _y in window.document.results.node_box(path))
        if (x0 >= 900) == (name == "far"):
            return path
    raise LookupError(name)


def test_a_note_says_what_happened_and_the_same_note_is_not_stacked(window):
    window.notify("Exported a.gds", "success")
    window.notify("Exported a.gds", "success")
    assert window.toasts.texts() == ["Exported a.gds"]
    for n in range(MOST + 2):
        window.notify(f"note {n}")
    assert len(window.toasts.texts()) == MOST
    assert window.toasts.texts()[-1] == f"note {MOST + 1}"  # the newest stay
    window.toasts.clear()
    assert window.toasts.texts() == [] and not window.toasts.isVisible()


def test_an_error_stays_longer_and_leads_to_the_messages(window):
    """#226: errors go by themselves too (they piled up), after longer."""
    from mems_sketch.gui.toast import SECONDS

    QCursor.setPos(0, 0)  # pointing at a note keeps it
    window.tool_windows.close("messages")
    window.report_error("width must be positive")
    (toast,) = window.toasts.toasts()
    assert toast.kind == "error" and toast._timer.isActive()
    assert toast._timer.interval() == SECONDS["error"] * 1000 > SECONDS["warning"] * 1000
    toast.button.click()
    assert window.tool_windows.is_open("messages")
    assert window.toasts.texts() == []


def test_a_success_note_goes_by_itself(window, qtbot):
    QCursor.setPos(0, 0)  # pointing at a note keeps it
    toast = window.notify("Aligned A to B", "success", seconds=0.05)
    assert toast._timer.isActive()
    qtbot.waitUntil(lambda: window.toasts.texts() == [], timeout=2000)


def test_undo_and_redo_say_what_they_did(window):
    window.undo()
    assert window.toasts.texts()[-1].startswith("Undid")
    window.redo()
    assert window.toasts.texts() == [window.toasts.texts()[-1]]  # one note, replaced
    assert window.toasts.texts()[-1].startswith("Redid")


def test_fit_glides_and_ends_where_a_jump_would(window, qtbot, moving):
    canvas = window.canvas
    canvas.zoom_by(8)
    canvas.fit()
    expected = canvas.view_state()
    canvas.zoom_by(8)
    canvas.fit(animated=True)
    assert motion.running(canvas, "view")
    qtbot.waitUntil(lambda: not motion.running(canvas, "view"), timeout=3000)
    assert canvas.view_state() == pytest.approx(expected, rel=1e-6)


def test_zoom_clicks_during_a_glide_add_up(window, qtbot, moving):
    canvas = window.canvas
    start = canvas.pixels_per_um()
    canvas.zoom_by(1.25, animated=True)
    canvas.zoom_by(1.25, animated=True)
    qtbot.waitUntil(lambda: not motion.running(canvas, "view"), timeout=3000)
    assert canvas.pixels_per_um() == pytest.approx(start * 1.25 * 1.25, rel=1e-6)


def test_the_wheel_takes_over_from_a_glide(window, moving):
    canvas = window.canvas
    canvas.fit(animated=True)
    canvas.stop_gliding()
    assert not motion.running(canvas, "view")


def test_a_shape_picked_in_a_panel_glows_and_comes_into_view(window, qtbot, moving):
    canvas = window.canvas
    canvas.zoom_to(QRectF(-10, -10, 40, 40))
    window.tree.select_paths([path_of(window, "far")])
    assert canvas._pulse_item is not None
    qtbot.waitUntil(lambda: not motion.running(canvas, "view"), timeout=3000)
    shown = canvas.mapToScene(canvas.viewport().rect()).boundingRect()
    assert shown.contains(QRectF(900, 0, 20, 20))
    qtbot.waitUntil(lambda: canvas._pulse_item is None, timeout=3000)  # it fades away


def test_a_shape_clicked_on_the_canvas_does_not_glow(window, moving):
    window._select_quietly([path_of(window, "near")])
    assert window.selection and window.canvas._pulse_item is None


def test_reduce_motion_jumps(window, moving):
    window.settings.set("appearance/reduce_motion", True)
    canvas = window.canvas
    canvas.fit(animated=True)
    assert not motion.running(canvas, "view")
    window.tree.select_paths([path_of(window, "far")])
    assert canvas._pulse_item is None
    window.settings.set("appearance/reduce_motion", False)
    assert motion.enabled()


def test_a_rules_details_unfold(window, qtbot, moving):
    window.open_process()
    rules = window.area.process_view.rules
    rules.open_details("device_anchored")
    row = rules._rows.index(None)
    qtbot.waitUntil(lambda: not motion.running(rules, "unfold"), timeout=2000)
    assert rules.rules.rowHeight(row) == rules.details.sizeHint().height()


def test_a_long_note_takes_two_lines_and_keeps_all_of_it(window, qtbot):
    """#226: a long message is cut to two lines with "…"; its tooltip, the
    toast's text and Messages have all of it."""
    QCursor.setPos(0, 0)
    long = "rounded corners cannot be applied into shapes; keep the modifier " * 4
    toast = window.notify(long.strip(), "warning")
    qtbot.waitUntil(lambda: toast.label.text() != long.strip(), timeout=2000)
    label = toast.label
    assert label.text().endswith("…") and toast.text() == long.strip()
    assert label.toolTip() == long.strip()
    assert label.heightForWidth(label.width()) <= 2 * label.fontMetrics().lineSpacing() + 4
