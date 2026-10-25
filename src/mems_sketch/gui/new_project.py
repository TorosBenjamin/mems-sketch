"""The New Project wizard: what to make, its name and folder, the process and libraries.

Like an IDE's New Project dialog: a project or a library on the left, the
details on the right, and the folder it will be created in under the name.
The project is saved there straight away (see ``EditSession.create``).
"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.core.project import new_project
from mems_sketch.editing.components import library_name
from mems_sketch.gui import icons, theme, widgets
from mems_sketch.storage.project_files import PROJECT_FILE, load_library

KINDS = {  # what the wizard makes: label, icon, what it is
    "project": ("Project", "folder", "A design with a top component, exported as a chip."),
    "library": (
        "Library",
        "library",
        "Components to place in other projects; it has no top component.",
    ),
}
DEFAULT_PROCESS = "Default layers (device, anchor, metal)"
COPY_PROCESS = "Copy from another project…"


class NewProjectDialog(QDialog):
    def __init__(self, location: str, library: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("new-project")
        self.setWindowTitle("New Project")
        self.resize(720, 460)
        self.process_from: str | None = None
        self._library_processes: dict[str, list[str]] = {}  # by library folder

        self.side = widgets.SideList([(icon, label) for label, icon, _about in KINDS.values()])
        self.kinds = self.side.list
        for row, key in enumerate(KINDS):
            self.kinds.item(row).setData(Qt.ItemDataRole.UserRole, key)
        self.kinds.currentRowChanged.connect(self._kind_changed)

        self.about = QLabel()
        self.about.setObjectName("muted")
        self.about.setWordWrap(True)
        self.name = QLineEdit()
        self.name.textEdited.connect(self._name_edited)
        self.name.textChanged.connect(self._validate)
        self.location = QLineEdit(location)
        self.location.textChanged.connect(self._validate)
        browse = QToolButton()
        icons.bind(browse, "folder")
        browse.setToolTip("Choose the folder to create it in")
        browse.clicked.connect(self._browse)
        where = QHBoxLayout()
        where.setSpacing(4)
        where.addWidget(self.location, 1)
        where.addWidget(browse)
        self.target = QLabel()
        self.target.setObjectName("muted")
        self.target.setWordWrap(True)

        self.process = QComboBox()
        self.process.activated.connect(self._process_chosen)

        self.libraries = QListWidget()
        self.libraries.setObjectName("library-list")
        self.libraries.setMinimumHeight(90)
        self.libraries.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.libraries.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.libraries.itemSelectionChanged.connect(self._validate)
        widgets.placeholder(
            self.libraries,
            "No libraries of your own yet. The bundled ones (mems) are always included.",
        )
        add = widgets.action_button(
            "plus",
            "Add a library: a folder of components (a library or another project)",
            lambda: self.add_library(),
        )
        self.remove = widgets.action_button(
            "minus", "Remove the selected library", self._remove_library
        )
        library_buttons = QVBoxLayout()
        library_buttons.setSpacing(theme.metric("space_xs"))
        library_buttons.addWidget(add)
        library_buttons.addWidget(self.remove)
        library_buttons.addStretch()
        library_row = QHBoxLayout()
        library_row.setSpacing(theme.metric("space_s"))
        library_row.addWidget(self.libraries, 1)
        library_row.addLayout(library_buttons)

        form = widgets.form()
        form.addRow("Name", self.name)
        form.addRow("Location", where)
        form.addRow("", self.target)
        form.addRow("Process", self.process)
        libraries_label = QLabel("Libraries")
        form.addRow(libraries_label, library_row)
        form.setAlignment(libraries_label, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.create_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.create_button.setText("Create")
        self.create_button.setDefault(True)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        details = widgets.dialog_margins(QVBoxLayout())
        self.heading = QLabel()
        self.heading.setObjectName("heading")
        details.addWidget(self.heading)
        details.addWidget(self.about)
        details.addSpacing(theme.metric("space_s"))
        details.addLayout(form)
        details.addStretch()
        details.addWidget(self.buttons)
        right = QWidget()
        right.setLayout(details)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.side)
        layout.addWidget(right, 1)

        self._fill_processes()
        self._named_by_user = False
        self.kinds.setCurrentRow(1 if library else 0)
        self.name.setFocus()

    # -- what it makes -------------------------------------------------------

    @property
    def making(self) -> str:
        """``project`` or ``library``."""
        item = self.kinds.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else "project"

    def folder(self) -> Path:
        """Where it will be created: a folder named after it, in the location."""
        return Path(self.location.text().strip()).expanduser() / _folder_name(self.name.text())

    def values(self) -> dict:
        """The arguments for ``EditSession.create``."""
        return {
            "folder": self.folder(),
            "name": self.name.text().strip(),
            "library": self.making == "library",
            "libraries": [self.libraries.item(i).toolTip() for i in range(self.libraries.count())],
            "process_from": self.process_from,
            "process": self.chosen_process(),
        }

    def problem(self) -> str | None:
        """Why it cannot be created yet (None: it can)."""
        name = self.name.text().strip()
        if not name:
            return "Give it a name."
        if not _folder_name(name):
            return "The name needs a letter or digit."
        if not self.location.text().strip():
            return "Choose where to create it."
        folder = self.folder()
        if (folder / PROJECT_FILE).exists():
            return f"{folder} already holds a project."
        if folder.exists() and not folder.is_dir():
            return f"{folder} is a file."
        return None

    # -- reacting ------------------------------------------------------------

    def _kind_changed(self, _row: int) -> None:
        label, _icon, about = KINDS[self.making]
        self.heading.setText(f"New {label.lower()}")
        self.about.setText(about)
        if not self._named_by_user:  # suggest a free name for the kind
            self.name.setText(
                self._free_name("untitled" if self.making == "project" else "library")
            )
        self._validate()

    def _name_edited(self, _text: str) -> None:
        self._named_by_user = True

    def _free_name(self, base: str) -> str:
        location = Path(self.location.text().strip() or ".").expanduser()
        name, n = base, 1
        while (location / name).exists():
            n += 1
            name = f"{base}{n}"
        return name

    def _validate(self) -> None:
        problem = self.problem()
        self.target.setProperty("error", problem is not None)
        self.target.setText(problem or f"Created in {self.folder()}")
        self.target.style().unpolish(self.target)
        self.target.style().polish(self.target)
        self.create_button.setEnabled(problem is None)
        self.remove.setEnabled(bool(self.libraries.selectedItems()))

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Create it in folder", self.location.text().strip()
        )
        if folder:
            self.location.setText(folder)

    # -- the process -----------------------------------------------------------
    # Each entry's data: ("default", None), ("library", "mems.surface"),
    # ("copy", None) to choose a project, or ("from", folder).

    def chosen_process(self) -> str | None:
        """The library's process chosen (``mems.surface``), if one is."""
        kind, value = self.process.currentData() or ("default", None)
        return value if kind == "library" else None

    def _fill_processes(self) -> None:
        """The default layers, every library's process (the bundled ones and those
        of the libraries added), copying another project's, and the copy chosen."""
        current = self.process.currentData()
        self.process.clear()
        self.process.addItem(DEFAULT_PROCESS, ("default", None))
        names = [n for n in new_project("processes").process_names() if "." in n]
        for folder in (self.libraries.item(i).toolTip() for i in range(self.libraries.count())):
            names += self._library_processes.get(folder, [])
        for name in names:
            self.process.addItem(icons.icon("process"), name, ("library", name))
        self.process.addItem(COPY_PROCESS, ("copy", None))
        if self.process_from is not None:
            self.process.addItem(
                icons.icon("folder"),
                f"From {Path(self.process_from).name}",
                ("from", self.process_from),
            )
            self.process.setItemData(
                self.process.count() - 1, self.process_from, Qt.ItemDataRole.ToolTipRole
            )
        found = self.process.findData(current) if current is not None else -1
        self.process.setCurrentIndex(max(found, 0))

    def _process_chosen(self, index: int) -> None:
        kind, _value = self.process.itemData(index)
        if kind != "copy":
            if kind != "from":
                self.process_from = None
                self._fill_processes()
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Copy the process of the project in folder", self.location.text().strip()
        )
        self.set_process_from(folder or None)

    def set_process_from(self, folder: str | None) -> None:
        """Copy the process of the project in ``folder`` (None: the default layers)."""
        self.process_from = folder
        self.process.setCurrentIndex(0)
        self._fill_processes()
        if folder is not None:
            self.process.setCurrentIndex(self.process.count() - 1)

    def add_library(self, folder: str | None = None) -> None:
        if folder is None:
            folder = QFileDialog.getExistingDirectory(
                self, "Add library: choose its folder", self.location.text().strip()
            )
        if not folder:
            return
        name = library_name(folder)
        try:
            processes = list(load_library(name, Path(folder)).processes)
        except (OSError, ValueError):
            processes = []  # creating says what is wrong with it
        self._library_processes[folder] = [f"{name}.{p}" for p in processes]
        item = QListWidgetItem(icons.icon("library"), f"{name}  ·  {folder}")
        item.setToolTip(folder)
        self.libraries.addItem(item)
        self._fill_processes()
        self._validate()

    def _remove_library(self) -> None:
        for item in self.libraries.selectedItems():
            self.libraries.takeItem(self.libraries.row(item))
        self._fill_processes()
        self._validate()


def _folder_name(name: str) -> str:
    """A folder name for a project name: spaces and odd characters become ``_``."""
    return re.sub(r"[^\w.-]+", "_", name.strip()).strip("._")
