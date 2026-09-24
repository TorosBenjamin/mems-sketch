"""Search Everywhere (Shift twice): find a component, a shape, a parameter or a command.

Like JetBrains IDEs: one field, results grouped by what they are, a tab per
group to search only that (Find Action, Ctrl+Shift+A, opens on Actions). All
typed words must match; names that start with the text come first. Up/Down
choose, Enter goes to the result (opens the component, selects the shape,
shows the parameter, runs the command).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import QElapsedTimer, QEvent, QObject, Qt, Signal
from PySide6.QtGui import QAction, QIcon, QKeySequence, QWindow
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHeaderView,
    QLineEdit,
    QMenu,
    QMenuBar,
    QTabBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from mems_sketch.gui import icons

GROUPS = ("Components", "Shapes", "Parameters", "Actions")  # in the order shown
ALL = "All"
PER_GROUP_IN_ALL = 8  # the All tab shows this many of each group; its tab shows every one
RESULT_ROLE = Qt.ItemDataRole.UserRole


@dataclass
class Result:
    """One thing to find: its name, what it is, and what choosing it does."""

    title: str
    group: str
    run: Callable[[], object]
    detail: str = ""
    icon: QIcon = field(default_factory=QIcon)
    keys: str = ""  # a command's shortcut
    enabled: bool = True

    def score(self, words: list[str]) -> int | None:
        """How well it matches (lower is better), or None when a word is missing."""
        title, haystack = self.title.lower(), f"{self.title} {self.detail}".lower()
        if not all(word in haystack for word in words):
            return None
        if not words:
            return 2
        text = " ".join(words)
        return 0 if title.startswith(text) else 1 if text in title else 2


def menu_actions(bar: QMenuBar | QMenu) -> list[tuple[str, QAction]]:
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


def action_results(bar: QMenuBar | QMenu) -> list[Result]:
    return [
        Result(
            _plain(action.text()),
            "Actions",
            action.trigger,
            detail=path,
            icon=action.icon(),
            keys=action.shortcut().toString(QKeySequence.SequenceFormat.NativeText),
            enabled=action.isEnabled(),
        )
        for path, action in menu_actions(bar)
    ]


def _plain(text: str) -> str:
    return text.replace("&", "").replace("…", "").strip()


class SearchDialog(QDialog):
    def __init__(self, results: list[Result], scope: str = ALL, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("find-action")
        self.resize(640, 420)
        self.results = results
        self.scopes = QTabBar()
        self.scopes.setObjectName("search-scopes")
        self.scopes.setDrawBase(False)
        self.scopes.setExpanding(False)
        for name in (ALL, *GROUPS):
            self.scopes.addTab(name)
        self.scopes.setCurrentIndex((ALL, *GROUPS).index(scope))
        self.scopes.currentChanged.connect(lambda _index: self._filter(self.search.text()))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search components, shapes, parameters and actions")
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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self.scopes)
        layout.addWidget(self.search)
        layout.addWidget(self.list)
        self._filter("")

    @property
    def scope(self) -> str:
        return self.scopes.tabText(self.scopes.currentIndex())

    def _visible(self) -> list[QTreeWidgetItem]:
        """The result rows shown (not the group headings)."""
        items = (self.list.topLevelItem(i) for i in range(self.list.topLevelItemCount()))
        return [item for item in items if item.data(0, RESULT_ROLE) is not None]

    def _filter(self, text: str) -> None:
        words = text.lower().split()
        self.list.clear()
        groups = GROUPS if self.scope == ALL else (self.scope,)
        muted = self.palette().placeholderText()
        for group in groups:
            ranked = [
                (score, index, result)
                for index, result in enumerate(self.results)
                if result.group == group and (score := result.score(words)) is not None
            ]
            if not ranked:
                continue
            ranked.sort(key=lambda entry: entry[:2])
            if self.scope == ALL:
                heading = QTreeWidgetItem([group])
                heading.setFlags(Qt.ItemFlag.NoItemFlags)
                heading.setForeground(0, muted)
                self.list.addTopLevelItem(heading)
                ranked = ranked[:PER_GROUP_IN_ALL]
            for _score, _index, result in ranked:
                item = QTreeWidgetItem([result.title, result.detail, result.keys])
                item.setIcon(0, result.icon)
                item.setData(0, RESULT_ROLE, result)
                item.setForeground(1, muted)
                item.setForeground(2, muted)
                item.setDisabled(not result.enabled)
                self.list.addTopLevelItem(item)
        for column in (1, 2):
            self.list.resizeColumnToContents(column)
        shown = [item for item in self._visible() if not item.isDisabled()]
        self.list.setCurrentItem(shown[0] if shown else None)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.search and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                shown = [item for item in self._visible() if not item.isDisabled()]
                if shown:
                    current = self.list.currentItem()
                    index = shown.index(current) if current in shown else -1
                    index += 1 if key == Qt.Key.Key_Down else -1
                    self.list.setCurrentItem(shown[max(0, min(index, len(shown) - 1))])
                return True
            if key == Qt.Key.Key_Tab:  # the next group, as in the IDE
                self.scopes.setCurrentIndex((self.scopes.currentIndex() + 1) % self.scopes.count())
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._run(self.list.currentItem())
                return True
        return super().eventFilter(watched, event)

    def _run(self, item: QTreeWidgetItem | None) -> None:
        if item is None or item.isDisabled() or item.data(0, RESULT_ROLE) is None:
            return
        result = item.data(0, RESULT_ROLE)
        self.accept()
        result.run()


class DoubleShift(QObject):
    """Emits ``pressed`` when Shift is pressed twice quickly (and nothing in between)."""

    pressed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._clock = QElapsedTimer()
        self._armed = False  # Shift was pressed and let go on its own, just now
        self._alone = False  # no other key pressed since Shift went down

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if not isinstance(watched, QWindow):
            return False  # a key reaches its window once, then widget after widget
        kind = event.type()
        if kind == QEvent.Type.KeyPress and not event.isAutoRepeat():
            self._alone = event.key() == Qt.Key.Key_Shift
            if not self._alone:
                self._armed = False
        elif kind == QEvent.Type.KeyRelease and not event.isAutoRepeat():
            if event.key() == Qt.Key.Key_Shift and not self._alone:
                self._armed = False  # Shift was held for typing (Shift+A)
            elif event.key() == Qt.Key.Key_Shift:
                quick = self._clock.isValid() and (
                    self._clock.elapsed() <= QApplication.doubleClickInterval()
                )
                if self._armed and quick:
                    self._armed = False
                    self.pressed.emit()
                else:
                    self._armed = True
                    self._clock.start()
        return False
