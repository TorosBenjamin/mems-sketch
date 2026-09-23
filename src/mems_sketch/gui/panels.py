"""Dockable panels: components, shape tree, parameters, points, process and messages."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.core.component import component_types
from mems_sketch.core.expressions import ExpressionError, resolve_variables
from mems_sketch.core.process import Layer
from mems_sketch.core.shapes import NodePath, Shape, child_lists
from mems_sketch.gui import icons
from mems_sketch.gui.document import ProjectDocument

PATH_ROLE = Qt.ItemDataRole.UserRole
SLOT_LABELS = {"boolean": ("A", "B")}


def describe(shape: Shape) -> str:
    """Short summary shown next to a node's name, with its alignment if it has one."""
    summary = _summary(shape)
    if shape.align is not None:
        summary += f" · {shape.align.point} at {shape.align.to}"
    return summary


def _summary(shape: Shape) -> str:
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


def _action_bar(title: QLabel | None, *actions: tuple[str, str, object]) -> QHBoxLayout:
    """A tool window's header row: an optional title, then small icon buttons.

    ``actions`` are ``(icon, tooltip, slot)``; the buttons are also returned in
    the layout's ``buttons`` attribute (by tooltip) for tests and shortcuts.
    """
    row = QHBoxLayout()
    row.setContentsMargins(6, 2, 4, 2)
    row.setSpacing(1)
    if title is not None:
        title.setObjectName("muted")
        row.addWidget(title, 1)
    else:
        row.addStretch(1)
    row.buttons = {}
    for name, tip, slot in actions:
        button = QToolButton()
        icons.bind(button, name)
        button.setIconSize(QSize(16, 16))
        button.setToolTip(tip)
        button.setAutoRaise(True)
        button.clicked.connect(slot)
        row.addWidget(button)
        row.buttons[tip] = button
    return row


def swatch_icon(color: QColor) -> QIcon:
    """A small rounded square in a layer's colour."""
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(color.darker(130))
    fill = QColor(color)
    fill.setAlpha(200)
    painter.setBrush(fill)
    painter.drawRoundedRect(3, 3, 18, 18, 4, 4)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def shape_icon(shape: Shape) -> QIcon:
    if shape.kind == "boolean":
        return icons.icon(shape.op)
    return icons.icon(icons.KIND_ICONS.get(shape.kind, "point"))


def _table(columns: Sequence[str]) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(list(columns))
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    table.verticalHeader().hide()
    return table


def _readonly(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    item.setForeground(QBrush(QColor("#8c8f99")))
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
    """The project's components, library components and built-ins.

    Double-click to open one in a tab (library and built-in components open
    read-only); Place inserts the selected one into the component being edited.
    """

    place_requested = Signal(str)
    open_requested = Signal(str)
    NAME_ROLE = Qt.ItemDataRole.UserRole

    def __init__(self, document: ProjectDocument) -> None:
        super().__init__()
        self.document = document
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemDoubleClicked.connect(self._activated)
        self.collapsed: set[str] = set()  # group titles, e.g. "Built-in"
        self.tree.itemCollapsed.connect(lambda item: self._set_collapsed(item, True))
        self.tree.itemExpanded.connect(lambda item: self._set_collapsed(item, False))
        self._refreshing = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.actions = _action_bar(
            None,
            ("add", "New component", self._new),
            ("edit", "Rename", self._rename),
            ("delete", "Delete", self._delete),
            ("top", "Set as top component", self._set_top),
            ("place", "Place in the edited component", self._place),
        )
        layout.addLayout(self.actions)
        layout.addWidget(self.tree)

    collapse_changed = Signal()

    def _set_collapsed(self, item: QTreeWidgetItem, collapsed: bool) -> None:
        if self._refreshing or item.parent() is not None:
            return
        key = self._group_key(item.text(0))
        (self.collapsed.add if collapsed else self.collapsed.discard)(key)
        self.collapse_changed.emit()

    @staticmethod
    def _group_key(title: str) -> str:
        return "project" if title.startswith("Project: ") else title

    def refresh(self) -> None:
        self._refreshing = True
        try:
            self._fill()
        finally:
            self._refreshing = False

    def _fill(self) -> None:
        project = self.document.project
        self.tree.clear()
        local = QTreeWidgetItem(self.tree, [f"Project: {project.name}"])
        local.setIcon(0, icons.icon("folder"))
        for name, definition in project.components.items():
            label = name + ("  (top)" if name == project.top else "")
            item = QTreeWidgetItem(local, [label])
            item.setData(0, self.NAME_ROLE, name)
            item.setToolTip(0, definition.description or name)
            item.setIcon(0, icons.icon("top" if name == project.top else "component"))
            self._mark_active(item, name, label)
        for library in project.libraries.values():
            group = QTreeWidgetItem(self.tree, [f"Library: {library.name}"])
            group.setIcon(0, icons.icon("library"))
            for name in library.components:
                item = QTreeWidgetItem(group, [name])
                item.setData(0, self.NAME_ROLE, f"{library.name}.{name}")
                item.setIcon(0, icons.icon("component"))
                item.setToolTip(0, f"{library.name}.{name} (library, read-only)")
                self._mark_active(item, f"{library.name}.{name}", name)
        builtins = QTreeWidgetItem(self.tree, ["Built-in"])
        builtins.setIcon(0, icons.icon("builtin"))
        for name in component_types():
            item = QTreeWidgetItem(builtins, [name])
            item.setData(0, self.NAME_ROLE, name)
            item.setIcon(0, icons.icon("component"))
            item.setToolTip(0, f"{name} (built-in, read-only)")
            self._mark_active(item, name, name)
        self.tree.expandAll()
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            if self._group_key(group.text(0)) in self.collapsed:
                group.setExpanded(False)

    def _mark_active(self, item: QTreeWidgetItem, name: str, label: str) -> None:
        if name == self.document.active:
            font = QFont()
            font.setBold(True)
            item.setFont(0, font)
            item.setIcon(0, icons.icon("eye" if self.document.read_only else "edit", "blue"))
            item.setToolTip(0, ("Viewing " if self.document.read_only else "Editing ") + label)

    def selected(self) -> str | None:
        items = self.tree.selectedItems()
        return items[0].data(0, self.NAME_ROLE) if items else None

    def _is_local(self, name: str | None) -> bool:
        return name is not None and name in self.document.project.components

    def _activated(self, item: QTreeWidgetItem) -> None:
        name = item.data(0, self.NAME_ROLE)
        if name is not None:
            self.open_requested.emit(name)

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
    collapse_changed = Signal()

    def __init__(self, document: ProjectDocument) -> None:
        super().__init__()
        self.document = document
        self.setHeaderLabels(["Shape", "Type"])
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.itemSelectionChanged.connect(self._emit_selection)
        self.itemChanged.connect(self._item_changed)
        self._rebuilding = False
        # Collapsed nodes per component (everything else is expanded).
        self.collapsed: dict[str, set[NodePath]] = {}
        self.itemCollapsed.connect(lambda item: self._set_collapsed(item, True))
        self.itemExpanded.connect(lambda item: self._set_collapsed(item, False))

    def _set_collapsed(self, item: QTreeWidgetItem, collapsed: bool) -> None:
        path = item.data(0, PATH_ROLE)
        if self._rebuilding or path is None:
            return
        paths = self.collapsed.setdefault(self.document.active, set())
        (paths.add if collapsed else paths.discard)(path)
        self.collapse_changed.emit()

    def rebuild(self, keep: list[NodePath] | None = None) -> None:
        keep = self.selected_paths() if keep is None else keep
        self._rebuilding = True
        self.clear()
        for index, shape in enumerate(self.document.shapes):
            self._add(self.invisibleRootItem(), shape, ((0, index),))
        self.expandAll()
        collapsed = self.collapsed.get(self.document.active, set())
        pending = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        while pending:
            item = pending.pop()
            if item.data(0, PATH_ROLE) in collapsed:
                item.setExpanded(False)
            pending.extend(item.child(i) for i in range(item.childCount()))
        self._rebuilding = False
        self.select_paths(keep)

    def _add(self, parent: QTreeWidgetItem, shape: Shape, path: NodePath) -> None:
        item = QTreeWidgetItem(parent, [shape.name or f"({shape.kind})", describe(shape)])
        item.setData(0, PATH_ROLE, path)
        item.setIcon(0, shape_icon(shape))
        if shape.repeat is not None:
            item.setIcon(1, icons.icon("repeat"))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Checked if shape.enabled else Qt.CheckState.Unchecked)
        if not shape.enabled:
            for column in (0, 1):
                item.setForeground(column, QBrush(QColor("#8c8f99")))
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
    """Parameters of the component in the current tab: default, limits, trial and value.

    A trial value overrides the default for viewing only; it is not saved.
    Library and built-in components are read-only, but trial values work.
    """

    COLUMNS = ("Name", "Default", "Min", "Max", "Trial", "Value")
    TRIAL = 4

    def __init__(self, document: ProjectDocument) -> None:
        super().__init__()
        self.document = document
        self.title = QLabel()
        self.table = _table(self.COLUMNS)
        self.table.itemChanged.connect(self._changed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.actions = _action_bar(
            self.title,
            ("add", "Add parameter", lambda: self._guard(self.document.add_parameter)),
            ("remove", "Remove the selected parameters", self._remove),
            ("clear", "Clear trial values", self._clear_trials),
        )
        layout.addLayout(self.actions)
        layout.addWidget(self.table)
        self._names: list[str] = []

    def _clear_trials(self) -> None:
        for name in list(self.document.trials.get(self.document.active, {})):
            self._guard(lambda n=name: self.document.set_trial(n, None))

    def refresh(self) -> None:
        parameters = self.document.active_definition.parameters
        read_only = self.document.read_only
        suffix = " (read-only; trial values work)" if read_only else ""
        self.title.setText(f"Parameters of <b>{self.document.active}</b>{suffix}")
        try:
            values = self.document.scope()
        except Exception:  # noqa: BLE001 - shown as "error" per row
            values = {}
        trials = self.document.trials.get(self.document.active, {})
        self.table.blockSignals(True)
        self.table.setRowCount(len(parameters))
        self._names = [p.name for p in parameters]
        for row, p in enumerate(parameters):
            value = values.get(p.name)
            make = _readonly if read_only else QTableWidgetItem
            cells = [
                make(p.name),
                make(_format(p.default)),
                make(_format(p.min)),
                make(_format(p.max)),
                QTableWidgetItem(_format(trials.get(p.name))),
                _readonly("error" if value is None else f"{value:g}"),
            ]
            cells[0].setToolTip(p.description)
            cells[self.TRIAL].setToolTip("Try a value without changing the design (not saved)")
            if p.name in trials:
                cells[-1].setForeground(QBrush(QColor("#e0a000")))
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
                case self.TRIAL:
                    self.document.set_trial(name, parse_value(text) if text else None)

        if not self._guard(apply):
            self.refresh()

    def _remove(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedItems()}, reverse=True)
        for row in rows:
            self._guard(lambda n=self._names[row]: self.document.remove_parameter(n))


# -- points ------------------------------------------------------------------


class PointsPanel(_Panel):
    """Alignment points the edited component declares, for whoever places it.

    ``At`` is an optional ``shape.point`` the position is measured from; ``X``
    and ``Y`` may be expressions over the component's parameters.
    """

    COLUMNS = ("Name", "At", "X", "Y", "Position")
    FIELDS = ("name", "at", "x", "y")

    def __init__(self, document: ProjectDocument) -> None:
        super().__init__()
        self.document = document
        self.title = QLabel()
        self.table = _table(self.COLUMNS)
        self.table.itemChanged.connect(self._changed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.actions = _action_bar(
            self.title,
            ("add", "Add point", lambda: self._guard(self.document.add_point)),
            ("remove", "Remove the selected points", self._remove),
        )
        layout.addLayout(self.actions)
        layout.addWidget(self.table)
        self._names: list[str] = []

    def refresh(self) -> None:
        points = self.document.active_definition.points
        self.title.setText(f"Points of <b>{self.document.active}</b> (besides center, top, …)")
        try:
            positions = self.document.declared_points()
        except Exception:  # noqa: BLE001 - shown as "error" per row
            positions = {}
        self.table.blockSignals(True)
        self.table.setRowCount(len(points))
        self._names = [p.name for p in points]
        for row, point in enumerate(points):
            position = positions.get(point.name)
            make = _readonly if self.document.read_only else QTableWidgetItem
            cells = [
                make(point.name),
                make(point.at or ""),
                make(_format(point.x)),
                make(_format(point.y)),
                _readonly("error" if position is None else "{:g}, {:g}".format(*position)),
            ]
            cells[0].setToolTip(point.description)
            for column, cell in enumerate(cells):
                self.table.setItem(row, column, cell)
        self.table.blockSignals(False)

    def _changed(self, item: QTableWidgetItem) -> None:
        name = self._names[item.row()]
        text = item.text().strip()
        field = self.FIELDS[item.column()] if item.column() < len(self.FIELDS) else None

        def apply() -> None:
            match field:
                case "name":
                    if text != name:
                        self.document.update_point(name, name=text)
                case "at":
                    self.document.update_point(name, at=text or None)
                case "x" | "y":
                    self.document.update_point(name, **{field: parse_value(text or "0")})

        if field is not None and not self._guard(apply):
            self.refresh()

    def _remove(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedItems()}, reverse=True)
        for row in rows:
            self._guard(lambda n=self._names[row]: self.document.remove_point(n))


# -- process -------------------------------------------------------------------


class LayersPanel(_Panel):
    """Process layers: visibility, colour, GDS mapping, etch loss and rules."""

    visibility_changed = Signal(str, bool)
    LAYER_COLUMNS = ("Layer", "GDS", "Datatype", "Undercut µm", "Min width µm", "Min space µm")

    def __init__(self, document: ProjectDocument) -> None:
        super().__init__()
        self.document = document
        self.visible: dict[str, bool] = {}
        self.colors: dict[str, QColor] = {}
        self.layers = _table(self.LAYER_COLUMNS)
        self.layers.itemChanged.connect(self._layer_changed)
        self.layers.setToolTip("Tick to show a layer; click a layer to draw on it")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.actions = _action_bar(
            None,
            ("add", "Add layer", lambda: self._guard(self.document.add_layer)),
            ("remove", "Remove the selected layers", self._remove_layers),
        )
        layout.addLayout(self.actions)
        layout.addWidget(self.layers)
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
            name_cell.setIcon(swatch_icon(self.colors[layer.name]))
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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.actions = _action_bar(
            QLabel("Use in expressions as process.<name>"),
            ("add", "Add constant", lambda: self._guard(self.document.add_constant)),
            ("remove", "Remove the selected constants", self._remove_constants),
        )
        layout.addLayout(self.actions)
        layout.addWidget(self.constants)
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

    counts_changed = Signal(int, int)  # errors, violations

    def show_messages(self, errors: list[str], violations) -> None:
        self.clear()
        for text in errors:
            item = QListWidgetItem(icons.icon("error"), text)
            item.setToolTip(text)
            self.addItem(item)
        if violations:
            summary = QListWidgetItem(
                f"{len(violations)} rule violation(s) — click one to zoom to it"
            )
            font = QFont()
            font.setBold(True)
            summary.setFont(font)
            self.addItem(summary)
        for v in violations:
            x0, y0, x1, y1 = v.bbox_um or (0, 0, 0, 0)
            where = f" at ({(x0 + x1) / 2:.2f}, {(y0 + y1) / 2:.2f}) µm" if v.bbox_um else ""
            item = QListWidgetItem(
                icons.icon("warning"), f"[{v.rule}] {v.layer}: {v.message}{where}"
            )
            item.setData(PATH_ROLE, v.bbox_um)
            self.addItem(item)
        if not errors and not violations:
            self.addItem(QListWidgetItem(icons.icon("ok"), "No rule violations."))
        self.counts_changed.emit(len(errors), len(violations))

    def _activated(self, item: QListWidgetItem) -> None:
        bbox = item.data(PATH_ROLE)
        if bbox:
            self.zoom_requested.emit(tuple(bbox))
