"""The New Project wizard: what to make, its name and folder, the process and libraries.

Like an IDE's New Project dialog: a project or a library on the left, the
details on the right, and the folder it will be created in under the name.
The project is saved there straight away (see ``EditSession.create``).
"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.editing.components import library_name
from mems_sketch.gui import icons
from mems_sketch.storage.project_files import PROJECT_FILE

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

        self.kinds = QListWidget()
        self.kinds.setObjectName("settings-pages")  # the same side list as Settings
        self.kinds.setFixedWidth(170)
        self.kinds.setIconSize(QSize(18, 18))
        self.kinds.setSpacing(1)
        for key, (label, icon, _about) in KINDS.items():
            item = QListWidgetItem(icons.icon(icon), label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setSizeHint(QSize(0, 30))
            self.kinds.addItem(item)
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
        self.process.addItems([DEFAULT_PROCESS, COPY_PROCESS])
        self.process.activated.connect(self._process_chosen)

        self.libraries = QListWidget()
        self.libraries.setObjectName("library-list")
        self.libraries.setMinimumHeight(90)
        self.libraries.itemSelectionChanged.connect(self._validate)
        add = QPushButton("Add…")
        icons.bind(add, "add")
        add.setToolTip("Load a folder of components (a library or another project)")
        add.clicked.connect(lambda: self.add_library())
        self.remove = QPushButton("Remove")
        self.remove.clicked.connect(self._remove_library)
        for button in (add, self.remove):
            button.setAutoDefault(False)  # Enter creates the project
        library_buttons = QVBoxLayout()
        library_buttons.addWidget(add)
        library_buttons.addWidget(self.remove)
        library_buttons.addStretch()
        library_row = QHBoxLayout()
        library_row.addWidget(self.libraries, 1)
        library_row.addLayout(library_buttons)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        form.addRow("Name", self.name)
        form.addRow("Location", where)
        form.addRow("", self.target)
        form.addRow("Process", self.process)
        form.addRow("Libraries", library_row)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.create_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.create_button.setText("Create")
        self.create_button.setDefault(True)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        details = QVBoxLayout()
        details.setContentsMargins(18, 14, 18, 14)
        details.setSpacing(10)
        self.heading = QLabel()
        self.heading.setObjectName("heading")
        details.addWidget(self.heading)
        details.addWidget(self.about)
        details.addLayout(form)
        details.addStretch()
        details.addWidget(self.buttons)
        right = QWidget()
        right.setLayout(details)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.kinds)
        layout.addWidget(right, 1)

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

    def _process_chosen(self, index: int) -> None:
        if self.process.itemText(index) == DEFAULT_PROCESS:
            self.process_from = None
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Copy the process of the project in folder", self.location.text().strip()
        )
        self.set_process_from(folder or None)

    def set_process_from(self, folder: str | None) -> None:
        """Copy the process of the project in ``folder`` (None: the default layers)."""
        self.process_from = folder
        while self.process.count() > 2:
            self.process.removeItem(2)
        if folder is None:
            self.process.setCurrentIndex(0)
            return
        self.process.addItem(icons.icon("folder"), f"From {Path(folder).name}")
        self.process.setItemData(2, folder, Qt.ItemDataRole.ToolTipRole)
        self.process.setCurrentIndex(2)

    def add_library(self, folder: str | None = None) -> None:
        if folder is None:
            folder = QFileDialog.getExistingDirectory(
                self, "Add library: choose its folder", self.location.text().strip()
            )
        if not folder:
            return
        name = library_name(folder)
        item = QListWidgetItem(icons.icon("library"), f"{name}    {folder}")
        item.setToolTip(folder)
        self.libraries.addItem(item)
        self._validate()

    def _remove_library(self) -> None:
        for item in self.libraries.selectedItems():
            self.libraries.takeItem(self.libraries.row(item))
        self._validate()


def _folder_name(name: str) -> str:
    """A folder name for a project name: spaces and odd characters become ``_``."""
    return re.sub(r"[^\w.-]+", "_", name.strip()).strip("._")
