"""The main window's one toolbar row.

☰ (every menu), the project, undo and redo, then one button each to add a
primitive, place a component and apply an operation; on the right, split
view, find action and settings. Everything else is in ☰ or the editor's
right-click menu.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLabel, QMenu, QSizePolicy, QToolBar, QToolButton, QWidget

from mems_sketch.gui import icons
from mems_sketch.gui.canvas import MENU_CARET

if TYPE_CHECKING:
    from mems_sketch.gui.app import MainWindow


def build_toolbar(window: MainWindow) -> QToolBar:
    actions = window.actions_
    bar = QToolBar("Main")
    bar.setObjectName("main-toolbar")
    bar.setMovable(False)
    bar.setFloatable(False)
    bar.setIconSize(QSize(18, 18))
    bar.toggleViewAction().setVisible(False)  # the only toolbar: it cannot be hidden

    menu_button = _menu_button(bar, actions.root, "menu", "Main menu")
    menu_button.setObjectName("main-menu")
    window.project_label = QLabel()
    window.project_label.setObjectName("heading")
    window.project_label.setContentsMargins(6, 0, 10, 0)
    bar.addWidget(window.project_label)
    bar.addSeparator()
    bar.addAction(actions.undo)
    bar.addAction(actions.redo)
    bar.addSeparator()
    _menu_button(bar, actions.add, "rect", "Add a primitive", "Add")
    _menu_button(bar, actions.place, "place", "Place a component", "Place")
    _menu_button(
        bar,
        actions.operations_menu,
        "subtract",
        "Apply an operation to the selection",
        "Operations",
    )

    spacer = QWidget()
    spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    bar.addWidget(spacer)
    for action in _right_side(window):
        if action is None:
            bar.addSeparator()
        elif isinstance(action, QAction):
            bar.addAction(action)
        else:
            bar.addWidget(action)
    return bar


def _right_side(window: MainWindow) -> list:
    actions = window.actions_
    return [actions.split, None, actions.find, actions.settings]


def _menu_button(
    bar: QToolBar, menu: QMenu, icon: str, tip: str, text: str | None = None
) -> QToolButton:
    button = QToolButton()
    icons.bind(button, icon)
    button.setMenu(menu)
    button.setToolTip(tip)
    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    if text:
        button.setText(f"{text} {MENU_CARET}")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    bar.addWidget(button)
    return button
