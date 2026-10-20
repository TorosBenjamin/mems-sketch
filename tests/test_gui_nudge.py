"""Moving the selection with the arrow keys (#202): the shape and its highlight move
together, without the highlight dropping out or the selection glowing again."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox

from mems_sketch.core.shapes import RectShape
from mems_sketch.gui.app import MainWindow


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1000, 700)
    w.show()
    qtbot.waitExposed(w)
    w.document.nodes.add(RectShape(name="beam", layer="device", x0=0, y0=0, x1=40, y1=20))
    w.tree.select_paths([((0, 0),)])
    return w


def test_nudging_keeps_the_highlight_and_does_not_glow(window, monkeypatch):
    shown, pulses = [], []
    show_overlay = window.canvas.show_overlay

    def overlay(highlights, *args, **kwargs):
        shown.append(len(highlights))
        return show_overlay(highlights, *args, **kwargs)

    monkeypatch.setattr(window.canvas, "show_overlay", overlay)
    monkeypatch.setattr(window.canvas, "pulse", lambda *geometries: pulses.append(geometries))
    for _ in range(3):
        window.nudge(1, 0)
    assert window.document.node(((0, 0),)).x0 == pytest.approx(3 * window.canvas.grid_step())
    assert window.selection == [((0, 0),)]
    assert shown and 0 not in shown  # never drawn without the selected shape
    assert pulses == []  # the same selection is not greeted again


def test_picking_in_the_shapes_panel_still_glows(window, monkeypatch):
    window.document.nodes.add(RectShape(name="pad", layer="device", x0=0, y0=30, x1=10, y1=40))
    pulses = []
    monkeypatch.setattr(window.canvas, "pulse", lambda *geometries: pulses.append(geometries))
    window.tree.select_paths([((0, 1),)])
    assert len(pulses) == 1
