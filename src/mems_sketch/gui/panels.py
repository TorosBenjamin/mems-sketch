"""Dockable panels: shape tree, variables, layers and messages."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.core.design import Layer
from mems_sketch.core.expressions import ExpressionError, resolve_variables
from mems_sketch.core.shapes import NodePath, Shape, child_lists
from mems_sketch.gui.document import DesignDocument

PATH_ROLE = Qt.ItemDataRole.UserRole
SLOT_LABELS = {"boolean": ("A", "B")}


def describe(shape: Shape) -> str:
    """Short summary shown next to a node's name."""
    match shape.kind:
        case "ref":
            return shape.component
        case "boolean":
            return shape.op
        case "rect" | "polygon" | "circle" | "arc" | "path":
            return f"{shape.kind} · {shape.layer}"
        case "offset":
            return f"offset {shape.distance}"
        case "fillet":
            return f"fillet {shape.radius}"
        case "layer_map":
            return "layers " + ", ".join(f"{a}→{b}" for a, b in shape.mapping.items())
    return shape.kind


def _button_row(*buttons: QPushButton) -> QHBoxLayout:
    row = QHBoxLayout()
    for button in buttons:
        row.addWidget(button)
    row.addStretch()
    return row


class ShapeTree(QTreeWidget):
    """The design's shape tree. Emits the selected node paths."""

    selection_changed_paths = Signal(list)
    enabled_toggled = Signal(tuple, bool)

    def __init__(self, document: DesignDocument) -> None:
        super().__init__()
        self.document = document
        self.setHeaderLabels(["Shape", "Type"])
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.itemSelectionChanged.connect(self._emit_selection)
        self.itemChanged.connect(self._item_changed)
        self._rebuilding = False

    def rebuild(self, keep: list[NodePath] | None = None) -> None:
        keep = self.selected_paths() if keep is None else keep
        self._rebuilding = True
        self.clear()
        for index, shape in enumerate(self.document.design.shapes):
            self._add(self.invisibleRootItem(), shape, ((0, index),))
        self.expandAll()
        self._rebuilding = False
        self.select_paths(keep)

    def _add(self, parent: QTreeWidgetItem, shape: Shape, path: NodePath) -> None:
        item = QTreeWidgetItem(parent, [shape.name or f"({shape.kind})", describe(shape)])
        item.setData(0, PATH_ROLE, path)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Checked if shape.enabled else Qt.CheckState.Unchecked)
        if not shape.enabled:
            for column in (0, 1):
                item.setForeground(column, QBrush(QColor("#808080")))
        labels = SLOT_LABELS.get(shape.kind)
        for slot, children in enumerate(child_lists(shape)):
            holder = item
            if labels:
                holder = QTreeWidgetItem(item, [labels[slot], "operand"])
                holder.setFlags(Qt.ItemFlag.ItemIsEnabled)
            for index, child in enumerate(children):
                self._add(holder, child, (*path, (slot, index)))

    def selected_paths(self) -> list[NodePath]:
        return [p for item in self.selectedItems() if (p := item.data(0, PATH_ROLE)) is not None]

    def select_paths(self, paths: list[NodePath]) -> None:
        wanted = set(paths)
        self.blockSignals(True)
        self.clearSelection()
        iterator = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        while iterator:
            item = iterator.pop()
            if item.data(0, PATH_ROLE) in wanted:
                item.setSelected(True)
                self.scrollToItem(item)
            iterator.extend(item.child(i) for i in range(item.childCount()))
        self.blockSignals(False)
        self._emit_selection()

    def _emit_selection(self) -> None:
        self.selection_changed_paths.emit(self.selected_paths())

    def _item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        path = item.data(0, PATH_ROLE)
        if self._rebuilding or column != 0 or path is None:
            return
        enabled = item.checkState(0) == Qt.CheckState.Checked
        if enabled != self.document.node(path).enabled:
            self.enabled_toggled.emit(path, enabled)


class VariablesPanel(QWidget):
    """Global variables: name, expression and evaluated value."""

    error = Signal(str)

    def __init__(self, document: DesignDocument) -> None:
        super().__init__()
        self.document = document
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Expression", "Value"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.itemChanged.connect(self._changed)
        add, remove = QPushButton("Add"), QPushButton("Remove")
        add.clicked.connect(self._add)
        remove.clicked.connect(self._remove)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table)
        layout.addLayout(_button_row(add, remove))
        self._names: list[str] = []

    def refresh(self) -> None:
        variables = self.document.design.variables
        try:
            values = resolve_variables(variables)
        except (ExpressionError, ZeroDivisionError, ValueError):
            values = {}
        self.table.blockSignals(True)
        self.table.setRowCount(len(variables))
        self._names = list(variables)
        for row, (name, expression) in enumerate(variables.items()):
            value = values.get(name)
            cells = [
                QTableWidgetItem(name),
                QTableWidgetItem(_format(expression)),
                QTableWidgetItem("error" if value is None else f"{value:g}"),
            ]
            cells[2].setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            for column, cell in enumerate(cells):
                self.table.setItem(row, column, cell)
        self.table.blockSignals(False)

    def _changed(self, item: QTableWidgetItem) -> None:
        row = item.row()
        old = self._names[row]
        try:
            if item.column() == 0 and item.text() != old:
                self.document.rename_variable(old, item.text().strip())
            elif item.column() == 1:
                self.document.set_variable(old, parse_value(item.text()))
        except Exception as exc:  # noqa: BLE001 - reported to the user
            self.error.emit(str(exc))
            self.refresh()

    def _add(self) -> None:
        n = 1
        while f"var{n}" in self.document.design.variables:
            n += 1
        self.document.set_variable(f"var{n}", 0.0)

    def _remove(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedItems()}, reverse=True)
        for row in rows:
            try:
                self.document.remove_variable(self._names[row])
            except Exception as exc:  # noqa: BLE001
                self.error.emit(f"cannot delete '{self._names[row]}': {exc}")


class LayersPanel(QWidget):
    """Process layers: visibility, colour, GDS mapping, etch loss and rules."""

    visibility_changed = Signal(str, bool)
    error = Signal(str)
    COLUMNS = ["Layer", "GDS", "Datatype", "Undercut µm", "Min width µm", "Min space µm"]

    def __init__(self, document: DesignDocument) -> None:
        super().__init__()
        self.document = document
        self.visible: dict[str, bool] = {}
        self.colors: dict[str, QColor] = {}
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().hide()
        self.table.itemChanged.connect(self._changed)
        add, remove = QPushButton("Add layer"), QPushButton("Remove")
        add.clicked.connect(lambda: self._guard(self.document.add_layer))
        remove.clicked.connect(self._remove)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table)
        layout.addLayout(_button_row(add, remove))
        self._names: list[str] = []

    def refresh(self) -> None:
        from mems_sketch.gui.canvas import layer_color

        layers = self.document.design.layers
        self._names = list(layers)
        self.colors = {name: layer_color(i) for i, name in enumerate(layers)}
        self.table.blockSignals(True)
        self.table.setRowCount(len(layers))
        for row, layer in enumerate(layers.values()):
            values = [
                layer.name,
                layer.gds_layer,
                layer.gds_datatype,
                layer.undercut,
                layer.min_width,
                layer.min_space,
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(
                    ""
                    if value is None
                    else f"{value:g}"
                    if isinstance(value, float)
                    else str(value)
                )
                self.table.setItem(row, column, cell)
            name_cell = self.table.item(row, 0)
            name_cell.setFlags(name_cell.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            name_cell.setCheckState(
                Qt.CheckState.Checked
                if self.visible.get(layer.name, True)
                else Qt.CheckState.Unchecked
            )
            swatch = QPixmap(12, 12)
            swatch.fill(self.colors[layer.name])
            name_cell.setIcon(QIcon(swatch))
        self.table.blockSignals(False)

    def _changed(self, item: QTableWidgetItem) -> None:
        name = self._names[item.row()]
        if item.column() == 0:
            shown = item.checkState() == Qt.CheckState.Checked
            if shown != self.visible.get(name, True):
                self.visible[name] = shown
                self.visibility_changed.emit(name, shown)
                return
            if item.text().strip() == name:
                return
        row = item.row()
        texts = [self.table.item(row, c).text().strip() for c in range(len(self.COLUMNS))]

        def optional(text: str) -> float | None:
            return float(text) if text else None

        try:
            layer = Layer(
                texts[0],
                int(texts[1]),
                int(texts[2]),
                float(texts[3] or 0),
                optional(texts[4]),
                optional(texts[5]),
            )
            self.document.set_layer(name, layer)
            if layer.name != name:
                self.visible[layer.name] = self.visible.pop(name, True)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"layer '{name}': {exc}")
            self.refresh()

    def _remove(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedItems()}, reverse=True)
        for row in rows:
            self._guard(lambda n=self._names[row]: self.document.remove_layer(n))

    def _guard(self, action) -> None:
        try:
            action()
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class MessagesPanel(QListWidget):
    """Errors and rule violations. Activating a violation zooms to it."""

    zoom_requested = Signal(tuple)

    def __init__(self) -> None:
        super().__init__()
        self.itemActivated.connect(self._activated)
        self.itemClicked.connect(self._activated)

    def show_messages(self, errors: list[str], violations) -> None:
        self.clear()
        for text in errors:
            item = QListWidgetItem(f"Error: {text}")
            item.setForeground(QBrush(QColor("#ff6b6b")))
            self.addItem(item)
        if violations:
            summary = QListWidgetItem(
                f"{len(violations)} rule violation(s) — click one to zoom to it"
            )
            summary.setForeground(QBrush(QColor("#ffb347")))
            self.addItem(summary)
        for v in violations:
            x0, y0, x1, y1 = v.bbox_um or (0, 0, 0, 0)
            where = f" at ({(x0 + x1) / 2:.2f}, {(y0 + y1) / 2:.2f}) µm" if v.bbox_um else ""
            item = QListWidgetItem(f"    [{v.rule}] {v.layer}: {v.message}{where}")
            item.setData(PATH_ROLE, v.bbox_um)
            item.setForeground(QBrush(QColor("#ffb347")))
            self.addItem(item)
        if not errors and not violations:
            self.addItem(QListWidgetItem("No rule violations."))

    def _activated(self, item: QListWidgetItem) -> None:
        bbox = item.data(PATH_ROLE)
        if bbox:
            self.zoom_requested.emit(tuple(bbox))


def parse_value(text: str) -> float | str:
    """A number if the text is one, otherwise the text as an expression."""
    text = text.strip()
    try:
        return float(text)
    except ValueError:
        if not text:
            raise ValueError("a value is required") from None
        return text


def _format(value: float | str) -> str:
    return f"{value:g}" if isinstance(value, float | int) else str(value)
