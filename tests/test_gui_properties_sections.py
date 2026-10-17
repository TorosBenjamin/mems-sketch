"""Properties in cards (#113): a card per part that folds away and stays folded for
that kind of shape, the kind in words, units in a lighter colour; and an aligned
shape's offset by the cursor while it is dragged (#112)."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QToolButton

from mems_sketch.core.shapes import Align, RectShape
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.properties import Section, kind_title

LEFT = Qt.MouseButton.LeftButton


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    w.tool_windows.open("properties")
    w.document.nodes.add(RectShape(name="bar", layer="device", x0=0, y0=0, x1=100, y1=20))
    w.document.nodes.add(RectShape(name="pad", layer="device", x0=200, y0=0, x1=220, y1=20))
    return w


def paths(window):
    return sorted(window.document.results.inspection())


def sections(window) -> dict[str, Section]:
    return {s.title(): s for s in window.properties.findChildren(Section) if s.isVisible()}


def test_each_part_is_a_card_and_the_kind_is_in_words(window):
    window.tree.select_paths([paths(window)[0]])
    cards = sections(window)
    assert "Geometry" in cards and "Modifiers" in cards
    assert any(t.startswith("Align") for t in cards)
    texts = [label.text() for label in window.properties.findChildren(QLabel)]
    assert "Rectangle" in texts and "rect" not in texts


def test_a_placed_component_says_so_and_opens(window):
    window.add_component("anchor")
    node = window.document.node(window.selection[0])
    assert kind_title(node) == "Placed component"
    assert "Placement" in sections(window)
    opener = next(
        b for b in window.properties.findChildren(QToolButton) if b.text() == "Open anchor"
    )
    opener.click()
    assert window.document.active == "anchor"


def test_a_folded_card_stays_folded_for_that_kind_of_shape(window, qtbot):
    first, second = paths(window)
    window.tree.select_paths([first])
    geometry = sections(window)["Geometry"]
    geometry.set_collapsed(True)
    assert geometry.maximumHeight() < 40
    window.tree.select_paths([second])  # another rectangle: built again, still folded
    qtbot.waitUntil(lambda: sections(window)["Geometry"].collapsed)
    sections(window)["Geometry"].set_collapsed(False)
    window.tree.select_paths([first])
    qtbot.wait(20)
    assert not sections(window)["Geometry"].collapsed


def test_units_in_labels_are_lighter(window):
    window.add_component("anchor")
    labels = [lab for lab in window.properties.findChildren(QLabel) if "<span" in lab.text()]
    assert labels and all("µm" in lab.toolTip() or "(" in lab.toolTip() for lab in labels)


def test_dragging_an_aligned_shape_shows_its_new_offset(window, qtbot):
    doc = window.document
    doc.nodes.add(
        RectShape(
            name="post",
            layer="device",
            x0=0,
            y0=0,
            x1=10,
            y1=30,
            align=Align(point="bottom", to="bar.top", dy=5),
        )
    )
    canvas = window.canvas
    canvas.zoom_to(canvas.content_rect())
    window.tree.select_paths([paths(window)[2]])

    def mouse(kind, x, y, modifiers=Qt.KeyboardModifier.ControlModifier):  # Ctrl: no snapping
        pos = QPointF(canvas.mapFromScene(QPointF(x, y)))
        buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else LEFT
        event = QMouseEvent(
            kind,
            pos,
            canvas.viewport().mapToGlobal(pos),
            LEFT,
            buttons,
            modifiers,
        )
        QApplication.sendEvent(canvas.viewport(), event)

    mouse(QEvent.Type.MouseButtonPress, 50, 40, Qt.KeyboardModifier.NoModifier)
    for y in (42, 46, 50):
        mouse(QEvent.Type.MouseMove, 50, y)

    # Mouse moves are handled at the next frame: the label catches up.
    def offset():
        label = canvas.cursor_label or ""
        if not label.startswith("offset "):
            return None
        return [float(v) for v in label.removeprefix("offset ").removesuffix(" µm").split(", ")]

    qtbot.waitUntil(lambda: offset() is not None and abs(offset()[1] - 15) < 0.3)
    assert offset()[0] == pytest.approx(0, abs=0.3)
    mouse(QEvent.Type.MouseButtonRelease, 50, 50)
    assert doc.node(paths(window)[2]).align.dy == pytest.approx(
        offset()[1] if offset() else 15, abs=0.3
    )


def test_an_operation_is_named_by_what_it_does():
    from mems_sketch.core.shapes import BooleanShape

    parts = [RectShape(name=n, layer="device", x0=0, y0=0, x1=1, y1=1) for n in "ab"]
    cut = BooleanShape.wrap("subtract", "cut", parts)
    assert kind_title(cut) == "Subtract"
    assert kind_title(RectShape(name="b", layer="device", x0=0, y0=0, x1=1, y1=1)) == "Rectangle"
