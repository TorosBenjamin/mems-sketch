"""The Points tool window: every point of the edited component, as objects.

The component's declared points come first, then its ports (where wires
attach: a point with a direction, a wire width and a layer), both editable;
then the points every component has (``center``, ``left``, …, of the whole
component) and those of each named top-level shape, read-only (a placed
component's ports among them: ``port.drive``). Hover a point to see it on the
canvas, click it to pan there and edit it in the form below the list.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QBrush, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.core.shapes import BBOX_POINTS
from mems_sketch.core.user_component import DIRECTIONS
from mems_sketch.editing import EditSession
from mems_sketch.gui import icons, theme, widgets
from mems_sketch.gui.help import HelpButton
from mems_sketch.gui.panels import _Panel
from mems_sketch.gui.value_edit import ValueEdit, ask_parameter_name

# ("declared", name) / ("port", name) / ("default", name) / (shape, name)
KEY_ROLE = Qt.ItemDataRole.UserRole + 20
GROUP_ROLE = Qt.ItemDataRole.UserRole + 21  # a group row: "default" or the shape's name

Key = tuple[str, str]
Marker = tuple[str, float, float]  # label, x, y


OWN = ("declared", "port")  # the component's own points and ports: editable


def reference(key: Key) -> str:
    """How the edited component refers to a point: ``tip``, ``center``, ``beam.left``;
    its port: ``port.drive`` (what a placement of it is aligned by)."""
    group, name = key
    if group == "port":
        return f"port.{name}"
    return name if group in ("declared", "default") else f"{group}.{name}"


def _position(xy: tuple[float, float] | None) -> str:
    return "error" if xy is None else f"{xy[0]:.4g}, {xy[1]:.4g}"


HINT_HEIGHT_PX = 32  # the form's place while no point is chosen
QWIDGETSIZE_MAX = (1 << 24) - 1


class PointsPanel(_Panel):
    """See the module docstring."""

    hovered = Signal(object)  # the Marker under the mouse, or None
    focused = Signal(object)  # the selected point's Marker (pan to it), or None

    def __init__(self, document: EditSession) -> None:
        super().__init__()
        self.document = document
        self.title = self.header_context = widgets.header_context()  # the component
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Point", "Position"])
        self.tree.setMouseTracking(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemEntered.connect(lambda item, _: self.hovered.emit(self._marker(item)))
        self.tree.viewport().installEventFilter(self)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemChanged.connect(self._renamed)
        self.tree.itemExpanded.connect(self._remember)
        self.tree.itemCollapsed.connect(self._remember)
        self.form_host = QWidget()
        self.form_host.setLayout(QVBoxLayout())
        self.form_host.layout().setContentsMargins(8, 6, 8, 6)
        self.form: QWidget | None = None
        splitter = widgets.splitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.form_host)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.actions = widgets.header_actions(
            ("plus", "Add point", self._add),
            ("port", "Add port", self._add_port),
            ("minus", "Delete the selected point", self._remove),
        )
        self.header_buttons = list(self.actions)  # shown in the tool window's header
        layout.addWidget(splitter)
        self._positions: dict[Key, tuple[float, float] | None] = {}
        self._expanded: dict[str, set[str]] = {}  # per component: open groups
        self._rebuilding = False

    # -- the list ---------------------------------------------------------------

    def refresh(self) -> None:
        keep = self.selected_key()
        self.hovered.emit(None)  # the rows are rebuilt
        self._rebuilding = True
        self.tree.clear()
        definition = self.document.active_definition
        read_only = self.document.read_only
        self.title.setText(self.document.active)
        results = self.document.results
        try:
            declared = results.declared_points()
        except Exception:  # noqa: BLE001 - shown as "error" per row
            declared = {}
        try:
            defaults = results.default_points()
        except Exception:  # noqa: BLE001
            defaults = {}
        try:
            shapes = {} if read_only else results.shape_points()
        except Exception:  # noqa: BLE001
            shapes = {}
        try:
            ports = results.declared_ports()
        except Exception:  # noqa: BLE001
            ports = {}
        self._positions = {}
        for point in definition.points:
            item = self._item(self.tree, ("declared", point.name), declared.get(point.name))
            item.setIcon(0, icons.icon("point"))
            if not read_only:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            tip = f"at {point.at}, " if point.at else ""
            item.setToolTip(0, f"{point.description}\n{tip}double-click to rename".strip())
        for port in definition.ports:
            built = ports.get(port.name)
            item = self._item(self.tree, ("port", port.name), built and (built.x, built.y))
            item.setIcon(0, icons.icon("port"))
            if not read_only:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            how = (
                f"{built.angle:g}°, {built.width:g} µm wide, on {built.layer}" if built else "error"
            )
            item.setToolTip(0, f"{port.description}\nPort: {how}; double-click to rename".strip())
        groups = [("default", "Its box", "Every component has these: of the box around it")]
        groups += [(name, name, f"The points of shape {name}") for name in shapes]
        opened = self._expanded.setdefault(self.document.active, set())
        for group, title, tip in groups:
            holder = QTreeWidgetItem(self.tree, [title])
            holder.setData(0, GROUP_ROLE, group)
            holder.setFlags(Qt.ItemFlag.ItemIsEnabled)
            holder.setToolTip(0, tip)
            holder.setIcon(0, icons.icon("shapes" if group != "default" else "component"))
            points = defaults if group == "default" else shapes[group]
            for name, xy in points.items():
                self._item(holder, (group, name), xy).setForeground(0, QBrush(theme.color("muted")))
            holder.setExpanded(group in opened)
        self.tree.resizeColumnToContents(0)
        self._rebuilding = False
        self.select(keep)
        self.actions.buttons["Add point"].setEnabled(not read_only)
        self.actions.buttons["Add port"].setEnabled(not read_only)
        self._update_remove()

    def _item(self, parent, key: Key, xy) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [key[1], _position(xy)])
        item.setData(0, KEY_ROLE, key)
        item.setForeground(1, QBrush(theme.color("muted")))
        self._positions[key] = xy
        return item

    def _remember(self, item: QTreeWidgetItem) -> None:
        group = item.data(0, GROUP_ROLE)
        if self._rebuilding or group is None:
            return
        opened = self._expanded.setdefault(self.document.active, set())
        (opened.add if item.isExpanded() else opened.discard)(group)

    def _items(self):
        pending = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while pending:
            item = pending.pop(0)
            yield item
            pending.extend(item.child(i) for i in range(item.childCount()))

    def selected_key(self) -> Key | None:
        items = self.tree.selectedItems()
        return items[0].data(0, KEY_ROLE) if items else None

    def select(self, key: Key | None) -> None:
        """Select a point (None: none) and show it in the form."""
        found = next((i for i in self._items() if key and i.data(0, KEY_ROLE) == key), None)
        self.tree.blockSignals(True)
        self.tree.clearSelection()
        if found is not None:
            if found.parent() is not None:
                found.parent().setExpanded(True)
            found.setSelected(True)
            self.tree.scrollToItem(found)
        self.tree.blockSignals(False)
        self._update_remove()
        self._show_form(self.selected_key())

    def _marker(self, item: QTreeWidgetItem | None) -> Marker | None:
        key = item.data(0, KEY_ROLE) if item is not None else None
        xy = self._positions.get(key) if key else None
        return None if xy is None else (reference(key), *xy)

    def focused_marker(self) -> Marker | None:
        items = self.tree.selectedItems()
        return self._marker(items[0]) if items else None

    def eventFilter(self, watched, event) -> bool:
        if watched is self.tree.viewport() and event.type() == QEvent.Type.Leave:
            self.hovered.emit(None)
        return super().eventFilter(watched, event)

    def _selection_changed(self) -> None:
        if self._rebuilding:
            return
        self._update_remove()
        self._show_form(self.selected_key())
        self.focused.emit(self.focused_marker())

    def _update_remove(self) -> None:
        key = self.selected_key()
        editable = key is not None and key[0] in OWN and not self.document.read_only
        self.actions.buttons["Delete the selected point"].setEnabled(editable)

    # -- editing ----------------------------------------------------------------

    def _add(self) -> None:
        names = []
        if self._guard(lambda: names.append(self.document.points.add())):
            self.select(("declared", names[0]))

    def _add_port(self) -> None:
        names = []
        if self._guard(lambda: names.append(self.document.ports.add())):
            self.select(("port", names[0]))

    def name_port(self, key: Key) -> None:
        """Re-export a placed component's port (``p1.port.signal``) as one of this
        component's: at it, taking its direction, width and layer."""
        taken = {p.name for p in self.document.active_definition.ports}
        stem = key[1].removeprefix("port.").partition("[")[0]
        name = stem if stem not in taken else f"{key[0]}_{stem}"
        names = []
        if self._guard(lambda: names.append(self.document.ports.add(at=reference(key), name=name))):
            self.select(("port", names[0]))

    def name_point(self, key: Key) -> None:
        """Re-export a default or shape point: declare one right there, measured from
        it, and start renaming it."""
        names = []
        if self._guard(lambda: names.append(self.document.points.add(at=reference(key)))):
            self.select(("declared", names[0]))
            self.focused.emit(self.focused_marker())
            self.rename(("declared", names[0]))

    def export_all(self, shape: str) -> None:
        """Re-export a shape's own points (a placed component's declared points), not
        its box points, in one step."""
        points = [n for g, n in self._positions if g == shape and _own_point(n)]
        if not points:
            return
        names = []
        if self._guard(
            lambda: names.extend(self.document.points.export([f"{shape}.{n}" for n in points]))
        ):
            self.select(("declared", names[0]))

    def _remove(self) -> None:
        key = self.selected_key()
        if key is not None and key[0] in OWN:
            edits = self.document.points if key[0] == "declared" else self.document.ports
            self._guard(lambda: edits.remove(key[1]))

    def rename(self, key: Key) -> None:
        item = next((i for i in self._items() if i.data(0, KEY_ROLE) == key), None)
        if item is not None:
            self.tree.editItem(item, 0)

    def _renamed(self, item: QTreeWidgetItem, column: int) -> None:
        key = item.data(0, KEY_ROLE)
        if self._rebuilding or column != 0 or key is None or key[0] not in OWN:
            return
        new = item.text(0).strip()
        if new == key[1]:
            return
        edits = self.document.points if key[0] == "declared" else self.document.ports
        if self._guard(lambda: edits.update(key[1], name=new)):
            self.select((key[0], new))
        else:
            self.refresh()

    def _menu(self, position) -> None:
        item = self.tree.itemAt(position)
        key = item.data(0, KEY_ROLE) if item is not None else None
        group = item.data(0, GROUP_ROLE) if item is not None else None
        menu = QMenu(self)
        if group not in (None, "default") and not self.document.read_only:
            own = [n for g, n in self._positions if g == group and _own_point(n)]
            action = menu.addAction(icons.icon("add"), f"Re-export the points of {group}")
            action.setEnabled(bool(own))
            action.setToolTip(", ".join(own) or "It declares no points of its own")
            action.triggered.connect(lambda: self.export_all(group))
            menu.exec(self.tree.viewport().mapToGlobal(position))
            return
        if key is None:
            return
        menu.addAction("Copy reference").triggered.connect(
            lambda: QApplication.clipboard().setText(reference(key))
        )
        if not self.document.read_only:
            if key[0] in OWN:
                edits = self.document.points if key[0] == "declared" else self.document.ports
                menu.addAction("Rename").triggered.connect(lambda: self.rename(key))
                menu.addAction(icons.icon("remove"), "Delete").triggered.connect(
                    lambda: self._guard(lambda: edits.remove(key[1]))
                )
            elif key[1].startswith("port."):
                menu.addAction(
                    icons.icon("port"), "Re-export as a port of this component"
                ).triggered.connect(lambda: self.name_port(key))
            else:
                menu.addAction(
                    icons.icon("add"), "Re-export as a point of this component"
                ).triggered.connect(lambda: self.name_point(key))
        menu.exec(self.tree.viewport().mapToGlobal(position))

    # -- the form ---------------------------------------------------------------

    def _show_form(self, key: Key | None) -> None:
        """Replace the form. The old one is deleted later: this may run from one of
        its own fields' events (Enter applies, the list is rebuilt)."""
        if self.form is not None:
            self.form_host.layout().removeWidget(self.form)
            self.form.hide()
            self.form.deleteLater()
        self.form = self._form(key)
        self.form_host.layout().addWidget(self.form)
        # Nothing chosen: one line of hint, and the list keeps the room.
        self.form_host.setMaximumHeight(HINT_HEIGHT_PX if key is None else QWIDGETSIZE_MAX)

    def _form(self, key: Key | None) -> QWidget:
        form = QWidget()
        layout = QFormLayout(form)
        layout.setContentsMargins(0, 0, 0, 0)
        if key is None:
            hint = QLabel("Select a point to see where it is.")
            hint.setObjectName("muted")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            return hint
        title = QLabel(reference(key))
        font = QFont(title.font())
        font.setBold(True)
        title.setFont(font)
        layout.addRow(title)
        if key[0] == "declared" and not self.document.read_only:
            self._declared_form(layout, key[1])
        elif key[0] == "port" and not self.document.read_only:
            self._port_form(layout, key[1])
        else:
            self._readonly_form(layout, key)
        return form

    def _declared_form(self, layout: QFormLayout, name: str) -> None:
        point = next(p for p in self.document.active_definition.points if p.name == name)
        scope = self.document.results.scope()
        choices = [""] + list(BBOX_POINTS)
        for (group, point_name), xy in self._positions.items():  # the shapes' points
            if group not in ("declared", "default"):
                choices.append(f"{group}.{point_name}")
                if xy is not None:
                    scope[f"{group}.{point_name}.x"], scope[f"{group}.{point_name}.y"] = xy
        at = QComboBox()
        at.setEditable(True)
        at.addItems(choices)
        at.setCurrentText(point.at or "")
        at.setToolTip("Measured from this point (empty: from the origin)")
        parameters = [p.name for p in self.document.active_definition.parameters]
        x = ValueEdit(point.x, scope, parameters, prefix="x")
        y = ValueEdit(point.y, scope, parameters, prefix="y")
        description = widgets.DescriptionEdit(point.description)
        description.setPlaceholderText("What it is for")

        def apply(*_) -> None:
            fields = {"at": at.currentText().strip() or None, "description": description.text()}
            try:
                fields |= {"x": x.value(), "y": y.value()}
            except ValueError as exc:
                self.error.emit(str(exc))
                return
            unchanged = all(getattr(point, k) == v for k, v in fields.items())
            if not unchanged and not self._guard(
                lambda: self.document.points.update(name, **fields)
            ):
                self.refresh()

        at.activated.connect(apply)
        at.lineEdit().returnPressed.connect(apply)
        for edit in (x, y):
            edit.returnPressed.connect(apply)
            edit.scrub_finished.connect(apply)
            self._offer_parameters(edit, f"{name}_{edit.prefix}", apply)
        description.editingFinished.connect(apply)
        layout.addRow("At", at)
        layout.addRow("X", x)
        layout.addRow("Y", y)
        layout.addRow("Description", description)
        layout.addRow("Position", QLabel(_position(self._positions.get(("declared", name)))))
        self.at_edit, self.x_edit, self.y_edit = at, x, y

    def _offer_parameters(self, edit: ValueEdit, suggestion: str, apply) -> None:
        """The field's "Make a parameter from this value": the parameter is made with
        the value as its default, then the field uses it."""

        def make(value) -> None:
            taken = {p.name for p in self.document.active_definition.parameters}
            try:
                name = ask_parameter_name(self, self.document.active, taken, value, suggestion)
            except ValueError as exc:
                self.error.emit(str(exc))
                return
            if name is not None and self._guard(lambda: self.document.parameters.set(name, value)):
                edit.setText(name)
                apply()

        edit.make_parameter.connect(make)

    def _port_form(self, layout: QFormLayout, name: str) -> None:
        port = next(p for p in self.document.active_definition.ports if p.name == name)
        scope = self.document.results.scope()
        choices = [""] + list(BBOX_POINTS)
        for (group, point_name), xy in self._positions.items():  # the shapes' points
            if group not in (*OWN, "default"):
                choices.append(f"{group}.{point_name}")
                if xy is not None and not point_name.startswith("port."):
                    scope[f"{group}.{point_name}.x"], scope[f"{group}.{point_name}.y"] = xy
        at = QComboBox()
        at.setEditable(True)
        at.addItems(choices)
        at.setCurrentText(port.at or "")
        at.setToolTip(
            "Measured from this point (empty: from the origin). At a placed component's "
            "port, it takes that port's direction, width and layer unless given here"
        )
        parameters = [p.name for p in self.document.active_definition.parameters]
        x = ValueEdit(port.x, scope, parameters, prefix="x")
        y = ValueEdit(port.y, scope, parameters, prefix="y")
        direction = QComboBox()
        direction.setEditable(True)
        direction.addItems(["", *DIRECTIONS])
        direction.setCurrentText("" if port.direction is None else str(port.direction))
        direction.setToolTip(
            "The way a wire leaves it: right, up, left, down, or an angle in degrees "
            "(counter-clockwise from +x; an expression). Empty: inherited, else right"
        )
        width = QLineEdit("" if port.width is None else str(port.width))
        width.setPlaceholderText("any")
        width.setToolTip(
            "The width of wire it expects, µm (an expression). Empty: inherited, else any"
        )
        layer = QComboBox()
        layer.setEditable(True)
        layer.addItems(
            ["", "level", "level+1", "level.anchor", *self.document.project.process.layers]
        )
        layer.setCurrentText(port.layer or "")
        layer.setToolTip("The layer the wire runs on, like a shape's. Empty: inherited, else level")
        description = widgets.DescriptionEdit(port.description)
        description.setPlaceholderText("What it is for")

        def number_or_text(text: str):
            text = text.strip()
            if not text:
                return None
            try:
                return float(text)
            except ValueError:
                return text

        def apply(*_) -> None:
            fields = {
                "at": at.currentText().strip() or None,
                "direction": number_or_text(direction.currentText()),
                "width": number_or_text(width.text()),
                "layer": layer.currentText().strip() or None,
                "description": description.text(),
            }
            try:
                fields |= {"x": x.value(), "y": y.value()}
            except ValueError as exc:
                self.error.emit(str(exc))
                return
            unchanged = all(getattr(port, k) == v for k, v in fields.items())
            if not unchanged and not self._guard(
                lambda: self.document.ports.update(name, **fields)
            ):
                self.refresh()

        for combo in (at, direction, layer):
            combo.activated.connect(apply)
            combo.lineEdit().returnPressed.connect(apply)
        for edit in (x, y):
            edit.returnPressed.connect(apply)
            edit.scrub_finished.connect(apply)
            self._offer_parameters(edit, f"{name}_{edit.prefix}", apply)
        width.editingFinished.connect(apply)
        description.editingFinished.connect(apply)
        layout.addRow("At", at)
        layout.addRow("X", x)
        layout.addRow("Y", y)
        layout.addRow("Direction", direction)
        layout.addRow("Width", width)
        layout.addRow("Layer", layer)
        layout.addRow("Description", description)
        layout.addRow("Position", QLabel(_position(self._positions.get(("port", name)))))
        self.at_edit, self.x_edit, self.y_edit = at, x, y
        self.direction_edit, self.width_edit, self.layer_edit = direction, width, layer

    def _readonly_form(self, layout: QFormLayout, key: Key) -> None:
        position = QLabel(_position(self._positions.get(key)))
        if key[0] in OWN:
            layout.addRow("Position", position)
            owned = self.document.active_definition.points + self.document.active_definition.ports
            item = next(p for p in owned if p.name == key[1])
            if item.description:
                layout.addRow("Description", QLabel(item.description))
            return
        explain = (
            "Every component has it, from the box around everything it draws."
            if key[0] == "default"
            else f"A point of shape {key[0]}."
        )
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(position, 1)
        line.addWidget(HelpButton(explain))
        layout.addRow("Position", row)
        if not self.document.read_only:
            if key[1].startswith("port."):  # a placed component's port: one of ours
                button = QPushButton(icons.icon("port"), "Re-export as a port")
                button.setToolTip(
                    "Make it a port of this component, at this one and with its direction, "
                    "width and layer"
                )
                button.clicked.connect(lambda: self.name_port(key))
            else:
                button = QPushButton(icons.icon("add"), "Re-export")
                button.setToolTip(
                    "Make it a point of this component, for whoever places it (measured from "
                    "this one, so it follows it)"
                )
                button.clicked.connect(lambda: self.name_point(key))
            layout.addRow(button)


def _own_point(name: str) -> bool:
    """Whether a shape's point is one of its own (a placed component's declared
    point), not of its box, nor a port (re-exported as a port instead)."""
    return name not in BBOX_POINTS and not name.startswith("port.")
