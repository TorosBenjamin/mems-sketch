"""A searchable list to choose from (#143): what a library, a process or a
component is chosen with, rather than the operating system's file picker.

Rows look as they do in Place and Search Everywhere (an icon, the name, then
muted its description, grouped under where they are from): typing filters them,
every word in the name, description or source, names that start with it first.
Enter or a double-click chooses; *Browse…*, where there is one, goes to the
file picker for anything else.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QVBoxLayout,
)

from mems_sketch.gui import widgets
from mems_sketch.gui.component_picker import (
    ENTRY_ROLE,
    Entry,
    RowDelegate,
    entry_item,
    first_sentence,
    heading_item,
    ranked,
    sources,
)

PICKER_WIDTH = 460
PICKER_HEIGHT = 420


class Picker(QDialog):
    """Choose one of ``entries``; :attr:`chosen` is it (None: cancelled or
    browsed), :attr:`browsed` whether *Browse…* was asked for instead."""

    def __init__(
        self,
        title: str,
        entries: list[Entry],
        placeholder: str = "Search",
        browse: str | None = None,
        empty: str = "Nothing to choose from",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.entries = entries
        self.chosen: Entry | None = None
        self.browsed = False
        self.search = QLineEdit()
        self.search.setPlaceholderText(placeholder)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.fill)
        self.list = QListWidget()
        self.list.setItemDelegate(RowDelegate(self.list))
        self.list.itemActivated.connect(lambda item: self._choose(item))
        self.list.itemDoubleClicked.connect(lambda item: self._choose(item))
        self.empty = QLabel(empty)
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText("Choose")
        buttons.accepted.connect(lambda: self._choose(self.list.currentItem()))
        buttons.rejected.connect(self.reject)
        if browse:
            self.browse = buttons.addButton(browse, QDialogButtonBox.ButtonRole.ActionRole)
            self.browse.clicked.connect(self._browse)
        layout = widgets.dialog_margins(QVBoxLayout(self))
        layout.addWidget(self.search)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.empty)
        layout.addWidget(buttons)
        self.search.installEventFilter(self)
        self.resize(PICKER_WIDTH, PICKER_HEIGHT)
        self.fill("")

    def fill(self, text: str) -> None:
        """The rows for what is typed: grouped by source, or ranked when filtering."""
        self.list.clear()
        found = ranked(self.entries, text) if text.strip() else self.entries
        if text.strip():
            for entry in found:
                self.list.addItem(entry_item(entry, source=True, match=text.strip()))
        else:
            for source in sources(found):
                self.list.addItem(heading_item(source))
                for entry in (e for e in found if e.source == source):
                    self.list.addItem(entry_item(entry))
        self.empty.setVisible(not found)
        self.list.setVisible(bool(found))
        first = next(
            (k for k in range(self.list.count()) if self.list.item(k).data(ENTRY_ROLE)), None
        )
        if first is not None:
            self.list.setCurrentRow(first)
        self.ok.setEnabled(first is not None)

    def visible_names(self) -> list[str]:
        """The names listed now (for tests)."""
        items = (self.list.item(k) for k in range(self.list.count()))
        return [item.data(ENTRY_ROLE).name for item in items if item.data(ENTRY_ROLE)]

    def choose(self, name: str) -> None:
        """Choose the entry called ``name`` (as a click would)."""
        for k in range(self.list.count()):
            entry = self.list.item(k).data(ENTRY_ROLE)
            if entry is not None and entry.name == name:
                self._choose(self.list.item(k))
                return
        raise KeyError(name)

    def _choose(self, item) -> None:
        entry = item.data(ENTRY_ROLE) if item is not None else None
        if entry is None:
            return
        self.chosen = entry
        self.accept()

    def _browse(self) -> None:
        self.browsed = True
        self.accept()

    def eventFilter(self, watched, event) -> bool:
        """Up and down in the search field move through the rows."""
        arrows = (Qt.Key.Key_Down, Qt.Key.Key_Up)
        if watched is self.search and event.type() == event.Type.KeyPress and event.key() in arrows:
            step = 1 if event.key() == Qt.Key.Key_Down else -1
            row = self.list.currentRow() + step
            while 0 <= row < self.list.count():
                if self.list.item(row).data(ENTRY_ROLE):
                    self.list.setCurrentRow(row)
                    break
                row += step
            return True
        return super().eventFilter(watched, event)


# -- what there is to choose ----------------------------------------------------------


def library_candidates(project, folder: Path | None) -> dict[Entry, Path]:
    """Libraries that could be added (#143), each with its folder: the project's own
    ``libraries/`` folder and the folders next to the project that hold one, then
    those the recent projects use. Ones the project has already are left out."""
    from mems_sketch.gui import recent_projects
    from mems_sketch.storage.project_files import is_project_folder, load_library

    have = {lib.path.resolve() for lib in project.libraries.values() if lib.path is not None}
    found: dict[Entry, Path] = {}
    seen: set[Path] = set()

    def offer(path: Path, source: str) -> None:
        path = path.resolve()
        if path in seen or path in have or (folder is not None and path == folder.resolve()):
            return
        seen.add(path)
        if not is_project_folder(path):
            return
        try:
            library = load_library(path.name, path)
        except Exception:  # noqa: BLE001 - one that does not load is not offered
            return
        count = len(library.components)
        what = first_sentence(library.description) or f"{count} component{'s' * (count != 1)}"
        found[Entry(path.name, source, what, "library")] = path

    if folder is not None:
        for parent, source in (
            (folder / "libraries", "In the project"),
            (folder.parent, "Next to it"),
        ):
            if parent.is_dir():
                for child in sorted(parent.iterdir()):
                    if child.is_dir():
                        offer(child, source)
    for recent in recent_projects.load():
        for path in _libraries_of(Path(recent.path)):
            offer(path, f"Used by {recent.name}")
    return found


def _libraries_of(folder: Path) -> list[Path]:
    """The library folders a project's manifest lists (not bundled ones)."""
    from mems_sketch.storage.project_files import ProjectFormatError, _manifest

    try:
        manifest = _manifest(folder)
    except (ProjectFormatError, OSError):
        return []
    paths = []
    for entry in (manifest.get("libraries") or {}).values():
        text = str(entry)
        if text.startswith("bundled"):
            continue
        path = Path(text)
        paths.append(path if path.is_absolute() else folder / path)
    return paths


def process_entries(project) -> list[Entry]:
    """Every process the project could use, with its description (#143)."""
    found = []
    for name in project.process_names():
        library, dot, short = name.partition(".")
        if dot:
            process = project.libraries[library].processes[short]
            source = library
        else:
            process, source = project.processes[name], "This project"
        levels = " · ".join(level.layer for level in process.levels)
        description = (
            first_sentence(process.description) or levels or f"{len(process.layers)} layers"
        )
        found.append(Entry(name, source, description, "process"))
    return found
