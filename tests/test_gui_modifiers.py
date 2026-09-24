"""Guides on the canvas and the modifier cards in Properties."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QLabel,
    QLineEdit,
    QMessageBox,
    QToolButton,
)

from mems_sketch import ArrayModifier, GuideShape, MirrorModifier, RectShape
from mems_sketch.gui.app import MainWindow

NONE = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    return w


def click(canvas, x, y):
    pos = QPointF(canvas.mapFromScene(QPointF(x, y)))
    for kind, buttons in (
        (QEvent.Type.MouseButtonPress, LEFT),
        (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton),
    ):
        event = QMouseEvent(kind, pos, canvas.viewport().mapToGlobal(pos), LEFT, buttons, NONE)
        QApplication.sendEvent(canvas.viewport(), event)


def scene(window):
    d = window.document
    d.nodes.add(RectShape(name="mass", layer="device", x0=-10, y0=-10, x1=10, y1=10))
    d.nodes.add(GuideShape(name="axis", x0=30, y0=-20, x1=30, y1=20))
    d.nodes.add(
        RectShape(
            name="bar",
            layer="device",
            x0=40,
            y0=0,
            x1=44,
            y1=4,
            modifiers=[ArrayModifier(rows=2, dy=10), MirrorModifier(about="axis")],
        )
    )
    window.canvas.set_view_state(4, 30, 0)


def cards(window):
    return window.properties.findChildren(QFrame, "modifier-card")


# -- guides ------------------------------------------------------------------------


def test_guides_are_drawn_and_selected_by_clicking_near_them(window):
    scene(window)
    assert [(g[1], g[2], g[3]) for g in window.view.guides] == [("axis", (30, -20), (30, 20))]
    assert window.canvas._guide_items
    click(window.canvas, 30.5, 5)  # two pixels beside the line
    assert window.selection == [((0, 1),)]
    click(window.canvas, 35, 5)  # twenty pixels away: nothing
    assert window.selection == []


def test_the_guide_tool_draws_a_guide(window):
    window.canvas.set_view_state(4, 0, 0)
    assert any(a.text() == "Guide" for a in window.actions_.add.actions())
    window.set_tool("guide")
    click(window.canvas, 0, -10)
    click(window.canvas, 0, 10)
    guide = window.document.shapes[-1]
    assert isinstance(guide, GuideShape) and (guide.x0, guide.y0, guide.y1) == (0, -10, 10)
    assert not window.tool_widgets["layer"][1].isVisible()  # a guide has no layer


# -- modifier cards -------------------------------------------------------------------


def test_each_modifier_has_a_card_with_its_settings(window):
    scene(window)
    window.tree.select_paths([((0, 2),)])
    array, mirror = cards(window)
    assert "1×2" in [label.text() for label in array.findChildren(QLabel)]
    about = mirror.findChild(QComboBox)
    assert about.currentText() == "axis"
    items = [about.itemText(i) for i in range(about.count())]
    assert "axis" in items and "mass.center" in items and "self.center" in items
    axis_field = next(w for w in mirror.findChildren(QComboBox) if w is not about)
    assert not axis_field.isEnabled()  # a guide replaces the axis
    about.setCurrentText("")
    assert axis_field.isEnabled()


def test_card_settings_are_applied_with_the_rest(window):
    scene(window)
    window.tree.select_paths([((0, 2),)])
    rows = cards(window)[0].findChildren(QLineEdit)[1]  # columns, rows, dx, dy
    rows.setText("3")
    window.properties.apply()
    node = window.document.node(((0, 2),))
    assert node.modifiers[0].rows == 3 and node.modifiers[1].about == "axis"


def buttons(card):
    return {b.toolTip().split(" (")[0].split(":")[0]: b for b in card.findChildren(QToolButton)}


def test_card_buttons_act_at_once_and_can_be_undone(window):
    scene(window)
    path = ((0, 2),)
    window.tree.select_paths([path])
    buttons(cards(window)[0])["Switch off"].click()
    assert not window.document.node(path).modifiers[0].enabled
    buttons(cards(window)[1])["Move up"].click()
    assert [m.kind for m in window.document.node(path).modifiers] == ["mirror", "array"]
    assert not buttons(cards(window)[1])["Apply"].isEnabled()  # only the first applies
    buttons(cards(window)[1])["Remove"].click()
    assert [m.kind for m in window.document.node(path).modifiers] == ["mirror"]
    window.document.undo()
    assert len(window.document.node(path).modifiers) == 2


def test_add_modifier_and_apply(window):
    scene(window)
    window.tree.select_paths([((0, 0),)])
    menu = window.properties.add_modifier_menu
    next(a for a in menu.actions() if a.text() == "Mirror").trigger()
    assert [m.kind for m in window.document.node(((0, 0),)).modifiers] == ["mirror"]
    buttons(cards(window)[0])["Apply"].click()
    assert window.document.node(((0, 0),)).kind == "transform"


def test_the_shape_list_shows_the_modifier_stack(window):
    scene(window)
    bar = window.tree.topLevelItem(2)
    assert not bar.icon(window.tree.MODIFIERS).isNull()
    assert bar.toolTip(window.tree.MODIFIERS) == "Modifiers: array 1×2 → mirror about axis"
    assert window.tree.topLevelItem(0).icon(window.tree.MODIFIERS).isNull()
