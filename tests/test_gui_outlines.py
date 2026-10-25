"""Outlines on the canvas: each shape keeps its edge on a merged layer, selected
shapes are outlined one by one, the selection stays outlined while a value is
dragged, and a deleted shape leaves no hover outline behind."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QGraphicsPathItem, QMessageBox

from mems_sketch.core.shapes import RectShape
from mems_sketch.gui.app import MainWindow


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    return w


def add(window, name, x0, y0, x1, y1):
    window.document.nodes.add(RectShape(name=name, layer="device", x0=x0, y0=y0, x1=x1, y1=y1))


def paths(window, name):
    """The node path of the shape called ``name``."""
    names = [s.name for s in window.document.shapes]
    return sorted(window.document.results.inspection())[names.index(name)]


def width(item):
    """How wide an item's shape is, in µm (not counting its pen)."""
    return round(item.mapToScene(item.path().boundingRect()).boundingRect().width(), 2)


def outlines(canvas):
    return [
        item for key, items in canvas._items_by_key.items() if key[0] == "outline" for item in items
    ]


def test_a_shape_inside_another_on_its_layer_keeps_its_edge(window):
    add(window, "big", 0, 0, 100, 100)
    add(window, "small", 40, 40, 60, 60)
    edges = sorted(width(item) for item in outlines(window.canvas))
    assert edges == pytest.approx([20, 100], abs=0.01)
    for item in outlines(window.canvas):
        assert item.brush().style().name == "NoBrush"  # the fill is drawn once, merged


def test_hiding_a_layer_hides_its_outlines_too(window):
    add(window, "big", 0, 0, 100, 100)
    window.canvas.set_layer_visible("device", False)
    assert not any(item.isVisible() for item in outlines(window.canvas))


def test_touching_selected_shapes_are_outlined_one_by_one(window):
    add(window, "left", 0, 0, 10, 10)
    add(window, "right", 10, 0, 20, 10)  # shares an edge with left
    window.tree.select_paths([paths(window, "left"), paths(window, "right")])
    selected = [
        i
        for i in window.canvas._overlay
        if isinstance(i, QGraphicsPathItem) and i.data(0) != "violation"
    ]
    widths = sorted(width(i) for i in selected)
    assert widths == [10, 10]  # two outlines, not one 20 µm wide


def test_the_selection_stays_outlined_while_a_value_is_dragged(window):
    add(window, "bar", 0, 0, 10, 10)
    path = paths(window, "bar")
    window.tree.select_paths([path])
    wider = window.document.node(path).model_copy(update={"x1": 30})
    window._preview_node(wider, path)
    selected = [
        i
        for i in window.canvas._overlay
        if isinstance(i, QGraphicsPathItem) and i.data(0) != "violation"
    ]
    assert [width(i) for i in selected] == [30]  # the new size


def test_a_deleted_shape_leaves_no_hover_outline(window):
    add(window, "bar", 0, 0, 10, 10)
    path = paths(window, "bar")
    window._hover(5, 5)
    assert window.canvas._hover_item is not None
    window.tree.select_paths([path])
    window.delete()
    assert window.canvas._hover_item is None and window._hovered is None
