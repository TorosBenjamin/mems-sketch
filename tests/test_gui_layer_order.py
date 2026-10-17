"""Layers are drawn bottom to top as the layer stack has them, not by name (#128)."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox

from mems_sketch.core.process import Layer, Level, Process
from mems_sketch.core.shapes import RectShape
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.canvas import ShapeItem


def test_the_stack_order_is_levels_from_the_bottom_with_loose_layers_under_them():
    process = Process(
        layers={
            n: Layer(n, i) for i, n in enumerate(["zz_top", "metal", "aa_base", "loose", "via"])
        },
        levels=[
            Level("aa_base", {"anchor": "via"}),
            Level("zz_top"),
            Level("metal"),
        ],
    )
    assert process.stack_order() == ["loose", "via", "aa_base", "zz_top", "metal"]


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    return w


def z_of(window, layer: str) -> float:
    items = [i for i in window.canvas._layer_items[layer] if isinstance(i, ShapeItem)]
    return items[0].zValue()


def test_an_upper_level_is_drawn_over_a_lower_one_whatever_their_names(window):
    process = window.document.project.process
    process.levels = list(reversed(process.levels))  # metal at the bottom, device on top
    bottom, top = "metal", "device"  # their names sort the other way round
    stack = process.stack_order()
    assert stack.index(bottom) < stack.index(top)
    for layer in (bottom, top):
        window.document.nodes.add(RectShape(layer=layer, x0=0, y0=0, x1=10, y1=10))
    window.refresh()
    assert z_of(window, top) > z_of(window, bottom)
    table = window.layers.layers
    names = [table.item(row, 0).text() for row in range(table.rowCount())]
    assert names == list(reversed(stack))  # the list shows the top one first
