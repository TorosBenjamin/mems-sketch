"""Panels fit their room: Properties never cut off on the right, a narrow Shapes row
keeps the name readable, History's header holds just the branch, Points keeps its
room for the list; and a new window starts in Select, not a tool for one job."""

import shutil
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QComboBox, QMessageBox

from mems_sketch.core.shapes import Align, RectShape
from mems_sketch.gui.app import MainWindow
from mems_sketch.gui.points_panel import HINT_HEIGHT_PX
from mems_sketch.gui.properties import Section

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1400, 860)
    w.show()
    qtbot.waitExposed(w)
    return w


@pytest.fixture
def resonator(window, tmp_path):
    for name in ("resonator",):
        shutil.copytree(
            EXAMPLES / name, tmp_path / name, ignore=shutil.ignore_patterns(".mems-sketch")
        )
    window.open_project(str(tmp_path / "resonator"))
    return window


def placed_plate(window):
    """The path of ``mass``, which places ``mems.perforated_plate``."""
    return next(
        path
        for path in sorted(window.document.results.inspection())
        if getattr(window.document.node(path), "component", "") == "mems.perforated_plate"
    )


def test_properties_of_a_long_component_name_fit_the_panel(resonator, qtbot):
    resonator.tool_windows.open("properties")
    resonator.tree.select_paths([placed_plate(resonator)])
    editor = resonator.properties
    qtbot.waitUntil(lambda: editor.widget().width() <= editor.viewport().width())
    assert editor.widget().minimumSizeHint().width() <= editor.viewport().width()
    assert not editor.horizontalScrollBar().isVisible()


def test_help_sits_by_the_label_so_fields_are_as_wide(window):
    doc = window.document
    doc.nodes.add(RectShape(name="bar", layer="device", x0=0, y0=0, x1=100, y1=20))
    doc.nodes.add(
        RectShape(
            name="post",
            layer="device",
            x0=0,
            y0=0,
            x1=10,
            y1=30,
            align=Align(point="bottom", to="bar.top"),
        )
    )
    window.tool_windows.open("properties")
    window.tree.select_paths([sorted(doc.results.inspection())[1]])
    combos = [c for c in window.properties.findChildren(QComboBox) if c.isVisible()]
    point, target = combos[-2:]
    assert point.width() == target.width()
    assert (
        point.mapTo(window, point.rect().topLeft()).x()
        == target.mapTo(window, target.rect().topLeft()).x()
    )


def test_a_long_card_heading_is_shortened_not_widening(resonator, qtbot):
    resonator.tool_windows.open("properties")
    resonator.tree.select_paths([placed_plate(resonator)])
    card = next(
        s for s in resonator.properties.findChildren(Section) if s.title().startswith("Parameters")
    )
    assert card.title() == "Parameters of mems.perforated_plate"  # the whole heading
    assert card.minimumSizeHint().width() < card.fontMetrics().horizontalAdvance(card.title())


def test_a_new_window_starts_in_select_not_a_one_job_tool(window, qtbot):
    window.set_tool("align")
    again = MainWindow()
    qtbot.addWidget(again)
    assert again.tool.name == "select"
    again.set_tool("hand")
    third = MainWindow()
    qtbot.addWidget(third)
    assert third.tool.name == "hand"  # a way of working comes back


def test_shape_rows_give_unused_icon_columns_to_the_name(window):
    tree = window.tree
    doc = window.document
    doc.nodes.add(RectShape(name="bar", layer="device", x0=0, y0=0, x1=100, y1=20))
    assert tree.isColumnHidden(tree.STATUS) and tree.isColumnHidden(tree.MODIFIERS)
    doc.nodes.add(
        RectShape(
            name="post",
            layer="device",
            x0=0,
            y0=0,
            x1=10,
            y1=30,
            align=Align(point="bottom", to="bar.top"),
        )
    )
    assert not tree.isColumnHidden(tree.STATUS)  # an aligned shape: its link shows
    assert tree.isColumnHidden(tree.MODIFIERS)


def test_points_keep_the_room_for_the_list_until_one_is_chosen(window):
    window.tool_windows.open("points")
    panel = window.points
    panel.select(None)
    assert panel.form_host.maximumHeight() == HINT_HEIGHT_PX


def test_a_panel_dragged_narrower_than_it_can_be_closes(window):
    windows = window.tool_windows
    windows.open("properties")
    main = windows.main
    before = main.sizes()
    assert before[2] > 0
    main.setSizes([before[0], before[1] + before[2], 0])  # as a drag past its minimum leaves it
    main.splitterMoved.emit(main.width(), 2)
    assert not windows.is_open("properties")
    windows.open("properties")
    assert main.sizes()[2] > 0  # back at a usable size


def test_the_editor_does_not_close_when_dragged(window):
    main = window.tool_windows.main
    assert not main.isCollapsible(1)
