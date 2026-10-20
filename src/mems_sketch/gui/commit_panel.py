"""The Commit tool window: the changes since the last commit, and committing them.

Changes are listed by what holds them, one row each: a component (with its
private ones), the project, its process, its imports. A row unfolds into each
change, and a change to several fields of one thing (a parameter's default,
minimum and maximum) into one line per field. Names are coloured by what
happened to them: added, changed, removed; other files in the project's
folder are listed with them, and files git does not track yet in a group of
their own.

The checkboxes choose what goes into the commit: a component is the unit,
since git commits files and a component is a file of its own. Below the list,
*Amend* (replace the last commit), the message (or the one suggested from the
chosen changes) and **Commit**, which saves the project and commits the files
holding the chosen changes, nothing else.

Right-click a row to roll it back to the last commit (an ordinary edit, which
Undo takes back), to start tracking or ignore an untracked file, or to put a
changed file back. The list follows edits, saving and the app coming back to
the front (git may have been used meanwhile).
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.core.component import Geometry
from mems_sketch.core.diff import ADDED, IMPORTS, PROCESS, PROJECT, REMOVED, Change
from mems_sketch.core.shapes import node_at
from mems_sketch.editing import EditSession
from mems_sketch.editing.history import Comparison, LocalChanges, LocalGroup
from mems_sketch.gui import icons, theme, widgets
from mems_sketch.gui.panels import _Panel
from mems_sketch.storage import git

ROW_ROLE = Qt.ItemDataRole.UserRole + 40  # ("heading", which) / ("group", key) / ("file", path)
CHANGE_ROLE = Qt.ItemDataRole.UserRole + 41  # a change row: its Change
CHANGES, UNTRACKED = "changes", "untracked"  # the two headings
NOT_IN_GIT = "The project is not in a git repository yet: start one to keep its history."
NOT_SAVED = "Save the project in a folder to keep its history in git."
NO_GIT = "Committing needs *git*, which is not installed."
NO_CHANGES = "No changes since the last commit"
GROUP_ICONS = {PROJECT: "settings", PROCESS: "layers", IMPORTS: "import"}
WHAT_ICONS = {
    "parameter": "parameter",
    "point": "point",
    "description": "edit",
    "layer": "layers",
    "constant": "parameter",
    "library": "library",
    "import": "import",
    "guide": "guide",
    "project": "settings",
}
FILE_WORDS = {
    git.ADDED: "added",
    git.MODIFIED: "modified",
    git.DELETED: "deleted",
    git.UNTRACKED: "new file",
}
MESSAGE_LINES = 4  # the message box's height, in lines
DETAIL_MAX_CHARS = 80  # a longer detail is cut off with "…" (the tooltip has it whole)


def action_color(action: str) -> str:
    """The theme colour of a name, by what happened to it."""
    return {ADDED: "vcs_added", REMOVED: "vcs_removed"}.get(action, "vcs_changed")


def file_color(state: str) -> str:
    return {
        git.ADDED: "vcs_added",
        git.DELETED: "vcs_removed",
        git.UNTRACKED: "vcs_untracked",
    }.get(state, "vcs_changed")


class _KeepColor(QStyledItemDelegate):
    """Draws a row's text in its own colour when it is selected too (the style sheet
    would draw every selected row in the text colour)."""

    def paint(self, painter, option, index) -> None:
        brush = index.data(Qt.ItemDataRole.ForegroundRole)
        if brush is None or not option.state & QStyle.StateFlag.State_Selected:
            super().paint(painter, option, index)
            return
        item = QStyleOptionViewItem(option)
        self.initStyleOption(item, index)
        text, item.text = item.text, ""
        widget = option.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, item, painter, widget)
        area: QRect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, item, widget)
        painter.save()
        painter.setFont(item.font)
        painter.setPen(brush.color())
        shown = item.fontMetrics.elidedText(text, Qt.TextElideMode.ElideRight, area.width())
        painter.drawText(area, int(item.displayAlignment), shown)
        painter.restore()


class CommitPanel(_Panel):
    """See the module docstring."""

    comparison_changed = Signal()  # the changes shown changed: redraw the canvas
    navigate = Signal(object)  # a Change was clicked: show it
    committed = Signal()  # a commit was made: the list of commits changed
    compare_requested = Signal(object, object, object)  # key, component, Change: a tab

    def __init__(self, document: EditSession) -> None:
        super().__init__()
        self.document = document
        self.comparison: Comparison | None = None
        self.local = LocalChanges()
        self._files: list[git.FileStatus] | None = None  # read again when None
        self._left_out: set[tuple[str, str]] = set()  # rows unchecked by hand
        self._taken: set[str] = set()  # untracked files checked by hand
        self._geometry: dict[str, tuple[Geometry, Geometry] | str] = {}
        self._stale = True
        self._amended_from = ""  # the message Amend filled in

        self.header_context = widgets.header_context()  # the branch, ahead and behind
        self.actions = widgets.header_actions(
            (
                "split",
                "Show the comparison on the canvas (double-click a row)",
                self.compare_current,
            ),
            ("undo", "Roll back the chosen row to the last commit", self.rollback_current),
            ("collapse", "Fold or unfold all", self.toggle_folded),
        )
        self.header_buttons = list(self.actions)
        self.note = widgets.EmptyState()  # why there is nothing to commit from
        self.note.set_action(
            "Initialize git here",
            self.init_repository,
            "Make the project's folder a git repository",
        )
        self.init_button = self.note.button

        self.tree = make_tree()
        self.tree.setObjectName("commit-tree")
        self.tree.itemChanged.connect(self._checked)
        self.tree.itemClicked.connect(self._clicked)
        self.tree.itemDoubleClicked.connect(lambda item, _column: self._compare(item))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.no_changes = widgets.EmptyState(NO_CHANGES)

        self.amend = QCheckBox("Amend")
        self.amend.setToolTip("Replace the last commit with this one")
        self.amend.toggled.connect(self._amend_toggled)
        self.count = QLabel()  # "2 of 3 chosen"
        self.count.setObjectName("muted")
        self.message = QPlainTextEdit()
        self.message.setObjectName("commit-message")
        lines = self.message.fontMetrics().lineSpacing() * MESSAGE_LINES
        self.message.setFixedHeight(lines + 2 * theme.metric("space_m"))
        self.message.textChanged.connect(self._update_button)
        self.commit_button = QPushButton("Commit")
        self.commit_button.setDefault(True)
        self.commit_button.setToolTip("Save the project and commit the chosen changes")
        self.commit_button.clicked.connect(self.commit)
        self.commit_note = QLabel()  # files staged elsewhere, left out
        self.commit_note.setObjectName("muted")
        self.commit_note.setWordWrap(True)

        rule = QFrame()  # a hairline between the list and the commit box
        rule.setObjectName("commit-rule")
        rule.setFixedHeight(1)
        rule.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.commit_box = QWidget()
        box = QVBoxLayout(self.commit_box)
        space = theme.metric("space_m")
        box.setContentsMargins(space, space, space, space)
        box.setSpacing(theme.metric("space_s"))
        top = QHBoxLayout()
        top.addWidget(self.amend)
        top.addStretch(1)
        top.addWidget(self.count)
        box.addLayout(top)
        box.addWidget(self.message)
        buttons = QHBoxLayout()
        buttons.addWidget(self.commit_button)
        buttons.addStretch(1)
        box.addLayout(buttons)
        box.addWidget(self.commit_note)
        line_host = QWidget()
        line_row = QHBoxLayout(line_host)
        line_row.setContentsMargins(space, space, space, 0)
        line_row.addWidget(rule)

        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.tree, 1)
        body.addWidget(self.no_changes, 1)
        body.addWidget(line_host)
        body.addWidget(self.commit_box)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.note, 1)
        layout.addWidget(self.body, 1)
        self.document.file_changed.connect(self._file_changed)
        QGuiApplication.instance().applicationStateChanged.connect(self._app_state)

    # -- reading the changes -------------------------------------------------------

    def reload(self) -> None:
        """Read everything again: the changes, the other files, the branch."""
        self._files = None
        self._show()

    def _show(self) -> None:
        self._stale = False
        history = self.document.history
        available = history.available
        self.body.setVisible(available)
        self.note.setVisible(not available)
        self.note.set_text(_rich(self._why_not()))
        self.init_button.setVisible(not available and history.can_init())
        self.comparison = None
        self.local = LocalChanges()
        if available:
            try:
                self.comparison = history.uncommitted()
                if self._files is None:
                    self._files = history.other_files()
                self.local = history.local(self.comparison, self._files)
            except Exception as exc:  # noqa: BLE001 - e.g. a last commit that no longer loads
                self.note.set_text(_rich(f"Cannot read the last commit: {exc}"))
                self.note.show()
        self.header_context.setText(self._branch_text() if available else "")
        self._geometry.clear()
        self._fill()
        self.comparison_changed.emit()

    def _branch_text(self) -> str:
        history = self.document.history
        text = history.branch or "detached"
        counts = history.ahead_behind()
        if counts:
            ahead, behind = counts
            text += "".join(f"  {mark}{n}" for mark, n in (("↑", ahead), ("↓", behind)) if n)
        return text

    def _why_not(self) -> str:
        if not git.available():
            return NO_GIT
        return NOT_SAVED if self.document.path is None else NOT_IN_GIT

    # -- the list --------------------------------------------------------------------

    def _fill(self) -> None:
        folded = self._folded()
        tree = self.tree
        tree.blockSignals(True)
        tree.clear()
        local = self.local
        tracked = [f for f in local.files if f.tracked]
        untracked = [f for f in local.files if not f.tracked]
        if local.groups or tracked:
            heading = self._heading(CHANGES, f"Changes · {len(local.groups) + len(tracked)}")
            for group in local.groups:
                self._group(heading, group)
            for status in tracked:
                self._file(heading, status, ("file", status.path) not in self._left_out)
            self._settle(heading)
        if untracked:
            heading = self._heading(UNTRACKED, f"Unversioned files · {len(untracked)}")
            for status in untracked:
                self._file(heading, status, status.path in self._taken)
            self._settle(heading)
        tree.expandAll()
        for index in range(tree.topLevelItemCount()):
            self._refold(tree.topLevelItem(index), folded)
        tree.blockSignals(False)
        fit_columns(tree)
        empty = not local
        tree.setVisible(not empty)
        self.no_changes.setVisible(empty)
        self._update_box()

    def _heading(self, which: str, text: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([text.upper()])
        item.setData(0, ROW_ROLE, ("heading", which))
        font = QFont(self.tree.font())
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 0.85)
        item.setFont(0, font)
        item.setForeground(0, theme.color("muted"))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        self.tree.addTopLevelItem(item)
        item.setFirstColumnSpanned(True)  # (only once it is in the tree)
        return item

    def _group(self, parent: QTreeWidgetItem, group: LocalGroup) -> None:
        item = group_row(parent, group, self.document.project)
        item.setData(0, ROW_ROLE, ("group", group.key))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        left_out = ("group", group.key) in self._left_out
        item.setCheckState(0, Qt.CheckState.Unchecked if left_out else Qt.CheckState.Checked)

    def _file(self, parent: QTreeWidgetItem, status: git.FileStatus, checked: bool) -> None:
        item = QTreeWidgetItem(parent, [status.path, FILE_WORDS[status.state]])
        item.setData(0, ROW_ROLE, ("file", status.path))
        item.setIcon(0, icons.icon("file"))
        item.setForeground(0, theme.color(file_color(status.state)))
        item.setForeground(1, theme.color("muted"))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    @staticmethod
    def _settle(heading: QTreeWidgetItem) -> None:
        """A heading's checkbox from its rows': all, none or some."""
        states = {heading.child(i).checkState(0) for i in range(heading.childCount())}
        if states == {Qt.CheckState.Checked}:
            heading.setCheckState(0, Qt.CheckState.Checked)
        elif states == {Qt.CheckState.Unchecked}:
            heading.setCheckState(0, Qt.CheckState.Unchecked)
        else:
            heading.setCheckState(0, Qt.CheckState.PartiallyChecked)

    def _folded(self) -> set:
        """The rows folded by hand (kept when the list is filled again)."""
        found = set()
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item.childCount() and not item.isExpanded():
                found.add(self._key(item))
            stack += [item.child(i) for i in range(item.childCount())]
        return found

    def _refold(self, item: QTreeWidgetItem, folded: set) -> None:
        if self._key(item) in folded:
            item.setExpanded(False)
        for index in range(item.childCount()):
            self._refold(item.child(index), folded)

    @staticmethod
    def _key(item: QTreeWidgetItem):
        role = item.data(0, ROW_ROLE)
        if role is not None:
            return tuple(role)
        change = item.data(0, CHANGE_ROLE)
        return ("change", change.group, change.what, change.subject) if change else None

    def toggle_folded(self) -> None:
        """Fold everything below the headings, or unfold all if it is folded."""
        rows = [
            self.tree.topLevelItem(i).child(j)
            for i in range(self.tree.topLevelItemCount())
            for j in range(self.tree.topLevelItem(i).childCount())
        ]
        unfolded = any(r.isExpanded() for r in rows if r.childCount())
        if unfolded:
            for row in rows:
                row.setExpanded(False)
        else:
            self.tree.expandAll()

    # -- choosing ----------------------------------------------------------------------

    def _checked(self, item: QTreeWidgetItem, column: int) -> None:
        role = item.data(0, ROW_ROLE)
        if column != 0 or role is None:
            return
        kind = role[0]
        self.tree.blockSignals(True)
        if kind == "heading":
            state = item.checkState(0)
            if state == Qt.CheckState.PartiallyChecked:
                state = Qt.CheckState.Checked
                item.setCheckState(0, state)
            for index in range(item.childCount()):
                self._remember(item.child(index), state)
                item.child(index).setCheckState(0, state)
        else:
            self._remember(item, item.checkState(0))
            self._settle(item.parent())
        self.tree.blockSignals(False)
        self._update_box()

    def _remember(self, item: QTreeWidgetItem, state: Qt.CheckState) -> None:
        kind, name = item.data(0, ROW_ROLE)
        checked = state == Qt.CheckState.Checked
        untracked = item.parent() is not None and item.parent().data(0, ROW_ROLE) == (
            "heading",
            UNTRACKED,
        )
        if untracked:
            (self._taken.add if checked else self._taken.discard)(name)
        else:
            (self._left_out.discard if checked else self._left_out.add)((kind, name))

    def chosen(self) -> tuple[list[str], list[str]]:
        """The groups and the files checked to go into the commit."""
        keys, files = [], []
        for index in range(self.tree.topLevelItemCount()):
            heading = self.tree.topLevelItem(index)
            for row in (heading.child(i) for i in range(heading.childCount())):
                if row.checkState(0) != Qt.CheckState.Checked:
                    continue
                kind, name = row.data(0, ROW_ROLE)
                (keys if kind == "group" else files).append(name)
        return keys, files

    def _update_box(self) -> None:
        keys, files = self.chosen()
        total = len(self.local.groups) + len(self.local.files)
        self.count.setText(f"{len(keys) + len(files)} of {total} chosen" if total else "")
        self.commit_box.setVisible(self.document.history.available)
        chosen = [c for g in self.local.groups if g.key in keys for c in g.changes]
        if self.comparison is not None:
            suggestion = self.document.history.suggested_message(
                Comparison(self.comparison.old, None, chosen)
            )
        else:
            suggestion = ""
        self.message.setPlaceholderText(suggestion or "Commit message")
        elsewhere = self.document.history.staged_elsewhere()
        self.commit_note.setVisible(bool(elsewhere))
        if elsewhere:
            names = ", ".join(elsewhere[:3]) + (" …" if len(elsewhere) > 3 else "")
            self.commit_note.setText(f"Left out: staged outside the project ({names}).")
        self._update_button()

    def _update_button(self) -> None:
        keys, files = self.chosen()
        has_message = bool(
            self.message.toPlainText().strip() or self.message.placeholderText() != "Commit message"
        )
        self.commit_button.setEnabled(bool(keys or files or self.amend.isChecked()) and has_message)

    def _amend_toggled(self, on: bool) -> None:
        """Amending starts from the last commit's message; back off, it goes again."""
        if on and not self.message.toPlainText().strip():
            self._amended_from = self.document.history.last_message()
            self.message.setPlainText(self._amended_from)
        elif not on and self._amended_from and self.message.toPlainText() == self._amended_from:
            self.message.clear()
            self._amended_from = ""
        self._update_button()

    # -- doing --------------------------------------------------------------------------

    def commit(self) -> None:
        """Save the project and commit the chosen changes with the message."""
        message = self.message.toPlainText().strip() or self.message.placeholderText()
        keys, files = self.chosen()
        amend = self.amend.isChecked()
        history = self.document.history
        if self._guard(lambda: history.commit_local(message, keys, files, amend)):
            self.message.clear()
            self._amended_from = ""
            self.amend.setChecked(False)
            self._left_out.clear()
            self._taken.clear()
            self.reload()
            self.committed.emit()

    def init_repository(self) -> None:
        if self._guard(self.document.history.init):
            self.reload()

    def compare_current(self) -> None:
        item = self.tree.currentItem()
        if item is not None:
            self._compare(item)
        else:
            self.compare_requested.emit(("uncommitted",), None, None)

    def _compare(self, item: QTreeWidgetItem) -> None:
        """The comparison tab, at a change (or the component of a group)."""
        change = item.data(0, CHANGE_ROLE)
        role = item.data(0, ROW_ROLE)
        if isinstance(change, Change):
            self.compare_requested.emit(("uncommitted",), change.component, change)
        elif role is not None and role[0] == "group":
            component = role[1] if role[1] not in _GROUP_NAMES else None
            self.compare_requested.emit(("uncommitted",), component, None)

    def rollback_current(self) -> None:
        item = self.tree.currentItem()
        if item is not None:
            self._rollback(item)

    def _rollback(self, item: QTreeWidgetItem) -> None:
        """Back to the last commit: a group by an undoable edit, a file after asking."""
        while item.data(0, ROW_ROLE) is None and item.parent() is not None:
            item = item.parent()  # a change: its group
        kind, name = item.data(0, ROW_ROLE)
        history = self.document.history
        if kind == "group":
            self._guard(lambda: history.rollback(name))
        elif kind == "file":
            status = next((f for f in self.local.files if f.path == name), None)
            if status is None or status.state not in (git.MODIFIED, git.DELETED):
                return
            answer = QMessageBox.question(
                self,
                "Roll back",
                f"Put {name} back as it is in the last commit? This cannot be undone.",
            )
            if answer == QMessageBox.StandardButton.Yes and self._guard(
                lambda: history.discard([name])
            ):
                self.reload()

    def _menu(self, pos) -> None:
        item = clicked = self.tree.itemAt(pos)
        if item is None:
            return
        while item.data(0, ROW_ROLE) is None and item.parent() is not None:
            item = item.parent()
        kind, name = item.data(0, ROW_ROLE)
        history = self.document.history
        menu = QMenu(self)
        if kind == "group":
            title = name if name not in _GROUP_NAMES else _GROUP_NAMES[name].lower()
            menu.addAction(
                icons.icon("split"), "Show the comparison", lambda: self._compare(clicked)
            )
            menu.addAction(icons.icon("undo"), f"Roll back {title}", lambda: self._rollback(item))
        elif kind == "file":
            status = next((f for f in self.local.files if f.path == name), None)
            if status is None:
                return
            if status.state == git.UNTRACKED:
                menu.addAction(
                    icons.icon("plus"),
                    "Add to git",
                    lambda: self._file_action(history.track, [name]),
                )
                menu.addAction("Ignore", lambda: self._file_action(history.ignore, name))
            elif status.state in (git.MODIFIED, git.DELETED):
                menu.addAction(icons.icon("undo"), "Roll back…", lambda: self._rollback(item))
        if menu.isEmpty():
            return
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _file_action(self, action, argument) -> None:
        if self._guard(lambda: action(argument)):
            self.reload()

    def _clicked(self, item: QTreeWidgetItem, column: int) -> None:
        change = item.data(0, CHANGE_ROLE)
        if isinstance(change, Change) and change.component is not None:
            self.navigate.emit(change)

    # -- for the canvas ------------------------------------------------------------------

    def geometry(self, component: str) -> tuple[Geometry, Geometry] | None:
        """The material added and removed in ``component`` since the last commit (None
        if nothing changed, or a version does not build)."""
        if self.comparison is None or not self.comparison.changes:
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

    # -- keeping up -----------------------------------------------------------------------

    def refresh(self) -> None:
        """After an edit: the changes follow it (the files on the disk did not move)."""
        if not self.isVisible():
            self._stale = True
            return
        self._show()

    def _file_changed(self) -> None:
        """Saved, or another project opened: the files and the branch may differ."""
        self._files = None
        self._stale = True
        if self.isVisible():
            self.reload()

    def _app_state(self, state) -> None:
        """Back to the front: git may have been used meanwhile."""
        if state == Qt.ApplicationState.ApplicationActive:
            self._files = None
            if self.isVisible():
                self.reload()
            else:
                self._stale = True

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._stale:
            self.reload()


_GROUP_NAMES = {PROJECT: "Project", PROCESS: "Process", IMPORTS: "Imports"}


def group_row(parent, group: LocalGroup, project) -> QTreeWidgetItem:
    """A group's row (under ``parent``, a row or a tree) and a row for each change in
    it: the name coloured by what happened, then the change; a change to several
    fields unfolds into one line each. ``project`` gives the shapes their icons."""
    name = group.key if group.component else _GROUP_NAMES[group.key]
    rows = [c for c in group.changes if not (c.what == "component" and c.group == group.key)]
    if group.action in (ADDED, REMOVED) and not rows:
        detail = group.action
    else:
        detail = f"{len(rows)} change{'s' if len(rows) != 1 else ''}"
    item = QTreeWidgetItem([name, detail])
    if isinstance(parent, QTreeWidgetItem):
        parent.addChild(item)
    else:
        parent.addTopLevelItem(item)
    item.setIcon(0, icons.icon(GROUP_ICONS.get(group.key, "component")))
    bold = QFont(item.font(0))
    bold.setBold(True)
    item.setFont(0, bold)
    item.setForeground(0, theme.color(action_color(group.action)))
    item.setForeground(1, theme.color("muted"))
    for change in rows:
        _change_row(item, group, change, project)
    return item


def _change_row(parent: QTreeWidgetItem, group: LocalGroup, change: Change, project) -> None:
    details = [_plain(d) for d in change.details]
    if len(details) == 1:
        detail = details[0]
    elif details:
        detail = f"{len(details)} changes"
    else:
        detail = change.action
    item = QTreeWidgetItem(parent, [_label(group, change), _cut(detail)])
    item.setData(0, CHANGE_ROLE, change)
    item.setIcon(0, _change_icon(change, project))
    item.setForeground(0, theme.color(action_color(change.action)))
    item.setForeground(1, theme.color("muted"))
    item.setToolTip(1, change.text)
    if len(details) > 1:
        for line in details:
            child = QTreeWidgetItem(item, ["", _cut(line)])
            child.setData(0, CHANGE_ROLE, change)
            child.setForeground(1, theme.color("muted"))
            child.setToolTip(1, line)


def _label(group: LocalGroup, change: Change) -> str:
    """A change's name in its group's list."""
    if not group.component:
        return f"{change.what} {change.subject}".strip()
    name = "description" if change.what == "description" else change.subject or change.what
    if change.group != group.key:  # in a private component of it
        name = f"{change.group.split('/', 1)[1]} › {name}"
    return name


def _change_icon(change: Change, project):
    if change.what == "shape" and change.action != REMOVED and change.path is not None:
        try:
            shapes = project.components[change.component].shapes
            return icons.icon(node_at(shapes, change.path).icon_name())
        except (KeyError, IndexError, TypeError, AttributeError):
            pass
    if change.what == "shape":
        return icons.icon("shapes")
    if change.what == "component":
        return icons.icon("component")
    return icons.icon(WHAT_ICONS.get(change.what, "modified"))


def make_tree() -> QTreeWidget:
    """A list of changes: names and changes in two columns, as wide as they need
    (the list scrolls sideways), names keeping their colour when selected."""
    tree = QTreeWidget()
    tree.setColumnCount(2)
    tree.setHeaderHidden(True)
    tree.setIndentation(22)  # a change's icon sits under its component's
    tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    tree.header().setStretchLastSection(False)
    tree.setItemDelegate(_KeepColor(tree))
    # (after the expanding: resizing from within it leaves the arrows drawn closed)
    tree.expanded.connect(lambda _index: QTimer.singleShot(0, tree, lambda: fit_columns(tree)))
    return tree


NAME_SLACK_PX = 16  # Qt measures bold names a little short


def fit_columns(tree: QTreeWidget) -> None:
    """Both columns as wide as their widest row (the list scrolls sideways)."""
    for column in (0, 1):
        tree.setColumnWidth(column, tree.sizeHintForColumn(column) + NAME_SLACK_PX)


def _plain(detail: str) -> str:
    """A change's detail as the list shows it: a placement's parameter by its name
    (``fingers 16 → 20``, not ``params.fingers``)."""
    return detail.removeprefix("params.")


def _cut(text: str) -> str:
    return text if len(text) <= DETAIL_MAX_CHARS else text[: DETAIL_MAX_CHARS - 1] + "…"


def _rich(text: str) -> str:
    """``*word*`` in italics, as in the help texts."""
    parts = text.split("*")
    return "".join(f"<i>{p}</i>" if i % 2 else p for i, p in enumerate(parts))
