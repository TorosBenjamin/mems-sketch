"""Touchpad navigation (#159) and box selection by touch (#158)."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QNativeGestureEvent, QPointingDevice, QWheelEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from mems_sketch.core.shapes import BooleanShape, CircleShape, GuideShape, RectShape
from mems_sketch.gui.app import MainWindow

NONE = Qt.MouseButton.NoButton


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1000, 700)
    w.show()
    qtbot.waitExposed(w)
    w.canvas.configure(draft_quality=False)
    return w


def wheel(canvas, angle=(0, 0), pixels=(0, 0), phase=Qt.ScrollPhase.NoScrollPhase, ctrl=False):
    where = QPointF(canvas.viewport().rect().center())
    event = QWheelEvent(
        where,
        canvas.viewport().mapToGlobal(where),
        QPoint(*pixels),
        QPoint(*angle),
        NONE,
        Qt.KeyboardModifier.ControlModifier if ctrl else Qt.KeyboardModifier.NoModifier,
        phase,
        False,
    )
    QApplication.sendEvent(canvas.viewport(), event)


def view_of(canvas):
    """(scale, horizontal scroll, vertical scroll)."""
    return (
        abs(canvas.transform().m11()),
        canvas.horizontalScrollBar().value(),
        canvas.verticalScrollBar().value(),
    )


def test_a_mouse_wheel_notch_zooms_one_step(window):
    canvas = window.canvas
    scale = view_of(canvas)[0]
    wheel(canvas, angle=(0, 120))
    assert view_of(canvas)[0] == pytest.approx(scale * canvas.options["zoom_step"])


def test_two_fingers_on_a_touchpad_pan_both_ways_and_do_not_zoom(window):
    canvas = window.canvas
    scale, h, v = view_of(canvas)
    wheel(canvas, angle=(-40, 60), pixels=(-20, 30), phase=Qt.ScrollPhase.ScrollUpdate)
    after = view_of(canvas)
    assert after[0] == pytest.approx(scale)
    assert (after[1], after[2]) == (h + 20, v - 30)


def test_a_touchpad_zooms_in_proportion_when_set_to_zoom(window):
    canvas = window.canvas
    window.settings.set("canvas/scrolling", "zoom")
    scale = view_of(canvas)[0]
    wheel(canvas, angle=(0, 30), pixels=(0, 10), phase=Qt.ScrollPhase.ScrollUpdate)
    assert view_of(canvas)[0] == pytest.approx(scale * canvas.options["zoom_step"] ** 0.25)


def test_ctrl_scrolling_zooms_as_a_windows_pinch_does(window):
    canvas = window.canvas
    scale = view_of(canvas)[0]
    wheel(canvas, angle=(0, -60), pixels=(0, -20), phase=Qt.ScrollPhase.ScrollUpdate, ctrl=True)
    assert view_of(canvas)[0] == pytest.approx(scale / canvas.options["zoom_step"] ** 0.5)


def test_a_mouse_wheel_pans_when_set_to(window):
    canvas = window.canvas
    window.settings.set("canvas/scrolling", "pan")
    scale, _, v = view_of(canvas)
    wheel(canvas, angle=(0, -120))
    after = view_of(canvas)
    assert after[0] == pytest.approx(scale) and after[2] == v + 48


def test_a_pinch_zooms_about_the_fingers(window):
    canvas = window.canvas
    where = QPointF(200, 150)
    under = canvas.mapToScene(where.toPoint())
    scale = view_of(canvas)[0]
    event = QNativeGestureEvent(
        Qt.NativeGestureType.ZoomNativeGesture,
        QPointingDevice.primaryPointingDevice(),
        2,
        where,
        where,
        canvas.viewport().mapToGlobal(where),
        0.5,
        QPointF(),
        0,
    )
    QApplication.sendEvent(canvas.viewport(), event)
    assert view_of(canvas)[0] == pytest.approx(scale * 1.5)
    still = canvas.mapToScene(where.toPoint())
    assert abs(still.x() - under.x()) < 2 / scale and abs(still.y() - under.y()) < 2 / scale


# -- box selection --------------------------------------------------------------


def test_a_pinch_placed_in_the_window_still_zooms_about_the_fingers(window):
    """Wayland (Qt 6.11 on KDE) gives a pinch's position in the window, not in the
    canvas; its position on the screen is right."""
    canvas = window.canvas
    where = QPointF(200, 150)
    under = canvas.mapToScene(where.toPoint())
    scale = view_of(canvas)[0]
    in_window = QPointF(canvas.viewport().mapTo(window, where.toPoint()))
    assert in_window != where
    event = QNativeGestureEvent(
        Qt.NativeGestureType.ZoomNativeGesture,
        QPointingDevice.primaryPointingDevice(),
        2,
        in_window,
        in_window,
        canvas.viewport().mapToGlobal(where),
        0.5,
        QPointF(),
        0,
    )
    QApplication.sendEvent(canvas.viewport(), event)
    assert view_of(canvas)[0] == pytest.approx(scale * 1.5)
    still = canvas.mapToScene(where.toPoint())
    assert abs(still.x() - under.x()) < 2 / scale and abs(still.y() - under.y()) < 2 / scale


@pytest.fixture
def shapes(window):
    nodes = window.document.nodes
    nodes.add(RectShape(name="beam", layer="device", x0=0, y0=0, x1=100, y1=4))
    ring = BooleanShape(
        name="ring",
        op="subtract",
        a=[CircleShape(layer="device", x=200, y=0, radius=40)],
        b=[CircleShape(layer="device", x=200, y=0, radius=30)],
    )
    nodes.add(ring)
    nodes.add(GuideShape(name="axis", x0=-50, y0=-50, x1=-50, y1=50))
    window.refresh()
    return window


def selected(window) -> set[str]:
    shapes = window.document.definition_of(window.view.component).shapes
    return {shapes[p[0][1]].name for p in window.selection}


def test_a_box_selects_what_it_touches_by_default(shapes):
    shapes.select_box(90, -10, 120, 10, additive=False)  # over the beam's end only
    assert selected(shapes) == {"beam"}
    shapes.select_box(-60, -10, -40, 10, additive=False)  # across the guide's line
    assert selected(shapes) == {"axis"}


def test_a_box_in_a_rings_hole_selects_nothing(shapes):
    shapes.select_box(190, -10, 210, 10, additive=False)
    assert selected(shapes) == set()
    shapes.select_box(232, -2, 236, 2, additive=False)  # within the ring's material
    assert selected(shapes) == {"ring"}


def test_inside_selects_only_what_is_entirely_inside(shapes):
    shapes.settings.set("editor/box_selection", "inside")
    shapes.select_box(90, -10, 120, 10, additive=False)
    assert selected(shapes) == set()
    shapes.select_box(-10, -10, 110, 10, additive=False)
    assert selected(shapes) == {"beam"}


def test_by_direction_the_left_way_touches(shapes):
    shapes.settings.set("editor/box_selection", "direction")
    shapes.select_box(90, -10, 120, 10, additive=False)  # to the right: inside
    assert selected(shapes) == set()
    shapes.select_box(120, -10, 90, 10, additive=False)  # to the left: touching
    assert selected(shapes) == {"beam"}
    assert not shapes.box_touches(0, 10) and shapes.box_touches(10, 0)


def test_panning_off_a_shape_with_two_fingers_takes_its_name_away(window, qtbot):
    """The view moves under a still cursor: what is under it changes without a
    mouse move, and the hover follows."""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QMouseEvent

    window.document.nodes.add(RectShape(name="bar", layer="device", x0=0, y0=0, x1=100, y1=20))
    canvas = window.canvas
    canvas.fit()
    where = QPointF(canvas.viewport().rect().center())
    move = QMouseEvent(
        QEvent.Type.MouseMove,
        where,
        canvas.viewport().mapToGlobal(where),
        NONE,
        NONE,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(canvas.viewport(), move)
    qtbot.waitUntil(lambda: canvas.hover_label is not None, timeout=2000)
    assert window._hovered is not None
    wheel(canvas, angle=(0, 400), pixels=(0, 300), phase=Qt.ScrollPhase.ScrollUpdate)
    qtbot.waitUntil(lambda: canvas.hover_label is None, timeout=2000)
    assert window._hovered is None
