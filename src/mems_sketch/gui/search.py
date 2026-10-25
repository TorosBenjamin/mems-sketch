"""Search Everywhere (Shift twice): find a component, a shape, a parameter or a command.

Like JetBrains IDEs: one field, results grouped by what they are, a tab per
group to search only that (Find Action, Ctrl+Shift+A, opens on Actions). All
typed words must match; names that start with the text come first. Up/Down
choose, Enter goes to the result (opens the component, selects the shape,
shows the parameter, runs the command).

The Components tab is for placing them: they are listed by where they are from
(a heading per source, which folds away; a list of sources to show one), each
with its description, and the chosen one is previewed beside the list. Enter
places it (it follows the cursor until a click), Ctrl+Enter opens it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import QElapsedTimer, QEvent, QObject, QSize, Qt, Signal
from PySide6.QtGui import QAction, QFont, QIcon, QKeySequence, QWindow
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QMenuBar,
    QTabBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from mems_sketch.gui import component_picker, icons, theme, widgets
from mems_sketch.gui.component_picker import (
    ENTRY_ROLE,
    HEADING_ROLE,
    ComponentPreview,
    Entry,
    Info,
    RowDelegate,
)

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
    # A component: what the Components tab shows of it, and what places it (None:
    # it cannot be placed here, e.g. the component being edited).
    entry: Entry | None = None
    place: Callable[[], object] | None = None

    def score(self, words: list[str]) -> int | None:
        """How well it matches (lower is better), or None when a word is missing."""
        if self.entry is not None:
            return self.entry.score(words)
        title, haystack = self.title.lower(), f"{self.title} {self.detail}".lower()
        if not all(word in haystack for word in words):
            return None
        if not words:
            return 2
        text = " ".join(words)
        if not any(word in title for word in words):
            return 3  # only its menu matches ("comb" in Operations › Combine): last (#328)
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
            keys=_state(action)
            + (
                action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
                or action.text().partition("\t")[2]  # "Shift Shift", written after a tab
            ),
            enabled=action.isEnabled(),
        )
        for path, action in menu_actions(bar)
    ]


def _state(action: QAction) -> str:
    """An on/off command's state, as JetBrains' Find Action shows it (#328)."""
    group = action.actionGroup()
    if not action.isCheckable() or (group is not None and group.isExclusive()):
        return ""  # a command, or one of several choices (the tools)
    return "ON   " if action.isChecked() else "OFF   "


def _plain(text: str) -> str:
    return text.partition("\t")[0].replace("&", "").replace("…", "").strip()


COMPONENTS = "Components"
ALL_SOURCES = "All sources"
WIDTH = 640
WIDE = 840  # with the preview of the Components tab
PREVIEW_WIDTH = 290


class SearchDialog(QDialog):
    """See the module docstring. ``describe`` gives a component's preview (an
    :class:`~mems_sketch.gui.component_picker.Info`) for the Components tab."""

    def __init__(
        self,
        results: list[Result],
        scope: str = ALL,
        parent=None,
        describe: Callable[[Entry], Info] | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("find-action")
        self.resize(WIDTH, 420)
        self.results = results
        self.describe = describe
        self._described: dict[str, Info] = {}
        self.scopes = QTabBar()
        self.scopes.setObjectName("search-scopes")
        self.scopes.setDrawBase(False)
        self.scopes.setExpanding(False)
        for name in (ALL, *GROUPS):
            self.scopes.addTab(name)
        self.scopes.setCurrentIndex((ALL, *GROUPS).index(scope))
        self.scopes.currentChanged.connect(lambda _index: self._scope_changed())
        self.source = QComboBox()  # the Components tab: one source, or all of them
        self.source.setObjectName("search-source")
        self.source.addItem(ALL_SOURCES)
        entries = [r.entry for r in results if r.entry is not None]
        self.source.addItems(component_picker.sources(entries))
        self.source.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.source.currentIndexChanged.connect(lambda _index: self._filter(self.search.text()))
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.scopes)
        top.addStretch(1)
        top.addWidget(self.source)
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
        self.list.setMouseTracking(True)
        self.list.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.list.header().setStretchLastSection(False)
        self.list.itemActivated.connect(self._run)
        self.list.itemClicked.connect(self._clicked)
        self.list.currentItemChanged.connect(lambda *_: self._preview())
        self._rows = RowDelegate(self.list, descriptions=True)
        self.nothing = widgets.placeholder(self.list, "")
        self.preview = ComponentPreview()
        self.preview.setFixedWidth(PREVIEW_WIDTH)
        self.preview.place.connect(lambda: self._run(self.list.currentItem()))
        self.preview.open.connect(lambda: self._run(self.list.currentItem(), open_it=True))
        self.divider = QFrame()  # between the list and the preview
        self.divider.setObjectName("search-divider")
        self.divider.setFixedWidth(1)
        self.footer_line = QFrame()
        self.footer_line.setObjectName("search-divider")
        self.footer_line.setFixedHeight(1)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(14)
        body.addWidget(self.list, 1)
        body.addWidget(self.divider)
        body.addWidget(self.preview)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)
        layout.addLayout(top)
        layout.addWidget(self.search)
        layout.addLayout(body, 1)
        layout.addWidget(self.footer_line)
        layout.addWidget(self.preview.footer)
        self._scope_changed()

    @property
    def scope(self) -> str:
        return self.scopes.tabText(self.scopes.currentIndex())

    @property
    def placing(self) -> bool:
        """The Components tab: rows by source, a preview, Enter places."""
        return self.scope == COMPONENTS

    def _scope_changed(self) -> None:
        placing = self.placing
        for widget in (
            self.source,
            self.preview,
            self.divider,
            self.footer_line,
            self.preview.footer,
        ):
            widget.setVisible(placing)
        self.list.setItemDelegateForColumn(0, self._rows if placing else None)
        for column in (1, 2):
            self.list.setColumnHidden(column, placing)
        width = WIDE if placing else WIDTH
        if width != self.width():
            center = self.geometry().center().x()
            self.resize(width, 520 if placing else 420)
            if self.isVisible():
                self.move(center - width // 2, self.y())
        self.search.setPlaceholderText(
            "Search components by name, library or description"
            if placing
            else "Search components, shapes, parameters and actions"
        )
        self._filter(self.search.text())

    def _visible(self) -> list[QTreeWidgetItem]:
        """The result rows shown (not the group headings)."""
        items = (self.list.topLevelItem(i) for i in range(self.list.topLevelItemCount()))
        return [item for item in items if item.data(0, RESULT_ROLE) is not None]

    def _filter(self, text: str) -> None:
        words = text.lower().split()
        self.list.clear()
        if self.placing:
            self._fill_components(words)
        else:
            self._fill(words)
        what = text.strip()
        self.nothing.setText(f"Nothing matches “{what}”." if what else "Nothing to find.")
        self.nothing.refresh()
        shown = [item for item in self._visible() if not item.isDisabled()]
        self.list.setCurrentItem(shown[0] if shown else None)
        self._preview()

    def _fill(self, words: list[str]) -> None:
        groups = GROUPS if self.scope == ALL else (self.scope,)
        muted = self.palette().placeholderText()
        bold = self.list.font()
        bold.setWeight(QFont.Weight.DemiBold)
        for group in groups:
            ranked = self._ranked(group, words)
            if not ranked:
                continue
            if self.scope == ALL:
                heading = QTreeWidgetItem([group])
                heading.setFlags(Qt.ItemFlag.NoItemFlags)
                heading.setForeground(0, muted)
                heading.setFont(0, bold)
                if self.list.topLevelItemCount():  # room above every group but the first
                    heading.setSizeHint(
                        0, QSize(0, theme.metric("row_height") + theme.metric("space_m"))
                    )
                    heading.setTextAlignment(0, Qt.AlignmentFlag.AlignBottom)
                self.list.addTopLevelItem(heading)
                ranked = ranked[:PER_GROUP_IN_ALL]
            for result in ranked:
                item = QTreeWidgetItem([result.title, result.detail, result.keys])
                item.setIcon(0, result.icon)
                # Every row as high as the theme's rows, with or without an icon (#316).
                item.setSizeHint(0, QSize(0, theme.metric("row_height")))
                item.setData(0, RESULT_ROLE, result)
                item.setForeground(1, muted)
                item.setForeground(2, muted)
                item.setTextAlignment(
                    2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                item.setDisabled(not result.enabled)
                self.list.addTopLevelItem(item)
        for column in (1, 2):
            self.list.resizeColumnToContents(column)

    def _ranked(self, group: str, words: list[str]) -> list[Result]:
        ranked = [
            (score, index, result)
            for index, result in enumerate(self.results)
            if result.group == group and (score := result.score(words)) is not None
        ]
        ranked.sort(key=lambda entry: entry[:2])
        return [result for _score, _index, result in ranked]

    def _fill_components(self, words: list[str]) -> None:
        """A heading per source (folding it away, unless searching) and its rows."""
        only = self.source.currentText() if self.source.currentIndex() > 0 else None
        found = [r for r in self._ranked(COMPONENTS, words) if r.entry is not None]
        folded = component_picker.folded() if not words else set()
        names = component_picker.sources([r.entry for r in found])
        for source in names:
            if only is not None and source != only:
                continue
            rows = [r for r in found if r.entry.source == source]
            fold = source in folded
            heading = QTreeWidgetItem([source])
            heading.setData(0, HEADING_ROLE, (source, len(rows), fold))
            heading.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.list.addTopLevelItem(heading)
            if fold:
                continue
            for result in rows:
                item = QTreeWidgetItem([result.title])
                item.setData(0, RESULT_ROLE, result)
                item.setData(0, ENTRY_ROLE, result.entry)
                item.setToolTip(0, f"{result.title}\n{result.entry.description}".strip())
                self.list.addTopLevelItem(item)

    def _clicked(self, item: QTreeWidgetItem) -> None:
        heading = item.data(0, HEADING_ROLE)
        if heading is not None:  # a source's heading: fold or unfold it
            source, _count, fold = heading
            component_picker.set_folded(source, not fold)
            self._filter(self.search.text())
            return
        if not self.placing:  # elsewhere a click goes there; here it shows the preview
            self._run(item)

    def _preview(self) -> None:
        if not self.placing:
            return
        item = self.list.currentItem()
        result = item.data(0, RESULT_ROLE) if item is not None else None
        if result is None or result.entry is None or self.describe is None:
            self.preview.show_info(None)
            return
        name = result.entry.name
        if name not in self._described:
            self._described[name] = self.describe(result.entry)
        self.preview.show_info(self._described[name], can_place=result.place is not None)

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
                ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                self._run(self.list.currentItem(), open_it=ctrl)
                return True
        return super().eventFilter(watched, event)

    def _run(self, item: QTreeWidgetItem | None, open_it: bool = False) -> None:
        """Go to the result; on the Components tab place it (``open_it``: open it)."""
        if item is None or item.isDisabled() or item.data(0, RESULT_ROLE) is None:
            return
        result = item.data(0, RESULT_ROLE)
        action = result.run
        if self.placing and not open_it:
            if result.place is None:
                return
            action = result.place
        self.accept()
        action()


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
