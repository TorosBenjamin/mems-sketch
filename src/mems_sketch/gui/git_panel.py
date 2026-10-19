"""The Git tool window: the commit graph, and the chosen commit.

The commits that changed the project are drawn as a graph, newest first, with
their branch and tag labels: those on the branch checked out, on another
branch, or on all of them. Search, and the author and component filters, narrow
the list. Choosing a commit shows its message and its changes, grouped by
component, in the column on the right, and the canvas shows the material it
added (tinted) and removed (hatched) in the component being edited. From there
(or a commit's right-click menu) compare it with the design now, open the
comparison tab, or restore the project or the open component as it was (an
ordinary, undoable edit). Committing is the Commit panel's; checking out,
merging and remotes are left to git for now.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.core.component import Geometry
from mems_sketch.core.diff import Change
from mems_sketch.editing import EditSession
from mems_sketch.editing.history import Comparison, grouped
from mems_sketch.gui import icons, theme, widgets
from mems_sketch.gui.commit_graph import GRAPH_ROLE, GraphDelegate, layout
from mems_sketch.gui.commit_panel import CHANGE_ROLE, fit_columns, group_row, make_tree
from mems_sketch.gui.panels import _Panel
from mems_sketch.storage import git

KEY_ROLE = Qt.ItemDataRole.UserRole + 30  # a commit row: ("commit", sha)
NOT_IN_GIT = "The project is not in a git repository yet: start one to keep its history."
NOT_SAVED = "Save the project in a folder to keep its history in git."
NO_GIT = "The Git panel needs *git*, which is not installed."
CHOOSE = "Choose a commit to see what it changed"
NO_MATCH = "No commits match"
ALL_BRANCHES = "All branches"
ALL_AUTHORS = "All authors"
ALL_COMPONENTS = "All components"

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
    compare_requested = Signal(object, object, object)  # key, component, Change: a tab

    def __init__(self, document: EditSession) -> None:
        super().__init__()
        self.document = document
        self.comparison: Comparison | None = None
        self.key: Key | None = None  # ("commit", sha) or ("since", sha)
        self.branch: str | None = None  # the branch listed (None: the one checked out)
        self._commits: dict[str, git.Commit] = {}  # by sha, those listed
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

        # the filters, over the graph
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search commits: message, sha")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icons.icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(lambda _text: self._filter())
        self.branch_box = self._filter_box("The branch whose commits are listed")
        self.branch_box.activated.connect(lambda _index: self._branch_chosen())
        self.author_box = self._filter_box("Only the commits of one author")
        self.author_box.activated.connect(lambda _index: self._filter())
        self.component_box = self._filter_box("Only the commits that changed one component")
        self.component_box.activated.connect(lambda _index: self.reload())
        filters = QHBoxLayout()
        filters.setContentsMargins(6, 6, 6, 4)
        filters.setSpacing(4)
        filters.addWidget(self.search, 1)
        for box in (self.branch_box, self.author_box, self.component_box):
            filters.addWidget(box)

        self.versions = QTreeWidget()
        self.versions.setHeaderHidden(True)
        self.versions.setColumnCount(3)  # the graph and subject, the author, the date
        self.versions.setRootIsDecorated(False)
        self.versions.setIndentation(0)
        self.versions.setUniformRowHeights(True)
        self.versions.setMouseTracking(True)
        self.graph = GraphDelegate(self.versions)
        self.versions.setItemDelegateForColumn(0, self.graph)
        header_view = self.versions.header()
        header_view.setStretchLastSection(False)
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.versions.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.versions.itemSelectionChanged.connect(self._version_chosen)
        self.versions.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.versions.customContextMenuRequested.connect(self._menu)
        self.versions.itemDoubleClicked.connect(
            lambda item, _column: self.compare_requested.emit(
                tuple(item.data(0, KEY_ROLE)), None, None
            )
        )
        self.no_match = widgets.EmptyState(NO_MATCH)
        log = QWidget()
        log_layout = QVBoxLayout(log)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(0)
        log_layout.addLayout(filters)
        log_layout.addWidget(self.versions, 1)
        log_layout.addWidget(self.no_match, 1)

        # the chosen commit
        self.subject = self._label()
        self.message = self._label()  # the rest of the message
        self.meta = self._label("muted")  # sha · author · date, and its branches
        self.compared = QLabel()  # "Commit 1a2b3c4", over the changes
        self.compared.setObjectName("muted")
        self.compared.setContentsMargins(0, 6, 0, 2)
        self.changes = make_tree()
        self.changes.itemClicked.connect(self._change_clicked)
        self.changes.itemDoubleClicked.connect(lambda item, _column: self._compare(item))
        self.no_changes = widgets.EmptyState(CHOOSE)
        self.since_button = self._button(
            "Compare with now", "Everything changed since this commit", self._since
        )
        self.open_button = self._button(
            "Open comparison", "The comparison tab for this commit", self._open
        )
        self.restore_button = self._button("Restore", "Bring back the design as it was", None)
        self.restore_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.restore_menu = QMenu(self.restore_button)
        self.restore_menu.aboutToShow.connect(self._fill_restore_menu)
        self.restore_button.setMenu(self.restore_menu)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 4, 0, 6)
        buttons.setSpacing(4)
        for button in (self.since_button, self.open_button, self.restore_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        self.details = QWidget()  # all but no_changes
        details_layout = QVBoxLayout(self.details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(2)
        for label in (self.subject, self.message, self.meta):
            details_layout.addWidget(label)
        details_layout.addLayout(buttons)
        details_layout.addWidget(self.compared)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(10, 8, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(self.details)
        right_layout.addWidget(self.changes, 1)
        right_layout.addWidget(self.no_changes, 1)

        self.split = widgets.splitter(Qt.Orientation.Horizontal)
        self.split.addWidget(log)
        self.split.addWidget(right)
        self.split.setStretchFactor(0, 2)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([640, 340])
        layout_ = QVBoxLayout(self)
        layout_.setContentsMargins(0, 0, 0, 0)
        layout_.setSpacing(0)
        layout_.addWidget(self.note, 1)
        layout_.addWidget(self.split, 1)
        self.document.file_changed.connect(self._file_changed)

    def _filter_box(self, tip: str) -> QComboBox:
        box = QComboBox()
        box.setToolTip(tip)
        box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        return box

    def _label(self, name: str | None = None) -> QLabel:
        label = QLabel()
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if name:
            label.setObjectName(name)
        return label

    def _button(self, text: str, tip: str, slot) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tip)
        if slot is not None:
            button.clicked.connect(slot)
        return button

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
        self.title.setText(self._branch_text() if available else "")
        self._fill_branches(history.branches() if available else [])
        self._fill_components()
        commits = []
        if available:
            commits = history.commits(rev=self.branch, component=self._component())
        self._commits = {c.sha: c for c in commits}
        self._fill_authors(commits)
        self.versions.blockSignals(True)
        self.versions.clear()
        muted = theme.color("muted")
        for commit, row in zip(commits, layout(commits), strict=True):
            item = QTreeWidgetItem([commit.subject, commit.author, when(commit.date)])
            item.setData(0, KEY_ROLE, ("commit", commit.sha))
            item.setData(0, GRAPH_ROLE, (row, commit))
            item.setForeground(1, muted)
            item.setForeground(2, muted)
            item.setToolTip(
                0,
                f"{commit.subject}\n{commit.short} · {commit.author} · {commit.date:%Y-%m-%d %H:%M}",
            )
            self.versions.addTopLevelItem(item)
        self.versions.blockSignals(False)
        if self.key is not None and self.key[1] not in self._commits:
            self.key = None  # that commit is gone (amended, another branch or project)
        self._select_row()
        self._filter()
        self.compare()

    def _branch_text(self) -> str:
        history = self.document.history
        text = history.branch or "detached"
        counts = history.ahead_behind()
        if counts:
            ahead, behind = counts
            text += "".join(f"  {mark}{n}" for mark, n in (("↑", ahead), ("↓", behind)) if n)
        return text

    def _fill_branches(self, refs: list[git.Ref]) -> None:
        """The branch filter: the one checked out (first), the others, the remote
        ones, then all. A branch that is gone falls back to the one checked out."""
        names = [r.name for r in refs]
        if self.branch is not None and self.branch != git.ALL and self.branch not in names:
            self.branch = None
        box = self.branch_box
        box.clear()
        current = next((r.name for r in refs if r.role == git.HEAD), None)
        current = current or self.document.history.branch  # (no commit on it yet)
        box.addItem(icons.icon("branch"), current or "HEAD (detached)", None)
        for ref in refs:
            if ref.role != git.HEAD:
                box.addItem(icons.icon("branch"), ref.name, ref.name)
        box.addItem(ALL_BRANCHES, git.ALL)
        index = box.findData(self.branch) if self.branch is not None else 0
        box.setCurrentIndex(max(index, 0))

    def _branch_chosen(self) -> None:
        chosen = self.branch_box.currentData()
        self.branch = chosen
        self.reload()

    def _fill_authors(self, commits: list[git.Commit]) -> None:
        chosen = self.author_box.currentData()
        box = self.author_box
        box.clear()
        box.addItem(ALL_AUTHORS, None)
        for author in sorted({c.author for c in commits}, key=str.casefold):
            box.addItem(author, author)
        box.setCurrentIndex(max(box.findData(chosen), 0) if chosen is not None else 0)

    def _fill_components(self) -> None:
        chosen = self._component()
        box = self.component_box
        box.clear()
        box.addItem(ALL_COMPONENTS, None)
        for name in self.document.project.components:
            if "/" not in name:  # (a private one goes with its owner)
                box.addItem(icons.icon("component"), name, name)
        box.setCurrentIndex(max(box.findData(chosen), 0) if chosen is not None else 0)

    def _component(self) -> str | None:
        return self.component_box.currentData()

    def _filter(self) -> None:
        """Hide the commits search and the author filter leave out; the graph is
        drawn without lines meanwhile (they would join the wrong commits)."""
        words = self.search.text().strip().casefold().split()
        author = self.author_box.currentData()
        shown = 0
        for index in range(self.versions.topLevelItemCount()):
            item = self.versions.topLevelItem(index)
            commit = self._commits[item.data(0, KEY_ROLE)[1]]
            text = f"{commit.subject}\n{commit.body}".casefold()
            hidden = (author is not None and commit.author != author) or not all(
                word in text or commit.sha.startswith(word) for word in words
            )
            item.setHidden(hidden)
            shown += not hidden
        self.graph.flat = bool(words) or author is not None
        self.versions.viewport().update()
        filtered = self.graph.flat or self._component() is not None or self.branch is not None
        self.no_match.set_text(NO_MATCH if filtered else "No commits yet")
        self.versions.setVisible(shown > 0)
        self.no_match.setVisible(shown == 0)

    def _row(self, key: Key) -> QTreeWidgetItem | None:
        for index in range(self.versions.topLevelItemCount()):
            item = self.versions.topLevelItem(index)
            if item.data(0, KEY_ROLE) == key:
                return item
        return None

    def _select_row(self) -> None:
        row = self._row(("commit", self.key[1])) if self.key is not None else None
        if row is not None:
            self.versions.blockSignals(True)
            self.versions.setCurrentItem(row)
            self.versions.blockSignals(False)

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
        menu.addAction(
            icons.icon("split"),
            "Open the comparison",
            lambda: self.compare_requested.emit(key, None, None),
        )
        menu.addAction("Compare with the design now", lambda: self.choose(("since", key[1])))
        menu.addSeparator()
        self._restore_actions(menu, key[1])
        menu.exec(self.versions.viewport().mapToGlobal(pos))

    def _restore_actions(self, menu: QMenu, sha: str) -> None:
        menu.addAction("Restore the project as it was", lambda: self.restore(sha))
        active = self.document.active
        if active in self.document.project.components:
            menu.addAction(f"Restore {active} as it was", lambda: self.restore(sha, active))

    def _fill_restore_menu(self) -> None:
        self.restore_menu.clear()
        if self.key is not None:
            self._restore_actions(self.restore_menu, self.key[1])

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
        self._select_row()
        self.compare()

    def _since(self) -> None:
        if self.key is not None:
            self.choose(("since", self.key[1]))

    def _open(self) -> None:
        if self.key is not None:
            self.compare_requested.emit(self.key, None, None)

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
        self._describe_commit()
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
        self.no_changes.set_text(CHOOSE if self.key is None else "No changes")
        self.comparison_changed.emit()

    def _describe_commit(self) -> None:
        """The chosen commit's message, sha, author, date and labels."""
        commit = self._commits.get(self.key[1]) if self.key is not None else None
        self.details.setVisible(self.key is not None)
        if commit is None:
            self.subject.setText(self.key[1][:7] if self.key is not None else "")
            self.message.hide()
            self.meta.hide()
            return
        self.subject.setText(f"<b>{_escape(commit.subject)}</b>")
        self.message.setText(_escape(commit.body).replace("\n", "<br>"))
        self.message.setVisible(bool(commit.body))
        meta = f"{commit.short} · {_escape(commit.author)} · {commit.date:%d %b %Y %H:%M}"
        if commit.refs:
            meta += "<br>" + _escape(", ".join(ref.name for ref in commit.refs))
        self.meta.setText(meta)
        self.meta.show()

    def _describe(self) -> str:
        if self.key[0] == "since":
            return f"From {self.key[1][:7]} to now"
        return f"Commit {self.key[1][:7]}"

    def _compare(self, item: QTreeWidgetItem) -> None:
        """The comparison tab for the comparison shown, at a change."""
        change = item.data(0, CHANGE_ROLE)
        if self.key is not None and isinstance(change, Change):
            self.compare_requested.emit(self.key, change.component, change)

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


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _rich(text: str) -> str:
    """``*word*`` in italics, as in the help texts."""
    parts = text.split("*")
    return "".join(f"<i>{p}</i>" if i % 2 else p for i, p in enumerate(parts))
