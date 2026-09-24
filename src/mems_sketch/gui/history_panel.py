"""The History tool window: the project's commits in git, and what each changed.

The list starts with *Uncommitted changes* (the last commit against the
project being edited, saved or not), then the commits that changed the
project, newest first. Choosing one lists its changes below, grouped by
component, and the canvas shows the material it added (tinted) and removed
(hatched) in the component being edited. Right-click a commit to compare it
with the design as it is now. Nothing here changes the repository.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QMenu,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from mems_sketch.core.component import Geometry
from mems_sketch.core.diff import ADDED, IMPORTS, PROCESS, PROJECT, REMOVED, Change
from mems_sketch.editing import EditSession
from mems_sketch.editing.history import Comparison
from mems_sketch.gui import icons
from mems_sketch.gui.panels import _action_bar, _Panel

KEY_ROLE = Qt.ItemDataRole.UserRole + 30  # a version row: ("uncommitted",) or ("commit", sha)
CHANGE_ROLE = Qt.ItemDataRole.UserRole + 31  # a change row: its Change
UNCOMMITTED = ("uncommitted",)
NOT_IN_GIT = (
    "The project is not in a git repository. Save it in one (or run *git init* in its "
    "folder) to see what each commit changed."
)
GROUP_ICONS = {PROJECT: "settings", PROCESS: "layers", IMPORTS: "import"}
ACTION_ICONS = {ADDED: "add", REMOVED: "remove"}  # anything else: "modified"

Key = tuple[str, ...]


def when(date: datetime, now: datetime | None = None) -> str:
    """A commit's date, short: ``14:05`` today, ``3 Sep`` this year, else ``3 Sep 2025``."""
    now = now or datetime.now(date.tzinfo)
    if date.date() == now.date():
        return date.strftime("%H:%M")
    if date.year == now.year:
        return f"{date.day} {date.strftime('%b')}"
    return f"{date.day} {date.strftime('%b %Y')}"


class HistoryPanel(_Panel):
    """See the module docstring."""

    comparison_changed = Signal()  # another comparison is shown: redraw the canvas
    navigate = Signal(object)  # a Change was clicked: show it

    def __init__(self, document: EditSession) -> None:
        super().__init__()
        self.document = document
        self.comparison: Comparison | None = None
        self.key: Key = UNCOMMITTED
        self._geometry: dict[str, tuple[Geometry, Geometry] | str] = {}  # per component
        self._stale = True  # the commits need reading again when the panel shows
        self.title = QLabel()  # what is being compared
        header = _action_bar(self.title, ("recompile", "Refresh", self.reload))
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setTextFormat(Qt.TextFormat.RichText)
        self.note.setContentsMargins(8, 6, 8, 6)
        self.note.setObjectName("muted")

        self.versions = QTreeWidget()
        self.versions.setHeaderLabels(["Version", "When"])
        self.versions.setRootIsDecorated(False)
        header_view = self.versions.header()
        header_view.setStretchLastSection(False)
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.versions.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.versions.itemSelectionChanged.connect(self._version_chosen)
        self.versions.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.versions.customContextMenuRequested.connect(self._menu)

        self.changes = QTreeWidget()
        self.changes.setHeaderHidden(True)
        self.changes.itemClicked.connect(self._change_clicked)

        self.split = QSplitter(Qt.Orientation.Vertical)
        self.split.addWidget(self.versions)
        self.split.addWidget(self.changes)
        self.split.setSizes([160, 300])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addWidget(self.note)
        layout.addWidget(self.split, 1)
        self.document.file_changed.connect(self._file_changed)

    # -- the versions --------------------------------------------------------

    def reload(self) -> None:
        """Read the commits again (e.g. after committing outside the app)."""
        self._stale = False
        history = self.document.history
        available = history.available
        self.split.setVisible(available)
        self.note.setVisible(not available)
        self.note.setText(_rich(NOT_IN_GIT))
        self.versions.blockSignals(True)
        self.versions.clear()
        if available:
            item = QTreeWidgetItem(["Uncommitted changes", ""])
            item.setData(0, KEY_ROLE, UNCOMMITTED)
            item.setIcon(0, icons.icon("modified"))
            self.versions.addTopLevelItem(item)
            for commit in history.commits():
                item = QTreeWidgetItem([commit.subject, when(commit.date)])
                item.setData(0, KEY_ROLE, ("commit", commit.sha))
                item.setToolTip(
                    0,
                    f"{commit.subject}\n{commit.short} · {commit.author} · "
                    f"{commit.date:%Y-%m-%d %H:%M}",
                )
                self.versions.addTopLevelItem(item)
        self.versions.blockSignals(False)
        if not available:
            self._show(None)
            return
        if self._row(self.key) is None and self.key[0] != "since":
            self.key = UNCOMMITTED
        row = self._row(self.key if self.key[0] != "since" else ("commit", self.key[1]))
        if row is not None:
            self.versions.blockSignals(True)
            self.versions.setCurrentItem(row)
            self.versions.blockSignals(False)
        self.compare()

    def _row(self, key: Key) -> QTreeWidgetItem | None:
        for index in range(self.versions.topLevelItemCount()):
            item = self.versions.topLevelItem(index)
            if item.data(0, KEY_ROLE) == key:
                return item
        return None

    def _version_chosen(self) -> None:
        item = self.versions.currentItem()
        if item is not None:
            self.key = tuple(item.data(0, KEY_ROLE))
            self.compare()

    def _menu(self, pos) -> None:
        item = self.versions.itemAt(pos)
        key = tuple(item.data(0, KEY_ROLE)) if item is not None else None
        if key is None or key[0] != "commit":
            return
        menu = QMenu(self)
        menu.addAction("Show what this commit changed", lambda: self.choose(key))
        menu.addAction("Compare with the design now", lambda: self.choose(("since", key[1])))
        menu.exec(self.versions.viewport().mapToGlobal(pos))

    def choose(self, key: Key) -> None:
        """Show a comparison: ``UNCOMMITTED``, ``("commit", sha)`` (what it changed)
        or ``("since", sha)`` (that commit against the design now)."""
        self.key = key
        row = self._row(key if key[0] != "since" else ("commit", key[1]))
        if row is not None:
            self.versions.blockSignals(True)
            self.versions.setCurrentItem(row)
            self.versions.blockSignals(False)
        self.compare()

    # -- the changes ---------------------------------------------------------

    def compare(self) -> None:
        """Work out the chosen comparison again and list it."""
        history = self.document.history
        comparison = None
        if history.available:
            try:
                if self.key == UNCOMMITTED:
                    comparison = history.uncommitted()
                elif self.key[0] == "since":
                    comparison = history.compare(self.key[1], None)
                else:
                    comparison = history.commit(self.key[1])
            except Exception as exc:  # noqa: BLE001 - e.g. an old version that no longer loads
                self.note.setText(_rich(f"Cannot read that version: {exc}"))
                self.note.show()
        self._show(comparison)

    def _show(self, comparison: Comparison | None) -> None:
        self.comparison = comparison
        self.title.setText(self._describe() if comparison is not None else "")
        self._geometry.clear()
        self.changes.clear()
        if comparison is not None:
            self._list(comparison)
        self.comparison_changed.emit()

    def _describe(self) -> str:
        if self.key == UNCOMMITTED:
            return "Since the last commit"
        if self.key[0] == "since":
            return f"From {self.key[1][:7]} to now"
        return f"Commit {self.key[1][:7]}"

    def _list(self, comparison: Comparison) -> None:
        if not comparison.changes:
            item = QTreeWidgetItem(["No changes"])
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.changes.addTopLevelItem(item)
            return
        groups: dict[str, QTreeWidgetItem] = {}
        bold = QFont(self.changes.font())
        bold.setBold(True)
        for change in comparison.changes:
            group = groups.get(change.group)
            if group is None:
                group = groups[change.group] = QTreeWidgetItem([change.group])
                group.setFont(0, bold)
                group.setIcon(0, icons.icon(GROUP_ICONS.get(change.group, "component")))
                self.changes.addTopLevelItem(group)
                group.setExpanded(True)
            if change.what == "component":
                group.setIcon(0, icons.icon(ACTION_ICONS.get(change.action, "component")))
                group.setText(0, f"{change.group} ({change.action})")
                group.setData(0, CHANGE_ROLE, change)
                continue
            row = QTreeWidgetItem([change.text])
            row.setToolTip(0, change.text)
            row.setData(0, CHANGE_ROLE, change)
            row.setIcon(0, icons.icon(ACTION_ICONS.get(change.action, "modified")))
            group.addChild(row)

    def _change_clicked(self, item: QTreeWidgetItem) -> None:
        change = item.data(0, CHANGE_ROLE)
        if isinstance(change, Change) and change.component is not None:
            self.navigate.emit(change)

    # -- for the canvas ------------------------------------------------------

    def geometry(self, component: str) -> tuple[Geometry, Geometry] | None:
        """The material added and removed in ``component`` by the comparison shown
        (None if there is none, or it cannot be built: the note says why)."""
        if self.comparison is None:
            return None
        if component not in self._geometry:
            try:
                self._geometry[component] = self.document.history.geometry(
                    self.comparison, component
                )
            except ValueError as exc:
                self._geometry[component] = str(exc)
        found = self._geometry[component]
        if isinstance(found, str):
            self.note.setText(_rich(found))
            self.note.show()
            return None
        if self.document.history.available:
            self.note.hide()
        return found

    # -- keeping up ------------------------------------------------------------

    def refresh(self) -> None:
        """After an edit: comparisons with the design now follow it."""
        if not self.isVisible():
            self._stale = True
            return
        if self._stale:
            self.reload()
        elif self.key == UNCOMMITTED or self.key[0] == "since":
            self.compare()

    def _file_changed(self) -> None:
        """Saved, or another project opened: its commits may differ."""
        self._stale = True
        if self.isVisible():
            self.reload()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._stale:
            self.reload()


def _rich(text: str) -> str:
    """``*word*`` in italics, as in the help texts."""
    parts = text.split("*")
    return "".join(f"<i>{p}</i>" if i % 2 else p for i, p in enumerate(parts))
