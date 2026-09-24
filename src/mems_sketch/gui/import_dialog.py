"""File › Import…: which cell of the file, its name, and where its layers go."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from mems_sketch.core.imports import cells
from mems_sketch.editing import EditSession
from mems_sketch.gui.help import HelpButton

LEAVE_OUT = "(leave out)"


class ImportDialog(QDialog):
    """Raises ``ValueError`` (not a GDS file) or ``OSError`` before it is shown."""

    def __init__(self, document: EditSession, path: str | Path, parent=None) -> None:
        super().__init__(parent)
        self.document = document
        self.data, _ = document.imports.read(path)
        names = cells(self.data)
        if not names:
            raise ValueError(f"{Path(path).name} has no cells")
        self.setWindowTitle(f"Import {Path(path).name}")
        self.name = QLineEdit(document.imports.suggested_name(path))
        self.cell = QComboBox()
        self.cell.addItems(names)
        self.cell.setToolTip("The cell to import, with its sub-cells (flattened)")
        self.layers = QTableWidget(0, 2)
        self.layers.setHorizontalHeaderLabels(["GDS layer", "Goes to"])
        self.layers.verticalHeader().hide()
        self.layers.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.layers.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        form = QFormLayout()
        form.addRow("Component", self.name)
        form.addRow("Cell", self.cell)
        layers_label = QLabel("Layers")
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        heading = QFormLayout()
        heading.addRow(
            layers_label,
            HelpButton(
                "Each GDS layer goes to the project layer with the same GDS numbers. One "
                "the project does not have gets a *new* layer, or can be left out."
            ),
        )
        layout.addLayout(heading)
        layout.addWidget(self.layers)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.cell.currentTextChanged.connect(self._fill_layers)
        self._fill_layers(self.cell.currentText())
        self.resize(420, 360)

    def _fill_layers(self, cell: str) -> None:
        mapping = self.document.imports.default_layers(self.data, cell)
        existing = list(self.document.project.layers)
        self.layers.setRowCount(len(mapping))
        for row, (gds, target) in enumerate(mapping.items()):
            self.layers.setItem(row, 0, QTableWidgetItem(gds))
            choice = QComboBox()
            for layer in existing:
                choice.addItem(layer, layer)
            if target not in existing:
                choice.addItem(f"{target} (new)", target)
            choice.addItem(LEAVE_OUT, "")
            choice.setCurrentIndex(choice.findData(target))
            self.layers.setCellWidget(row, 1, choice)

    def choices(self) -> dict:
        """The arguments for ``EditSession.imports.add`` besides the file."""
        layers = {
            self.layers.item(row, 0).text(): self.layers.cellWidget(row, 1).currentData()
            for row in range(self.layers.rowCount())
        }
        return {"cell": self.cell.currentText(), "name": self.name.text().strip(), "layers": layers}
