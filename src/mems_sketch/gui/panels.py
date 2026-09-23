"""Dockable panels: components, shape tree, parameters, process and messages."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
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

from mems_sketch.core.component import component_types
from mems_sketch.core.expressions import ExpressionError, resolve_variables
from mems_sketch.core.process import Layer
from mems_sketch.core.shapes import NodePath, Shape, child_lists
from mems_sketch.gui.document import ProjectDocument

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


def parse_value(text: str) -> float | str:
    """A number if the text is one, otherwise the text as an expression."""
    text = text.strip()
    try:
        return float(text)
    except ValueError:
        if not text:
            raise ValueError("a value is required") from None
        return text


def _format(value) -> str:
    if value is None:
        return ""
    return f"{value:g}" if isinstance(value, float | int) else str(value)


def _button_row(*buttons: QPushButton) -> QHBoxLayout:
    row = QHBoxLayout()
    for button in buttons:
        row.addWidget(button)
    row.addStretch()
    return row


def _table(columns: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    table.verticalHeader().hide()
    return table


def _readonly(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    item.setForeground(QBrush(QColor("#808080")))
    return item


class _Panel(QWidget):
    error = Signal(str)

    def _guard(self, action) -> bool:
        try:
            action()
            return True
        except Exception as exc:  # noqa: BLE001 - reported to the user
            self.error.emit(str(exc))
            return False


# -- components --------------------------------------------------------------


class ComponentsPanel(_Panel):
    """The project's components (editable) and library/built-in components (placeable).

    Double-click a project component to edit it, or a library/built-in
    component to place it in the component being edited.
    """

    place_requested = Signal(str)
    NAME_ROLE = Qt.ItemDataRole.UserRole

    def __init__(self, document: ProjectDocument) -> None:
        super().__init__()
        self.document = document
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemDoubleClicked.connect(self._activated)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tree)
        row = QHBoxLayout()
        for text, slot in (
            ("New", self._new),
            ("Rename", self._rename),
            ("Delete", self._delete),
            ("Set top", self._set_top),
            ("Place", self._place),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)

    def refresh(self) -> None:
        project = self.document.project
        self.tree.clear()
        local = QTreeWidgetItem(self.tree, [f"Project: {project.name}"])
        for name, definition in project.components.items():
            label = name + ("  (top)" if name == project.top else "")
            item = QTreeWidgetItem(local, [label])
            item.setData(0, self.NAME_ROLE, name)
            item.setToolTip(0, definition.description or name)
            if name == self.document.active:
                font = QFont()
                font.setBold(True)
                item.setFont(0, font)
                item.setText(0, f"✎ {label}")
        for library in project.libraries.values():
            group = QTreeWidgetItem(self.tree, [f"Library: {library.name}"])
            for name in library.components:
                item = QTreeWidgetItem(group, [name])
                item.setData(0, self.NAME_ROLE, f"{library.name}.{name}")
        builtins = QTreeWidgetItem(self.tree, ["Built-in"])
        for name in component_types():
            QTreeWidgetItem(builtins, [name]).setData(0, self.NAME_ROLE, name)
        self.tree.expandAll()

    def selected(self) -> str | None:
        items = self.tree.selectedItems()
        return items[0].data(0, self.NAME_ROLE) if items else None

    def _is_local(self, name: str | None) -> bool:
        return name is not None and name in self.document.project.components

    def _activated(self, item: QTreeWidgetItem) -> None:
        name = item.data(0, self.NAME_ROLE)
        if name is None:
            return
        if self._is_local(name):
            self._guard(lambda: self.document.set_active(name))
        else:
            self.place_requested.emit(name)

    def _new(self) -> None:
        name, ok = QInputDialog.getText(self, "New component", "Component name:")
        if ok and name.strip():
            self._guard(lambda: self.document.new_component(name.strip()))

    def _rename(self) -> None:
        old = self.selected()
        if not self._is_local(old):
            self.error.emit("select a project component to rename")
            return
        new, ok = QInputDialog.getText(self, "Rename component", "New name:", text=old)
        if ok and new.strip() and new.strip() != old:
            self._guard(lambda: self.document.rename_component(old, new.strip()))

    def _delete(self) -> None:
        name = self.selected()
        if self._is_local(name):
            self._guard(lambda: self.document.delete_component(name))

    def _set_top(self) -> None:
        name = self.selected()
        if self._is_local(name):
            self._guard(lambda: self.document.set_top(name))

    def _place(self) -> None:
        name = self.selected()
        if name is not None:
            self.place_requested.emit(name)


# -- shape tree ----------------------------------------------------------------


class ShapeTree(QTreeWidget):
    """The active component's shape tree. Emits the selected node paths."""

    selection_changed_paths = Signal(list)
    enabled_toggled = Signal(tuple, bool)

    def __init__(self, document: ProjectDocument) -> None:
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
        for index, shape in enumerate(self.document.shapes):
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
        pending = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        while pending:
            item = pending.pop()
            if item.data(0, PATH_ROLE) in wanted:
                item.setSelected(True)
                self.scrollToItem(item)
            pending.extend(item.child(i) for i in range(item.childCount()))
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


# -- parameters ----------------------------------------------------------------


class ParametersPanel(_Panel):
    """Parameters of the component being edited: default, limits and resolved value."""

    COLUMNS = ["Name", "Default", "Min", "Max", "Value"]

    def __init__(self, document: ProjectDocument) -> None:
        super().__init__()
        self.document = document
        self.title = QLabel()
        self.table = _table(self.COLUMNS)
        self.table.itemChanged.connect(self._changed)
        add, remove = QPushButton("Add"), QPushButton("Remove")
        add.clicked.connect(lambda: self._guard(self.document.add_parameter))
        remove.clicked.connect(self._remove)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.title)
        layout.addWidget(self.table)
        layout.addLayout(_button_row(add, remove))
        self._names: list[str] = []

    def refresh(self) -> None:
        parameters = self.document.active_definition.parameters
        self.title.setText(f" Parameters of <b>{self.document.active}</b>")
        try:
            values = self.document.scope()
        except Exception:  # noqa: BLE001 - shown as "error" per row
            values = {}
        self.table.blockSignals(True)
        self.table.setRowCount(len(parameters))
        self._names = [p.name for p in parameters]
        for row, p in enumerate(parameters):
            value = values.get(p.name)
            cells = [
                QTableWidgetItem(p.name),
                QTableWidgetItem(_format(p.default)),
                QTableWidgetItem(_format(p.min)),
                QTableWidgetItem(_format(p.max)),
                _readonly("error" if value is None else f"{value:g}"),
            ]
            cells[0].setToolTip(p.description)
            for column, cell in enumerate(cells):
                self.table.setItem(row, column, cell)
        self.table.blockSignals(False)

    def _changed(self, item: QTableWidgetItem) -> None:
        name = self._names[item.row()]
        text = item.text().strip()

        def apply() -> None:
            match item.column():
                case 0:
                    if text != name:
                        self.document.update_parameter(name, name=text)
                case 1:
                    self.document.update_parameter(name, default=parse_value(text))
                case 2:
                    self.document.update_parameter(name, min=float(text) if text else None)
                case 3:
                    self.document.update_parameter(name, max=float(text) if text else None)

        if not self._guard(apply):
            self.refresh()

    def _remove(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedItems()}, reverse=True)
        for row in rows:
            self._guard(lambda n=self._names[row]: self.document.remove_parameter(n))


# -- process -------------------------------------------------------------------


class LayersPanel(_Panel):
    """Process layers: visibility, colour, GDS mapping, etch loss and rules."""

    visibility_changed = Signal(str, bool)
    LAYER_COLUMNS = ["Layer", "GDS", "Datatype", "Undercut µm", "Min width µm", "Min space µm"]

    def __init__(self, document: ProjectDocument) -> None:
        super().__init__()
        self.document = document
        self.visible: dict[str, bool] = {}
        self.colors: dict[str, QColor] = {}
        self.layers = _table(self.LAYER_COLUMNS)
        self.layers.itemChanged.connect(self._layer_changed)
        add_layer, remove_layer = QPushButton("Add layer"), QPushButton("Remove")
        add_layer.clicked.connect(lambda: self._guard(self.document.add_layer))
        remove_layer.clicked.connect(self._remove_layers)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.layers)
        layout.addLayout(_button_row(add_layer, remove_layer))
        self._layer_names: list[str] = []

    def refresh(self) -> None:
        from mems_sketch.gui.canvas import layer_color

        layers = self.document.project.layers
        self._layer_names = list(layers)
        self.colors = {name: layer_color(i) for i, name in enumerate(layers)}
        self.layers.blockSignals(True)
        self.layers.setRowCount(len(layers))
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
                self.layers.setItem(row, column, QTableWidgetItem(_format(value)))
            name_cell = self.layers.item(row, 0)
            name_cell.setFlags(name_cell.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            shown = self.visible.get(layer.name, True)
            name_cell.setCheckState(Qt.CheckState.Checked if shown else Qt.CheckState.Unchecked)
            swatch = QPixmap(12, 12)
            swatch.fill(self.colors[layer.name])
            name_cell.setIcon(QIcon(swatch))
        self.layers.blockSignals(False)

    def _layer_changed(self, item: QTableWidgetItem) -> None:
        name = self._layer_names[item.row()]
        if item.column() == 0:
            shown = item.checkState() == Qt.CheckState.Checked
            if shown != self.visible.get(name, True):
                self.visible[name] = shown
                self.visibility_changed.emit(name, shown)
                return
            if item.text().strip() == name:
                return
        row = item.row()
        texts = [self.layers.item(row, c).text().strip() for c in range(len(self.LAYER_COLUMNS))]

        def optional(text: str) -> float | None:
            return float(text) if text else None

        def apply() -> None:
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

        if not self._guard(apply):
            self.refresh()

    def _remove_layers(self) -> None:
        rows = sorted({i.row() for i in self.layers.selectedItems()}, reverse=True)
        for row in rows:
            self._guard(lambda n=self._layer_names[row]: self.document.remove_layer(n))


class ConstantsPanel(_Panel):
    """Process constants, available in every expression as ``process.<name>``."""

    def __init__(self, document: ProjectDocument) -> None:
        super().__init__()
        self.document = document
        self.constants = _table(["Constant", "Expression", "Value"])
        self.constants.itemChanged.connect(self._constant_changed)
        add_const, remove_const = QPushButton("Add constant"), QPushButton("Remove")
        add_const.clicked.connect(lambda: self._guard(self.document.add_constant))
        remove_const.clicked.connect(self._remove_constants)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(" Use in expressions as process.<name>"))
        layout.addWidget(self.constants)
        layout.addLayout(_button_row(add_const, remove_const))
        self._constant_names: list[str] = []

    def refresh(self) -> None:
        project = self.document.project
        constants = project.process.constants
        try:
            values = resolve_variables(constants)
        except (ExpressionError, ZeroDivisionError, ValueError):
            values = {}
        self._constant_names = list(constants)
        self.constants.blockSignals(True)
        self.constants.setRowCount(len(constants))
        for row, (name, expression) in enumerate(constants.items()):
            value = values.get(name)
            self.constants.setItem(row, 0, QTableWidgetItem(name))
            self.constants.setItem(row, 1, QTableWidgetItem(_format(expression)))
            self.constants.setItem(row, 2, _readonly("error" if value is None else f"{value:g}"))
        self.constants.blockSignals(False)

    def _constant_changed(self, item: QTableWidgetItem) -> None:
        name = self._constant_names[item.row()]
        text = item.text().strip()

        def apply() -> None:
            if item.column() == 0 and text != name:
                self.document.rename_constant(name, text)
            elif item.column() == 1:
                self.document.set_constant(name, parse_value(text))

        if not self._guard(apply):
            self.refresh()

    def _remove_constants(self) -> None:
        rows = sorted({i.row() for i in self.constants.selectedItems()}, reverse=True)
        for row in rows:
            self._guard(lambda n=self._constant_names[row]: self.document.remove_constant(n))


# -- messages ------------------------------------------------------------------


class MessagesPanel(QListWidget):
    """Errors and rule violations. Clicking a violation zooms to it."""

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
