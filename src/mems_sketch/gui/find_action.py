"""Find Action (Ctrl+Shift+A): run any menu command by typing part of its name.

Like the command palette of JetBrains IDEs: the list shows every enabled menu
action with its menu and shortcut; typing filters it (all words must match),
Up/Down choose, Enter runs.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QHeaderView,
    QLineEdit,
    QMenu,
    QMenuBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from mems_sketch.gui import icons

ACTION_ROLE = Qt.ItemDataRole.UserRole


def menu_actions(bar: QMenuBar) -> list[tuple[str, QAction]]:
    """Every command in the menus, as ``(menu path, action)``."""
    found: list[tuple[str, QAction]] = []

    def visit(menu: QMenu, path: str) -> None:
        for action in menu.actions():
            if action.isSeparator() or not action.text():
                continue
            if action.menu() is not None:
                visit(action.menu(), f"{path} › {_plain(action.text())}")
            else:
                found.append((path, action))

    for top in bar.actions():
        if top.menu() is not None:
            visit(top.menu(), _plain(top.text()))
    return found


def _plain(text: str) -> str:
    return text.replace("&", "").replace("…", "").strip()


class FindActionDialog(QDialog):
    def __init__(self, actions: list[tuple[str, QAction]], parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("find-action")
        self.resize(560, 380)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Type an action name, e.g. “union” or “fit”")
        self.search.addAction(icons.icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._filter)
        self.search.installEventFilter(self)
        self.list = QTreeWidget()
        self.list.setObjectName("command-list")
        self.list.setColumnCount(3)
        self.list.setHeaderHidden(True)
        self.list.setRootIsDecorated(False)
        self.list.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.list.header().setStretchLastSection(False)
        self.list.itemActivated.connect(self._run)
        self.list.itemClicked.connect(self._run)
        for path, action in actions:
            name = _plain(action.text())
            keys = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
            item = QTreeWidgetItem([name, path, keys])
            item.setIcon(0, action.icon())
            item.setData(0, ACTION_ROLE, action)
            item.setForeground(1, self.palette().placeholderText())
            item.setForeground(2, self.palette().placeholderText())
            item.setDisabled(not action.isEnabled())
            item.setData(1, ACTION_ROLE, f"{name} {path}".lower())
            self.list.addTopLevelItem(item)
        for column in (1, 2):
            self.list.resizeColumnToContents(column)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.search)
        layout.addWidget(self.list)
        self._filter("")

    def _visible(self) -> list[QTreeWidgetItem]:
        items = (self.list.topLevelItem(i) for i in range(self.list.topLevelItemCount()))
        return [item for item in items if not item.isHidden()]

    def _filter(self, text: str) -> None:
        words = text.lower().split()
        for i in range(self.list.topLevelItemCount()):
            item = self.list.topLevelItem(i)
            haystack = item.data(1, ACTION_ROLE)
            item.setHidden(not all(word in haystack for word in words))
        shown = [item for item in self._visible() if not item.isDisabled()]
        self.list.setCurrentItem(shown[0] if shown else None)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.search and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                shown = self._visible()
                if shown:
                    current = self.list.currentItem()
                    index = shown.index(current) if current in shown else -1
                    index += 1 if key == Qt.Key.Key_Down else -1
                    self.list.setCurrentItem(shown[max(0, min(index, len(shown) - 1))])
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._run(self.list.currentItem())
                return True
        return super().eventFilter(watched, event)

    def _run(self, item: QTreeWidgetItem | None) -> None:
        if item is None or item.isDisabled():
            return
        action = item.data(0, ACTION_ROLE)
        self.accept()
        action.trigger()
