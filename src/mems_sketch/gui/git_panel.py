"""The Git tool window: the project's commits, and what each changed.

The commits that changed the project are listed newest first; choosing one
lists its changes beside them, grouped by component, and the canvas shows the
material it added (tinted) and removed (hatched) in the component being
edited. Right-click a commit to compare it with the design now, or to restore
the project or the open component as it was (an ordinary, undoable edit).
Committing is the Commit panel's; branches, merging and remotes are left to
git for now.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.core.component import Geometry
from mems_sketch.core.diff import Change
from mems_sketch.editing import EditSession
from mems_sketch.editing.history import Comparison, grouped
from mems_sketch.gui import widgets
from mems_sketch.gui.commit_panel import CHANGE_ROLE, fit_columns, group_row, make_tree
from mems_sketch.gui.panels import _Panel
from mems_sketch.storage import git

KEY_ROLE = Qt.ItemDataRole.UserRole + 30  # a commit row: ("commit", sha)
NOT_IN_GIT = "The project is not in a git repository yet: start one to keep its history."
NOT_SAVED = "Save the project in a folder to keep its history in git."
NO_GIT = "The Git panel needs *git*, which is not installed."
CHOOSE = "Choose a commit to see what it changed"

Key = tuple[str, ...]


def when(date: datetime, now: datetime | None = None) -> str:
    """A commit's date, short: ``14:05`` today, ``3 Sep`` this year, else ``3 Sep 2025``."""
    now = now or datetime.now(date.tzinfo)
    if date.date() == now.date():
        return date.strftime("%H:%M")
    if date.year == now.year:
        return f"{date.day} {date.strftime('%b')}"
    return f"{date.day} {date.strftime('%b %Y')}"


class GitPanel(_Panel):
    """See the module docstring."""

    comparison_changed = Signal()  # another comparison is shown: redraw the canvas
    navigate = Signal(object)  # a Change was clicked: show it

    def __init__(self, document: EditSession) -> None:
        super().__init__()
        self.document = document
        self.comparison: Comparison | None = None
        self.key: Key | None = None  # ("commit", sha) or ("since", sha)
        self._geometry: dict[str, tuple[Geometry, Geometry] | str] = {}  # per component
        self._stale = True  # the commits need reading again when the panel shows
        self.title = self.header_context = widgets.header_context()  # the branch
        self.actions = widgets.header_actions(("recompile", "Refresh", self.reload))
        self.header_buttons = list(self.actions)
        self.note = widgets.EmptyState()  # why there is no history, in place of it
        self.note.set_action(
            "Initialize git here",
            self.init_repository,
            "Make the project's folder a git repository",
        )
        self.init_button = self.note.button

        self.versions = QTreeWidget()
        self.versions.setHeaderLabels(["Commit", "When"])
        self.versions.setRootIsDecorated(False)
        header_view = self.versions.header()
        header_view.setStretchLastSection(False)
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.versions.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.versions.itemSelectionChanged.connect(self._version_chosen)
        self.versions.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.versions.customContextMenuRequested.connect(self._menu)

        self.changes = make_tree()
        self.changes.itemClicked.connect(self._change_clicked)
        self.compared = QLabel()  # "Commit 1a2b3c4", over the changes
        self.compared.setObjectName("muted")
        self.compared.setContentsMargins(8, 6, 8, 2)
        self.no_changes = widgets.EmptyState(CHOOSE)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(self.compared)
        right_layout.addWidget(self.changes, 1)
        right_layout.addWidget(self.no_changes, 1)

        self.split = widgets.splitter(Qt.Orientation.Horizontal)
        self.split.addWidget(self.versions)
        self.split.addWidget(right)
        self.split.setSizes([420, 480])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.note, 1)
        layout.addWidget(self.split, 1)
        self.document.file_changed.connect(self._file_changed)

    # -- the commits ---------------------------------------------------------------

    def reload(self) -> None:
        """Read the commits again (e.g. after committing outside the app)."""
        self._stale = False
        history = self.document.history
        available = history.available
        self.split.setVisible(available)
        self.note.setVisible(not available)
        self.note.set_text(_rich(self._why_not()))
        self.init_button.setVisible(not available and history.can_init())
        self.title.setText(history.branch or "" if available else "")
        self.versions.blockSignals(True)
        self.versions.clear()
        for commit in history.commits() if available else []:
            item = QTreeWidgetItem([commit.subject, when(commit.date)])
            item.setData(0, KEY_ROLE, ("commit", commit.sha))
            item.setToolTip(
                0,
                f"{commit.subject}\n{commit.short} · {commit.author} · {commit.date:%Y-%m-%d %H:%M}",
            )
            self.versions.addTopLevelItem(item)
        self.versions.blockSignals(False)
        if self.key is not None and self._row(("commit", self.key[1])) is None:
            self.key = None  # that commit is gone (amended, another project)
        row = self._row(("commit", self.key[1])) if self.key is not None else None
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

    def _why_not(self) -> str:
        """Why there is no history, in words."""
        if not git.available():
            return NO_GIT
        return NOT_SAVED if self.document.path is None else NOT_IN_GIT

    def _menu(self, pos) -> None:
        item = self.versions.itemAt(pos)
        key = tuple(item.data(0, KEY_ROLE)) if item is not None else None
        if key is None:
            return
        menu = QMenu(self)
        menu.addAction("Show what this commit changed", lambda: self.choose(key))
        menu.addAction("Compare with the design now", lambda: self.choose(("since", key[1])))
        menu.addSeparator()
        menu.addAction("Restore the project as it was", lambda: self.restore(key[1]))
        active = self.document.active
        if active in self.document.project.components:
            menu.addAction(f"Restore {active} as it was", lambda: self.restore(key[1], active))
        menu.exec(self.versions.viewport().mapToGlobal(pos))

    # -- doing -------------------------------------------------------------------------

    def restore(self, sha: str, component: str | None = None) -> None:
        """Bring back the project (or one component) as it was (the Commit panel then
        shows what that changed against the last commit)."""
        self._guard(lambda: self.document.history.restore(sha, component))

    def init_repository(self) -> None:
        if self._guard(self.document.history.init):
            self.reload()

    def choose(self, key: Key) -> None:
        """Show a comparison: ``("commit", sha)`` (what it changed) or ``("since",
        sha)`` (that commit against the design now)."""
        self.key = key
        row = self._row(("commit", key[1]))
        if row is not None:
            self.versions.blockSignals(True)
            self.versions.setCurrentItem(row)
            self.versions.blockSignals(False)
        self.compare()

    # -- the changes ---------------------------------------------------------------------

    def compare(self) -> None:
        """Work out the chosen comparison again and list it."""
        history = self.document.history
        comparison = None
        if history.available and self.key is not None:
            try:
                if self.key[0] == "since":
                    comparison = history.compare(self.key[1], None)
                else:
                    comparison = history.commit(self.key[1])
            except Exception as exc:  # noqa: BLE001 - e.g. an old version that no longer loads
                self.note.set_text(_rich(f"Cannot read that version: {exc}"))
                self.note.show()
        self._show(comparison)

    def _show(self, comparison: Comparison | None) -> None:
        self.comparison = comparison
        self.compared.setText(self._describe() if comparison is not None else "")
        self.compared.setVisible(comparison is not None)
        self._geometry.clear()
        self.changes.clear()
        changes = comparison.changes if comparison is not None else []
        for group in grouped(changes):
            group_row(self.changes, group, self.document.project)
        self.changes.expandAll()
        fit_columns(self.changes)
        self.changes.setVisible(bool(changes))
        self.no_changes.setVisible(not changes)
        self.no_changes.set_text(CHOOSE if comparison is None else "No changes")
        self.comparison_changed.emit()

    def _describe(self) -> str:
        if self.key[0] == "since":
            return f"From {self.key[1][:7]} to now"
        return f"Commit {self.key[1][:7]}"

    def _change_clicked(self, item: QTreeWidgetItem) -> None:
        change = item.data(0, CHANGE_ROLE)
        if isinstance(change, Change) and change.component is not None:
            self.navigate.emit(change)

    # -- for the canvas ------------------------------------------------------------------

    def geometry(self, component: str) -> tuple[Geometry, Geometry] | None:
        """The material added and removed in ``component`` by the comparison shown
        (None if there is none, or a version does not build)."""
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
        return None if isinstance(found, str) else found

    # -- keeping up ------------------------------------------------------------------------

    def refresh(self) -> None:
        """After an edit: a comparison with the design now follows it."""
        if not self.isVisible():
            self._stale = True
            return
        if self._stale:
            self.reload()
        elif self.key is not None and self.key[0] == "since":
            self.compare()

    def _file_changed(self) -> None:
        """Saved, committed, or another project opened: its commits may differ."""
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
