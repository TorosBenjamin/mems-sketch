"""File › Import…: which cell of the file, its name, and where its layers go,
with a preview of the cell: a picture, its size and layers (#143)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTableWidgetItem,
    QVBoxLayout,
)

from mems_sketch.core.imports import cells
from mems_sketch.editing import EditSession
from mems_sketch.gui import theme, widgets
from mems_sketch.gui.help import HelpButton

LEAVE_OUT = "(leave out)"
PREVIEW_PX = 160


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
        self.layers = widgets.make_table(("GDS layer", "Goes to"), stretch=1)
        self.layers.setObjectName("import-layers")
        self.layers.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        form = widgets.form()
        form.addRow("Component", self.name)
        form.addRow("Cell", self.cell)
        layout = widgets.dialog_margins(QVBoxLayout(self))
        layout.addLayout(form)
        heading = QHBoxLayout()
        heading.setSpacing(theme.metric("space_s"))
        layers_label = QLabel("Layers")
        layers_label.setObjectName("group-heading")
        heading.addWidget(layers_label)
        heading.addWidget(
            HelpButton(
                "Each GDS layer goes to the project layer with the same GDS numbers. One "
                "the project does not have gets a *new* layer, or can be left out."
            )
        )
        heading.addStretch(1)
        layout.addLayout(heading)
        layout.addWidget(self.layers, 1)
        self.preview = QLabel()  # the cell as it would come in
        self.preview.setObjectName("import-preview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedHeight(PREVIEW_PX)
        self.summary = QLabel()
        self.summary.setObjectName("muted")
        self.summary.setWordWrap(True)
        layout.addWidget(self.preview)
        layout.addWidget(self.summary)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.cell.currentTextChanged.connect(self._fill_layers)
        self._fill_layers(self.cell.currentText())
        self.resize(440, 560)

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
        self._show_preview(cell, mapping)

    def _show_preview(self, cell: str, mapping: dict[str, str]) -> None:
        """The cell flattened, each GDS layer in the colour of where it goes."""
        from mems_sketch.core.imports import ImportedCell, cell_geometry
        from mems_sketch.gui.canvas import layer_color, layer_colors
        from mems_sketch.gui.component_picker import thumbnail

        try:
            geometry = cell_geometry(
                ImportedCell(name="preview", file="", cell=cell, layers=mapping, data=self.data)
            )
        except Exception as exc:  # noqa: BLE001 - shown instead of the picture
            self.preview.clear()
            self.summary.setText(f"No preview: {exc}")
            return
        colors = layer_colors(self.document.project.layers)
        for k, target in enumerate(mapping.values()):
            colors.setdefault(target, layer_color(len(colors) + k))
        size = QSize(PREVIEW_PX * 2, PREVIEW_PX)
        self.preview.setPixmap(thumbnail(geometry, colors, size, self.devicePixelRatioF()))
        box = geometry.bbox()
        layers = len(geometry.layer_names())
        if box is None:
            self.summary.setText(f"{cell}: nothing on its layers")
            return
        width, height = box[2] - box[0], box[3] - box[1]
        others = self.cell.count() - 1
        self.summary.setText(
            f"{cell}: {width:g} × {height:g} µm, {layers} layer{'s' * (layers != 1)}, "
            f"{geometry.point_count()} points"
            + (f"; the file has {others} other cell{'s' * (others != 1)}" if others else "")
        )

    def choices(self) -> dict:
        """The arguments for ``EditSession.imports.add`` besides the file."""
        layers = {
            self.layers.item(row, 0).text(): self.layers.cellWidget(row, 1).currentData()
            for row in range(self.layers.rowCount())
        }
        return {"cell": self.cell.currentText(), "name": self.name.text().strip(), "layers": layers}
