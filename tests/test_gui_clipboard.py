"""Cut, copy and paste in the editor, through the system clipboard (#136)."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox

from mems_sketch import RectShape
from mems_sketch.gui.app import MainWindow


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    w.document.nodes.add(RectShape(name="beam", layer="device", x0=0, y0=0, x1=10, y1=2))
    w.refresh()
    return w


def names(window) -> list[str]:
    return [s.name for s in window.document.shapes]


def test_copy_then_paste_into_another_component(window):
    window.selection = [((0, 0),)]
    window.actions_.copy.trigger()
    assert "beam" in QApplication.clipboard().text()
    window.document.components.new("pillar")
    window.open_component("pillar")
    assert window.can_paste()
    window.actions_.paste.trigger()
    assert names(window) == ["beam"]
    assert window.selection == [((0, 0),)]
    window.actions_.paste.trigger()
    assert names(window) == ["beam", "beam1"]


def test_cut_removes_and_paste_puts_back(window):
    window.selection = [((0, 0),)]
    window.actions_.cut.trigger()
    assert names(window) == [] and window.selection == []
    window.actions_.paste.trigger()
    assert names(window) == ["beam"]


def test_paste_without_shapes_on_the_clipboard_says_so(window):
    QApplication.clipboard().setText("just text")
    assert not window.can_paste()
    window.actions_.paste.trigger()
    assert names(window) == ["beam"]


def test_the_shapes_tree_offers_the_clipboard(window):
    window.selection = [((0, 0),)]
    menu = window.actions_.shapes_menu()
    texts = [a.text() for a in menu.actions()]
    clipboard = [t for t in texts if t in ("Cut", "Copy", "Paste")]
    assert clipboard == ["Cut", "Copy", "Paste"]
    # ... and what the editor's right-click menu offers for a selection (#256)
    assert {"Combine", "Rename…", "Unpack operation", "Remove alignment"} <= set(texts)
    menu.deleteLater()
