"""Alignment, set up on the canvas: an aligned shape shows its point and target,
and either end can be picked on the canvas from Properties (#112)."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QMessageBox, QToolButton

from mems_sketch.core.shapes import Align, RectShape
from mems_sketch.gui.app import MainWindow

NONE = Qt.KeyboardModifier.NoModifier


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    doc = w.document
    doc.nodes.add(RectShape(name="base", layer="device", x0=0, y0=0, x1=100, y1=20))
    doc.nodes.add(
        RectShape(
            name="post",
            layer="device",
            x0=0,
            y0=0,
            x1=10,
            y1=30,
            align=Align(point="bottom", to="base.top", dy=5),
        )
    )
    w.canvas.zoom_to(w.canvas.content_rect())
    return w


def path_of(window, name):
    names = [s.name for s in window.document.shapes]
    return sorted(window.document.results.inspection())[names.index(name)]


def target_at(window, path, name):
    return next((x, y) for n, _, x, y in window.document.results.align_targets(path) if n == name)


def post_align(window):
    return window.document.node(path_of(window, "post")).align


def test_an_aligned_shape_shows_its_point_and_target(window):
    window.tree.select_paths([path_of(window, "post")])
    point, target = window.canvas._alignment
    assert point[0] == "bottom" and target[0] == "base.top"
    assert (target[1], target[2]) == pytest.approx((50, 20))
    assert (point[1], point[2]) == pytest.approx((50, 25))  # the offset sets them apart
    window.tree.select_paths([path_of(window, "base")])  # not aligned: nothing shown
    assert window.canvas._alignment is None


def test_the_target_is_picked_on_the_canvas_keeping_the_point_and_offset(window):
    post = path_of(window, "post")
    window.tree.select_paths([post])
    window.properties.pick_alignment.emit("to", "bottom", "base.top")
    tool = window.tool
    assert tool.name == "align" and tool.step == "target"
    x, y = target_at(window, post, "base.left")
    tool.move(x, y, NONE, False)
    assert window.canvas.cursor_label == "base.left"  # what a click would pick
    tool.press(x, y, NONE)
    align = post_align(window)
    assert (align.point, align.to, align.dy) == ("bottom", "base.left", 5)
    assert window.tool.name == "select"  # back where it was


def test_the_point_is_picked_on_the_canvas_keeping_the_target(window):
    post = path_of(window, "post")
    window.tree.select_paths([post])
    window.properties.pick_alignment.emit("point", "bottom", "base.top")
    tool = window.tool
    assert tool.step == "own"
    x, y = next((x, y) for n, x, y in window.document.results.node_points(post) if n == "left")
    tool.press(x, y, NONE)
    align = post_align(window)
    assert (align.point, align.to, align.dy) == ("left", "base.top", 5)


def test_esc_while_picking_changes_nothing_and_goes_back_to_select(window):
    window.tree.select_paths([path_of(window, "post")])
    window.properties.pick_alignment.emit("to", "bottom", "base.top")
    window.tool.cancel()
    assert window.tool.name == "select"
    assert post_align(window).to == "base.top"


def test_properties_has_a_pick_button_by_each_end_and_groups_targets_by_shape(window):
    window.document.nodes.add(RectShape(name="pad", layer="device", x0=200, y0=0, x1=220, y1=20))
    window.tree.select_paths([path_of(window, "post")])
    editor = window.properties
    picks = [
        b for b in editor.findChildren(QToolButton) if b.toolTip().startswith("Pick the point")
    ]
    assert len(picks) == 2
    targets = next(
        c for c in editor.findChildren(QComboBox) if c.findText("base.top") >= 0 and c.count() > 5
    )
    texts = [targets.itemText(i) for i in range(targets.count())]
    first_pad = next(i for i, t in enumerate(texts) if t.startswith("pad."))
    assert texts[first_pad - 1] == ""  # a separator between base's points and pad's
