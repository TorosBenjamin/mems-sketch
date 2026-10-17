"""Property editor generated from a shape node's schema.

Numeric fields accept a number or an expression (see
:mod:`mems_sketch.gui.value_edit`: expressions show their value inside the
field, names are completed, and a button uses or makes parameters). Pairs such
as x and y sit side by side. The name is the panel's title, edited in place,
with an eye that switches the shape off or on at once. Long texts are cut
with "…" so they never widen the panel. For a component reference the
component's own parameter
schema is shown, with defaults as placeholders. Edits are applied with the
Apply button or Enter and go through the document, so invalid input is
rejected without changing the design.

Modifiers are cards, as in Blender: each has its own settings (applied with
the rest), and buttons that act at once: switch on or off, move up or down,
apply (turn into real shapes) and remove. "Add modifier" adds one at the end.
"""

from __future__ import annotations

import contextlib
import html
import re
import typing

import shiboken6
from PySide6.QtCore import QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.core.expressions import evaluate
from mems_sketch.core.levels import Stack
from mems_sketch.core.shapes import (
    MODIFIER_KINDS,
    CornersModifier,
    Modifier,
    NodePath,
    Shape,
)
from mems_sketch.editing import EditSession
from mems_sketch.gui import icons, theme
from mems_sketch.gui.actions import OPERATIONS, PRIMITIVES
from mems_sketch.gui.canvas import layer_color
from mems_sketch.gui.help import HelpButton
from mems_sketch.gui.panels import component_icon, parse_value, read_only_kind, swatch_icon
from mems_sketch.gui.settings import APPLICATION, ORGANIZATION
from mems_sketch.gui.value_edit import ElidedLineEdit, ExpandedEdit, ValueEdit

# Fields edited by dedicated widgets, or not at all (children are edited in the tree).
_SPECIAL = {
    "kind",
    "name",
    "enabled",
    "repeat",
    "modifiers",  # edited as cards
    "align",
    "params",
    "points",
    "mapping",
    "children",
    "a",
    "b",
}
_LABELS = {
    "x0": "x₀",
    "y0": "y₀",
    "x1": "x₁",
    "y1": "y₁",
    "mirror_x": "Mirror about x",
    "keep_unmapped": "Keep unmapped layers",
    "inner_radius": "Inner radius",
    "outer_radius": "Outer radius",
    "start_angle": "Start angle °",
    "end_angle": "End angle °",
    "rotation": "Rotation °",
    "op": "Operation",
    "level": "Level",
}
MODIFIER_TITLES = {
    "array": "Array",
    "polar_array": "Polar array",
    "mirror": "Mirror",
    "corners": "Corners",
    "perforate": "Perforate",
}
MODIFIER_LABELS = {  # per kind, then per field
    "array": {"columns": "Columns", "rows": "Rows", "dx": "Step x", "dy": "Step y"},
    "polar_array": {
        "count": "Copies",
        "x": "Centre x",
        "y": "Centre y",
        "step": "Angle step °",
        "rotate": "Turn the copies",
    },
    "mirror": {
        "about": "About",
        "axis": "Axis",
        "x": "Line at x",
        "y": "Line at y",
        "keep": "Keep the original",
    },
    "perforate": {
        "grid": "Grid",
        "pitch": "Pitch",
        "row_pitch": "Row pitch",
        "hole_size": "Hole size",
        "hole_height": "Hole height",
        "corner_radius": "Corner radius",
        "margin": "Margin",
        "keep_out": "Keep clear of",
        "keep_out_margin": "Keep-out margin",
    },
}
MODIFIER_TIPS = {
    "array": "Copies on a grid; i and j are each copy's column and row",
    "polar_array": "Copies around a centre (a full circle unless an angle step is given); "
    "i is each copy's index",
    "mirror": "The shape and its mirror image: across a vertical or horizontal line, across "
    "a guide, or through a point",
    "corners": "Chosen corners rounded or cut, each with its own radius: pick them on the "
    "canvas with the Corners tool (O)",
    "perforate": "Holes on a square or hex grid, only where a whole hole fits inside the "
    "margin and clear of the shapes to keep clear of; they refill when the shape changes",
}
SELF_POINTS = ("self.center", "self.left", "self.right", "self.top", "self.bottom")
# What modifier fields are offered of the node itself.
SELF_MEASURES = (
    "self.width",
    "self.height",
    *(f"{p}.{axis}" for p in SELF_POINTS for axis in "xy"),
    "self.min.angle",
    "self.min.width",
    "self.min.height",
)
# Fields shown as one row of two: (first, second) -> row label, inside prefixes.
PAIRS = {
    ("x0", "y0"): ("From", ("x", "y")),
    ("x1", "y1"): ("To", ("x", "y")),
    ("x", "y"): ("Position", ("x", "y")),
    ("dx", "dy"): ("Step", ("x", "y")),
    ("columns", "rows"): ("Grid", ("cols", "rows")),
}
PAIR_LABELS = {  # (kind, first field) -> row label, where the default does not fit
    ("guide", "x0"): "Start",
    ("guide", "x1"): "End",
    ("circle", "x"): "Centre",
    ("arc", "x"): "Centre",
    ("polar_array", "x"): "Centre",
    ("mirror", "x"): "Line at",
    ("perforate", "x"): "Hole at",
}


# Fields holding a count: dragged in whole numbers, never below 1.
COUNT_FIELDS = {"columns", "rows", "count", "segments", "turns", "fingers"}


class PropertyEditor(QScrollArea):
    error = Signal(str)
    applied = Signal()
    previewed = Signal(object)  # the node as the fields describe it, while a value is dragged
    pick_corners = Signal()  # "Pick on the canvas" in a corners card: the Corners tool
    # A pick button of the alignment: "point" or "to", with the point and target the
    # form has now (the other one is kept).
    pick_alignment = Signal(str, str, str)
    open_component = Signal(str)  # "Open …" by a placed component's kind

    def __init__(self, document: EditSession) -> None:
        super().__init__()
        self.document = document
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.path: NodePath | None = None
        self.hide_implementation = False  # a read-only component: show its interface
        self._editors: dict[str, typing.Callable[[], object]] = {}
        self._scope: dict[str, float] = {}
        self._offered: tuple[str, ...] = ()  # names completed besides those of the scope
        self._show_placeholder("Select a shape to edit its properties.")
        # Leaving a text field applies it, as Enter does: an edit is never lost by
        # clicking elsewhere (another field, the canvas, another shape).
        QApplication.instance().focusChanged.connect(self._focus_changed)

    # -- building ----------------------------------------------------------

    def show_node(self, path: NodePath | None) -> None:
        self.path = path
        if path is None and self.hide_implementation:
            self._show_interface()
            return
        if path is None:
            self._show_placeholder("Select a shape to edit its properties.")
            return
        try:
            node = self.document.node(path)
        except KeyError:
            self._show_placeholder("Select a shape to edit its properties.")
            return
        self._editors = {}
        try:
            self._scope = {**self.document.results.scope(path), "i": 0.0, "j": 0.0}
        except Exception:  # noqa: BLE001 - previews then show "?"
            self._scope = {}
        body = QWidget()
        body.setObjectName("properties-body")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)  # between the cards
        title = QHBoxLayout()
        title.setSpacing(6)
        glyph = QLabel()
        glyph.setPixmap(icons.pixmap(node.icon_name(), 18))
        name = ElidedLineEdit(
            node.name or "",
            tip="The shape's name: click to rename (alignments and expressions follow)",
        )
        name.setObjectName("title-edit")
        name.setPlaceholderText(f"unnamed {node.kind}")
        name.returnPressed.connect(self.apply)
        name.setReadOnly(self.document.read_only)
        name.setMinimumWidth(60)
        title.addWidget(glyph)
        title.addWidget(name, 1)
        title.addWidget(self._enabled_toggle(node))
        layout.addLayout(title)
        # What it is, in words ("Placed component", not "ref"), and for a placed
        # component a way to open it.
        what = QHBoxLayout()
        what.setContentsMargins(24, 0, 0, 0)
        what.setSpacing(6)
        kind = QLabel(kind_title(node))
        kind.setObjectName("muted")
        what.addWidget(kind)
        what.addStretch(1)
        component = getattr(node, "component", None)
        if component:
            opener = QToolButton()
            opener.setText(f"Open {component}")
            icons.bind(opener, "edit")
            opener.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            opener.setToolTip(f"Open {component} in its own tab")
            opener.setObjectName("open-component")
            opener.clicked.connect(lambda: self.open_component.emit(component))
            what.addWidget(opener)
        layout.addLayout(what)
        layout.addSpacing(4)
        self.name_edit = name
        self._editors["name"] = lambda: name.text().strip() or None
        main = _section("Placement" if type(node).placed else "Geometry", node.kind)
        form = _form(main)
        layout.addWidget(main)

        self._add_fields(form, node, node.kind, "", skip=_SPECIAL)

        fields = type(node).model_fields
        if form.rowCount() == 0:  # nothing of its own to set (e.g. an operation)
            main.hide()
        if "points" in fields:
            form.addRow("Points (x, y per line)", self._points_editor(node.points))
        if "mapping" in fields:
            form.addRow("Mapping (from → to)", self._mapping_editor(node.mapping))
        if "params" in fields:
            layout.addWidget(self._params_editor(node))
        layout.addWidget(self._align_editor(node, path))
        # Fields apply on Enter, lists and boxes when changed; only text boxes of
        # several lines and modifier stacks keep an Apply button.
        needs_apply = bool(node.modifiers) or "points" in fields or "mapping" in fields
        layout.addWidget(self._modifiers_editor(node, path, needs_apply))
        layout.addStretch()
        self._show(body)

    def _enabled_toggle(self, node: Shape) -> QToolButton:
        """The eye in the title row: whether the shape is drawn (applied at once)."""
        toggle = QToolButton()
        toggle.setObjectName("enabled-toggle")
        toggle.setCheckable(True)
        toggle.setChecked(node.enabled)
        toggle.setAutoRaise(True)
        toggle.setIconSize(QSize(16, 16))
        toggle.setEnabled(not self.document.read_only)

        def show(on: bool) -> None:
            icons.bind(toggle, "eye" if on else "eye_off")
            toggle.setToolTip(
                "Enabled: click to switch the shape off" if on else "Switched off: click to enable"
            )

        show(node.enabled)
        toggle.toggled.connect(show)
        toggle.toggled.connect(self.apply)
        self.enabled_toggle = toggle
        self._editors["enabled"] = toggle.isChecked
        return toggle

    def _field_editor(self, field: str, annotation, value) -> QWidget:
        args = typing.get_args(annotation)
        if typing.get_origin(annotation) is typing.Literal:
            combo = _combo()
            combo.addItems([str(a) for a in args])
            combo.setCurrentText(str(value))
            self._editors[field] = combo.currentText
            return self._applies(combo)
        if annotation is bool:
            box = QCheckBox()
            box.setChecked(bool(value))
            box.clicked.connect(self.apply)
            self._editors[field] = box.isChecked
            return box
        if field == "layer":
            combo = self._layer_picker(value)
            self._editors[field] = lambda: combo.currentData()
            return self._applies(combo)
        if field == "level":  # where a placed component is
            combo = _combo()
            combo.setEditable(True)
            levels = [lv.layer for lv in self.document.project.process.levels]
            combo.addItems(["", "level+1", "level-1", *levels] if levels else [""])
            combo.setCurrentText(value or "")
            combo.setToolTip(
                "The level of the layer stack it is placed on: a level, or one relative to "
                "this component's (level+1). Empty: its own default level, else this one's"
            )
            self._editors[field] = lambda: combo.currentText().strip() or None
            return self._applies(combo)
        if field == "component":
            combo = _combo()
            combo.addItems(self.document.component_names())
            combo.setCurrentText(value)
            self._editors[field] = combo.currentText
            return self._applies(combo)
        if typing.get_origin(annotation) is list and args == (str,):  # names, e.g. keep_out
            edit = QLineEdit(", ".join(value))
            edit.setPlaceholderText("shape names, comma-separated")
            edit.returnPressed.connect(self.apply)
            edit.setReadOnly(self.document.read_only)
            self._editors[field] = lambda: [n.strip() for n in edit.text().split(",") if n.strip()]
            return edit
        optional = type(None) in args
        return self._value_editor(field, value, optional)

    def _layer_picker(self, value: str) -> QComboBox:
        """The layers a shape can be on, each with the colour of the layer it lands
        on: relative ones first (``level+1 · metal``), then the layers by name."""
        project = self.document.project
        process = project.process
        stack = Stack.of(process.levels, process.default_level)
        definition = self.document.active_definition
        try:
            current = stack.top_level(getattr(definition, "default_level", None))
        except ValueError:
            current = None
        colors = {name: layer_color(i) for i, name in enumerate(project.layers)}
        combo = _combo()
        choices = [*layer_choices(process.levels), *project.layers]
        if value and value not in choices:  # kept as it is, whatever it is
            choices.append(value)
        for spec in choices:
            try:
                lands = stack.layer(spec, current)
            except ValueError:
                lands = None
            label = spec if lands in (None, spec) else f"{spec} · {lands}"
            color = colors.get(lands)
            icon = swatch_icon(color) if color is not None else icons.icon("layers")
            combo.addItem(icon, label, spec)
        combo.setCurrentIndex(max(combo.findData(value), 0))
        combo.setToolTip(
            "level: the component's level of the layer stack; level-1, level+1: the "
            "levels below and above; level.anchor: a layer of the level by role; or a "
            "layer by name. The colour is the layer it is on in this component's tab."
        )
        return combo

    def _applies(self, combo: QComboBox) -> QComboBox:
        """Apply when an item is chosen (or, when typed in, on Enter)."""
        combo.activated.connect(lambda _index: self.apply())
        combo.setEnabled(not self.document.read_only)
        return combo

    def _value_editor(
        self, field: str, value, optional: bool = False, prefix: str = ""
    ) -> ValueEdit:
        """A field for a number or an expression (see :class:`ValueEdit`)."""
        parameters = [p.name for p in self.document.active_definition.parameters]
        edit = ValueEdit(
            value, self._scope, parameters, prefix=prefix, optional=optional, offered=self._offered
        )
        if field.rpartition(":")[2] in COUNT_FIELDS:
            edit.integer, edit.minimum = True, 1
        edit.returnPressed.connect(self.apply)
        edit.scrubbed.connect(self._preview)
        edit.scrub_finished.connect(self.apply)  # one step to undo, however long the drag
        edit.make_parameter.connect(lambda v, e=edit: self._make_parameter(e, v))
        edit.setReadOnly(self.document.read_only)
        self._editors[field] = edit.value
        return edit

    def _add_fields(self, form: QFormLayout, model, kind: str, key: str, skip=()) -> dict:
        """A row per field of ``model`` (pairs such as x and y share one row).

        Readers are registered as ``key + field``; returns the rows' widgets by field.
        """
        fields = [(f, info) for f, info in type(model).model_fields.items() if f not in skip]
        names = [f for f, _ in fields]
        labels = MODIFIER_LABELS.get(kind, {}) if key else {}
        widgets: dict[str, QWidget] = {}
        done: set[str] = set()
        for field, info in fields:
            if field in done:
                continue
            pair = next(
                (p for p in PAIRS if p[0] == field and p[1] in names and _numeric(model, p)), None
            )
            if pair is not None:
                label, prefixes = PAIRS[pair]
                label = PAIR_LABELS.get((kind, field), label)
                row = QWidget()
                line = QHBoxLayout(row)
                line.setContentsMargins(0, 0, 0, 0)
                line.setSpacing(4)
                for name, prefix in zip(pair, prefixes, strict=True):
                    edit = self._value_editor(key + name, getattr(model, name), prefix=prefix)
                    line.addWidget(edit, 1)
                    widgets[name] = edit
                    done.add(name)
                form.addRow(label, row)
                continue
            label = labels.get(field) or _LABELS.get(field, field.replace("_", " ").capitalize())
            widget = self._field_editor(key + field, info.annotation, getattr(model, field))
            widgets[field] = widget
            form.addRow(_label(label), widget)
        return widgets

    def _make_parameter(self, edit: ValueEdit, value) -> None:
        """Make a parameter with this value as its default, and use it in the field."""
        suggestion = "param"
        name, ok = QInputDialog.getText(
            self,
            "Make a parameter",
            f"Name of the new parameter of {self.document.active} (default {_format(value)}):",
            text=suggestion,
        )
        name = name.strip()
        if not ok or not name:
            return
        if not name.isidentifier():
            self.error.emit(f"'{name}' is not a valid parameter name")
            return
        edit.setText(name)
        try:
            new = self._collect()  # read the form before the panel is rebuilt
            self.document.parameters.set(name, value)
            self.document.nodes.replace(self.path, new)
            self.applied.emit()
        except Exception as exc:  # noqa: BLE001 - reported to the user
            self.error.emit(_message(exc))

    def _points_editor(self, points) -> QWidget:
        edit = QPlainTextEdit("\n".join(f"{_format(x)}, {_format(y)}" for x, y in points))
        edit.setFixedHeight(110)

        def read():
            rows = [line.split(",") for line in edit.toPlainText().splitlines() if line.strip()]
            if any(len(r) != 2 for r in rows):
                raise ValueError("each point needs two comma-separated values")
            return [(parse_value(x), parse_value(y)) for x, y in rows]

        self._editors["points"] = read
        return edit

    def _mapping_editor(self, mapping: dict[str, str]) -> QWidget:
        edit = QPlainTextEdit("\n".join(f"{a} -> {b}" for a, b in mapping.items()))
        edit.setFixedHeight(80)

        def read():
            result = {}
            for line in edit.toPlainText().splitlines():
                if line.strip():
                    source, sep, target = line.partition("->")
                    if not sep:
                        raise ValueError("write each mapping as 'source -> target'")
                    result[source.strip()] = target.strip()
            return result

        self._editors["mapping"] = read
        return edit

    def _params_editor(self, node: Shape) -> QWidget:
        box = _section(f"Parameters of {node.component}", node.kind)
        form = _form(box)
        try:
            target = self.document.resolve(node.component)  # as the edited component sees it
            component = self.document.component(target)
            defaults = self.document.parameter_defaults(target)
        except KeyError:
            form.addRow(QLabel("Unknown component."))
            self._editors["params"] = lambda: node.params
            return box
        readers = {}
        # Internal parameters are not offered; one still set here (made internal
        # later) is shown marked, so the value can be cleared.
        schema = component.Params.model_fields
        shown = [f for f in schema if f not in component.internal or f in node.params]
        for field in shown:
            info = schema[field]
            current = node.params.get(field)
            default = defaults.get(field)
            if info.annotation is str:
                edit = QLineEdit("" if current is None else str(current))
                edit.setPlaceholderText(str(default))
                edit.returnPressed.connect(self.apply)
                readers[field] = lambda e=edit: e.text().strip() or None
                widget = edit
            else:
                widget = self._value_editor(f"param:{field}", current, optional=True)
                readers[field] = self._editors.pop(f"param:{field}")
                widget.setPlaceholderText(_format(default))
                widget.integer = info.annotation is int
                for limit in info.metadata:  # the schema's number limits
                    for bound in ("ge", "gt"):
                        if getattr(limit, bound, None) is not None:
                            widget.minimum = getattr(limit, bound)
                    for bound in ("le", "lt"):
                        if getattr(limit, bound, None) is not None:
                            widget.maximum = getattr(limit, bound)
            label = info.description or field
            if field in component.internal:
                label = f"{field} (internal)"
                widget.setProperty("invalid", True)
                widget.setToolTip(
                    f"'{field}' is internal to {node.component}: clear it (it cannot be set here)"
                )
            form.addRow(_label(label), widget)

        def read():
            values = {field: reader() for field, reader in readers.items()}
            return {field: value for field, value in values.items() if value is not None}

        self._editors["params"] = read
        box.setVisible(bool(shown))  # e.g. an imported cell has no parameters
        return box

    def _align_editor(self, node: Shape, path: NodePath) -> QWidget:
        box = _section("Align to another shape's point", node.kind)
        box.setCheckable(True)
        box.setChecked(node.align is not None)
        form = _form(box)
        align = node.align
        # Lists to choose from, opened by a click anywhere on them (an editable
        # combo box opens only from its arrow).
        own = _combo()
        own.addItems([name for name, _, _ in self.document.results.node_points(path)] or ["center"])
        _choose(own, align.point if align else "center")
        target = _combo()
        shape = None
        for name, *_ in self.document.results.align_targets(path):
            if shape is not None and name.split(".")[0] != shape:
                target.insertSeparator(target.count())  # the points of one shape together
            shape = name.split(".")[0]
            target.addItem(name)
        target.setPlaceholderText("choose a point")
        _choose(target, align.to if align else "")
        editable = not self.document.read_only

        def picker(field: str, tip: str) -> QToolButton:
            button = QToolButton()
            icons.bind(button, "point")
            button.setToolTip(tip)
            button.setEnabled(editable)
            button.clicked.connect(
                lambda: self.pick_alignment.emit(
                    field, own.currentText().strip(), target.currentText().strip()
                )
            )
            return button

        form.addRow(
            "Point",
            _with_button(self._applies(own), picker("point", "Pick the point on the canvas")),
        )
        explain = (
            "Moves the shape so that its *Point* lands on *To* (another shape's point), "
            "plus the offset, and keeps it there whenever anything changes."
        )
        if type(node).placed:
            explain += "\n\nWhile aligned, x and y do not move it; rotation and mirroring do."
        form.addRow(
            "To",
            _with_help(
                _with_button(
                    self._applies(target),
                    picker("to", "Pick the point to align to on the canvas"),
                ),
                explain,
            ),
        )
        # Switching alignment off applies at once; switching it on waits for a point.
        box.clicked.connect(lambda on: self.apply() if not on or target.currentText() else None)
        offsets = {}
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(4)
        for field in ("dx", "dy"):
            value = getattr(align, field) if align else 0.0
            line.addWidget(self._value_editor(f"align:{field}", value, prefix=field[1]), 1)
            offsets[field] = self._editors.pop(f"align:{field}")
        form.addRow("Offset", row)

        def read():
            if not box.isChecked():
                return None
            to = target.currentText().strip()
            if not to:
                raise ValueError("choose the point to align to")
            return {
                "point": own.currentText().strip() or "center",
                "to": to,
                **{field: reader() for field, reader in offsets.items()},
            }

        self._editors["align"] = read
        return box

    def _modifiers_editor(self, node: Shape, path: NodePath, with_apply: bool) -> QWidget:
        """The modifier stack: one card each, then "Add modifier" (and Apply, for the form)."""
        box = _section("Modifiers", node.kind)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 6, 0, 4)
        column.setSpacing(6)
        readers = []
        read_only = self.document.read_only
        for index, modifier in enumerate(node.modifiers):
            card, read = self._modifier_card(node, path, index, modifier, read_only)
            column.addWidget(card)
            readers.append(read)
        add = QToolButton()
        add.setText("Add modifier")
        icons.bind(add, "add")
        add.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        add.setEnabled(not read_only)
        menu = QMenu(add)
        for kind in MODIFIER_KINDS:
            name = kind.kind_name()
            action = menu.addAction(icons.icon(kind.icon), MODIFIER_TITLES.get(name, name))
            action.setToolTip(MODIFIER_TIPS.get(name, ""))
            action.triggered.connect(
                lambda _=False, k=name: self._act(lambda: self.document.modifiers.add(path, k))
            )
        add.setMenu(menu)
        self.add_modifier_menu = menu  # for tests
        row = QHBoxLayout()
        row.addWidget(add)
        row.addStretch()
        self.apply_button = None
        if with_apply:
            apply = QPushButton("Apply")
            apply.setDefault(True)
            apply.setToolTip("Apply the changed fields (Enter in a field does too)")
            apply.clicked.connect(self.apply)
            apply.setEnabled(not read_only)
            row.addWidget(apply)
            self.apply_button = apply
        column.addLayout(row)
        self._editors["modifiers"] = lambda: [read() for read in readers]
        return box

    def _modifier_card(self, node, path, index: int, modifier: Modifier, read_only: bool):
        kind = modifier.kind
        card = QFrame()
        card.setObjectName("modifier-card")
        card.setProperty("off", not modifier.enabled)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 4, 4, 6)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(1)
        glyph = QLabel()
        glyph.setPixmap(icons.pixmap(type(modifier).icon, 16))
        title = QLabel(MODIFIER_TITLES.get(kind, kind))
        title.setObjectName("card-title")
        title.setToolTip(MODIFIER_TIPS.get(kind, ""))
        summary = ElidedLabel(modifier.summary().removeprefix(kind.replace("_", " ")).strip())
        summary.setObjectName("muted")
        header.addWidget(glyph)
        header.addSpacing(4)
        header.addWidget(title)
        header.addSpacing(6)
        header.addWidget(summary, 1)
        last = len(node.modifiers) - 1
        modifiers = self.document.modifiers
        for icon, tip, enabled, action in (
            (
                "eye" if modifier.enabled else "eye_off",
                "Switch off" if modifier.enabled else "Switch on",
                True,
                lambda: modifiers.set_enabled(path, index, not modifier.enabled),
            ),
            (
                "up",
                "Move up (applied earlier)",
                index > 0,
                lambda: modifiers.move(path, index, index - 1),
            ),
            (
                "down",
                "Move down (applied later)",
                index < last,
                lambda: modifiers.move(path, index, index + 1),
            ),
            (
                "apply",
                "Apply: turn it into real shapes (only the first modifier)",
                index == 0,
                lambda: modifiers.apply(path),
            ),
            ("close", "Remove", True, lambda: modifiers.remove(path, index)),
        ):
            button = QToolButton()
            icons.bind(button, icon)
            button.setIconSize(QSize(14, 14))
            button.setAutoRaise(True)
            button.setToolTip(tip)
            button.setEnabled(enabled and not read_only)
            button.clicked.connect(lambda _=False, a=action: self._act(a))
            header.addWidget(button)
        layout.addLayout(header)

        form = _form()
        form.setContentsMargins(0, 0, 4, 0)  # the fields may reach under the icon
        labels = MODIFIER_LABELS.get(kind, {})
        key = f"modifier{index}:"
        widgets: dict[str, QWidget] = {}
        if "about" in type(modifier).model_fields:  # first: it replaces the axis fields below
            widgets["about"] = self._about_editor(key + "about", modifier.about, path)
            form.addRow(labels.get("about", "About"), widgets["about"])
        skip = ("kind", "enabled", "about", "corners")
        scope, self._offered = self._scope, SELF_MEASURES
        try:
            self._scope = {**scope, **self.document.modifiers.self_values(path, index)}
        except Exception:  # noqa: BLE001, S110 - previews then show "?"
            pass
        try:
            widgets |= self._add_fields(form, modifier, kind, key, skip=skip)
        finally:
            self._scope, self._offered = scope, ()
        if isinstance(modifier, CornersModifier):
            layout.addWidget(self._corners_editor(key, modifier, path, read_only))
        fields = {
            k[len(key) :]: self._editors.pop(k) for k in list(self._editors) if k.startswith(key)
        }
        layout.addLayout(form)
        if kind == "mirror":  # a line or point given by "about" replaces axis, x and y
            about = widgets["about"].findChild(QComboBox) or widgets["about"]

            def update_axis(text: str) -> None:
                for field in ("axis", "x", "y"):
                    widget = widgets[field]
                    row = widget if form.labelForField(widget) else widget.parentWidget()
                    row.setEnabled(not text.strip())
                    label = form.labelForField(row)
                    if label is not None:
                        label.setEnabled(not text.strip())

            about.currentTextChanged.connect(update_axis)
            update_axis(about.currentText())

        def read() -> dict:
            return {"kind": kind, "enabled": modifier.enabled} | {
                field: reader() for field, reader in fields.items()
            }

        return card, read

    def _corners_editor(
        self, key: str, modifier: CornersModifier, path: NodePath, read_only: bool
    ) -> QWidget:
        """A row per corner (where it is, its radius and style), and "Pick on the canvas"."""
        box = QWidget()
        rows = QVBoxLayout(box)
        rows.setContentsMargins(0, 0, 4, 0)
        rows.setSpacing(3)
        readers = []
        for index, corner in enumerate(modifier.corners):
            line = QHBoxLayout()
            line.setSpacing(4)
            where = ElidedLabel(corner.where())
            written = corner.at or f"x {corner.x}, y {corner.y}"
            where.setToolTip(f"Where the corner is: {written}")
            where.setMinimumWidth(40)
            radius = self._value_editor(f"{key}corner{index}", corner.radius, prefix="r")
            reader = self._editors.pop(f"{key}corner{index}")
            style = _combo()
            style.addItems(["round", "chamfer"])
            style.setCurrentText(corner.style)
            style.setEnabled(not read_only)
            style.activated.connect(lambda _=0: self.apply())
            remove = QToolButton()
            icons.bind(remove, "close")
            remove.setIconSize(QSize(12, 12))
            remove.setAutoRaise(True)
            remove.setToolTip("Make this corner sharp again")
            remove.setEnabled(not read_only)
            remove.clicked.connect(
                lambda _=False, i=index: self._act(lambda: self.document.corners.remove(path, i))
            )
            line.addWidget(where, 2)
            line.addWidget(radius, 2)
            line.addWidget(style, 1)
            line.addWidget(remove)
            rows.addLayout(line)
            readers.append(
                lambda c=corner, r=reader, s=style: {
                    **c.model_dump(),
                    "radius": r(),
                    "style": s.currentText(),
                }
            )
        if not modifier.corners:
            none = QLabel("No corners yet.")
            none.setObjectName("muted")
            rows.addWidget(none)
        pick = QToolButton()
        pick.setText("Pick on the canvas")
        icons.bind(pick, "fillet")
        pick.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        pick.setToolTip("The Corners tool (O): click a corner to round it, drag to set its radius")
        pick.setEnabled(not read_only)
        pick.clicked.connect(self.pick_corners)
        rows.addWidget(pick)
        self._editors[key + "corners"] = lambda: [read() for read in readers]
        return box

    def _about_editor(self, key: str, value: str | None, path: NodePath) -> QWidget:
        """Where a mirror mirrors: nothing (use the axis), a guide, or a point."""
        combo = _combo()
        combo.setEditable(True)
        combo.addItem("")
        guides = [name for p, name, *_ in self.document.results.guides() if p != path]
        combo.addItems(guides)
        points = [name for name, *_ in self.document.results.align_targets(path)]
        combo.addItems([*SELF_POINTS, *points])
        combo.setCurrentText(value or "")
        self._applies(combo)
        combo.lineEdit().setPlaceholderText("the axis below")
        combo.setToolTip("A guide's name (mirror across it), or a point (mirror through it)")
        self._editors[key] = lambda: combo.currentText().strip() or None
        return combo

    def _act(self, action) -> None:
        """Run a modifier button's command; problems are reported, not raised."""
        try:
            action()
            self.applied.emit()
        except Exception as exc:  # noqa: BLE001 - reported to the user
            self.error.emit(_message(exc))

    def _show_interface(self) -> None:
        """What a component offers whoever places it, as text: its description,
        public parameters and declared points (for one that cannot be edited)."""
        name = self.document.active
        definition = self.document.definition_of(name)
        try:
            values = self.document.results.scope()
        except Exception:  # noqa: BLE001 - values then show "?"
            values = {}
        try:
            points = self.document.results.declared_points()
        except Exception:  # noqa: BLE001
            points = {}
        body = QWidget()
        body.setObjectName("properties-body")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(4)
        title = QHBoxLayout()
        title.setSpacing(6)
        glyph = QLabel()
        what = read_only_kind(self.document.project, name)
        glyph.setPixmap(icons.pixmap(component_icon(self.document.project, name), 18))
        label = ElidedLabel(name.rpartition(".")[2])
        label.setObjectName("card-title")
        kind = QLabel(f"{what} component")
        kind.setObjectName("muted")
        title.addWidget(glyph)
        title.addWidget(label, 1)
        title.addWidget(kind)
        explain = (
            "You see its interface: what you can set and align to when you place it. "
            "Its shapes are how it is built, like the inside of a library in code: "
            "View › Show implementation of read-only components shows them.\n\n"
            "Try other values in the Parameters panel (Trial); copy it into the "
            "project to change it."
        )
        if what == "imported":
            imported = self.document.project.imports[name]
            explain = (
                f"Cell *{imported.cell}* of *{imported.file}*, imported from GDS: fixed "
                "geometry, placed like any component (align to its center, top_left, …).\n\n"
                "Right-click it in Components to re-import a newer file; every placement "
                "follows."
            )
        title.addWidget(HelpButton(explain))
        layout.addLayout(title)
        if definition.description:
            about = QLabel(definition.description)
            about.setWordWrap(True)
            layout.addWidget(about)
        parameters = [p for p in definition.parameters if not p.internal]
        box = _section("Parameters")
        form = _form(box)
        for parameter in parameters:
            value = values.get(parameter.name)
            text = _format(parameter.default)
            if isinstance(parameter.default, str) and value is not None:
                text += f"  ({value:g})"
            limits = [
                _limit(parameter.min, ">" if parameter.min_exclusive else "≥", values),
                _limit(parameter.max, "<" if parameter.max_exclusive else "≤", values),
                "whole number" if parameter.integer else "",
            ]
            limits = ", ".join(t for t in limits if t)
            shown = ElidedLabel(f"{text}    {limits}" if limits else text)
            shown.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            shown.setToolTip(parameter.description or parameter.name)
            form.addRow(parameter.name, shown)
        if not parameters:
            form.addRow(QLabel("None."))
        layout.addWidget(box)
        if points:
            box = _section("Points")
            form = _form(box)
            for point, (x, y) in points.items():
                form.addRow(point, QLabel(f"x {x:g}, y {y:g} µm"))
            layout.addWidget(box)
        layout.addStretch()
        self._show(body)

    def _show(self, content: QWidget) -> None:
        """Replace the panel's content. The old content is deleted later, not now: the
        change may come from one of its own fields or buttons (Enter in a field,
        the eye), which Qt is still delivering an event to."""
        old = self.takeWidget()
        if old is not None:
            old.hide()
            old.deleteLater()
        self.setWidget(content)

    def _show_placeholder(self, text: str) -> None:
        body = QWidget()
        body.setObjectName("properties-body")  # the island's colour, as with a shape shown
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setObjectName("muted")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(label)
        self._show(body)

    # -- applying ----------------------------------------------------------

    def _collect(self) -> Shape:
        """The node as the form describes it (validated)."""
        node = self.document.node(self.path)
        data = node.model_dump()
        for field, read in self._editors.items():
            data[field] = read()
        return type(node).model_validate(data)

    def _preview(self) -> None:
        """Show what the fields describe, without applying it (while a value is dragged)."""
        if self.path is None:
            return
        try:
            self.previewed.emit(self._collect())
        except Exception:  # noqa: BLE001, S110 - not valid yet: keep the last preview
            pass

    def _focus_changed(self, old: QWidget | None, new: QWidget | None) -> None:
        """A text field of the form lost focus: apply what it says, if it changed."""
        body = self.widget()
        if (
            not isinstance(old, QLineEdit)
            or not shiboken6.isValid(old)
            or body is None
            or not body.isAncestorOf(old)
            or old.isReadOnly()
            or self.path is None
        ):
            return
        if isinstance(new, ExpandedEdit) or getattr(old, "expanded", None) is not None:
            return  # into the field's wider editor: still editing
        path = self.path
        try:
            node = self._collect()
        except Exception as exc:  # noqa: BLE001 - reported, as Enter would
            self.error.emit(_message(exc))
            return
        with contextlib.suppress(KeyError):
            if node == self.document.node(path):
                return
        # The field the focus went to, by its place in the form: the form is built
        # again once applied, and the focus goes back to the same place.
        fields = _focusable(body)
        at = fields.index(new) if new in fields else None
        # After the focus has moved, not inside the focus change being delivered.
        QTimer.singleShot(0, lambda: self._apply_left(path, node, at))

    def _apply_left(self, path: NodePath, node: Shape, at: int | None) -> None:
        try:
            self.document.nodes.replace(path, node)
        except Exception as exc:  # noqa: BLE001 - reported to the user
            self.error.emit(_message(exc))
            return
        self.applied.emit()
        body = self.widget()
        if at is not None and self.path == path and body is not None:
            fields = _focusable(body)
            if at < len(fields):
                fields[at].setFocus(Qt.FocusReason.OtherFocusReason)

    def apply(self) -> None:
        if self.path is None:
            return
        try:
            self.document.nodes.replace(self.path, self._collect())
            self.applied.emit()
        except Exception as exc:  # noqa: BLE001 - reported to the user
            self.error.emit(_message(exc))


class ElidedLabel(QLabel):
    """A one-line label that may shrink: a text too long is cut with "…" (the whole
    text is in the tooltip)."""

    def __init__(self, text: str = "") -> None:
        super().__init__()
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._elide()

    def text(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, self.width())
        super().setText(shown)


def _focusable(body: QWidget) -> list[QWidget]:
    """The widgets of a form that take the focus, in the order they were made."""
    return [
        w
        for w in body.findChildren(QWidget)
        if w.focusPolicy() != Qt.FocusPolicy.NoFocus and w.isVisible()
    ]


def _combo() -> QComboBox:
    """A combo box that may shrink below its longest item (long names never widen
    the panel; the list still opens wide enough)."""
    combo = QComboBox()
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(6)
    combo.view().setTextElideMode(Qt.TextElideMode.ElideNone)
    return combo


def _choose(combo: QComboBox, value: str) -> None:
    """Select ``value`` in a list, adding it if it is not there (e.g. a point that is
    gone, so that it shows); nothing is selected for an empty value."""
    if not value:
        combo.setCurrentIndex(-1)
        return
    if combo.findText(value) < 0:
        combo.addItem(value)
    combo.setCurrentText(value)


KIND_TITLES = (
    {"ref": "Placed component", "boolean": "Boolean"} | dict(PRIMITIVES) | dict(OPERATIONS)
)


def kind_title(node: Shape) -> str:
    """A shape's kind in words: "Rectangle", "Placed component", "Subtract"."""
    op = getattr(node, "op", None)  # a boolean says which operation it is
    title = KIND_TITLES.get(op) or KIND_TITLES.get(node.kind)
    return title or node.kind.replace("_", " ").capitalize()


class Section(QGroupBox):
    """A card of the form (the geometry, a component's parameters, the alignment, the
    modifiers): its own background and a bold heading, and a chevron that folds it
    away. Folded cards are remembered per kind of shape (``key``), between sessions."""

    def __init__(self, title: str, key: str = "") -> None:
        super().__init__(title)
        self.setObjectName("section")
        self.key = key
        self._collapsed = False
        self._toggle = QToolButton(self)
        self._toggle.setObjectName("section-toggle")
        self._toggle.setAutoRaise(True)
        self._toggle.setIconSize(QSize(12, 12))
        self._toggle.clicked.connect(lambda: self.set_collapsed(not self._collapsed))
        self._restored = False
        self._show_toggle()

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool, remember: bool = True) -> None:
        self._collapsed = collapsed
        for child in self.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            if child is not self._toggle:
                child.setVisible(not collapsed)
        self.setMaximumHeight(SECTION_FOLDED_PX if collapsed else QWIDGETSIZE_MAX)
        self._show_toggle()
        if remember and self.key:
            folded = set(_settings().value(FOLDED_KEY, [], type=list) or [])
            folded = folded | {self.key} if collapsed else folded - {self.key}
            _settings().setValue(FOLDED_KEY, sorted(folded))

    def _show_toggle(self) -> None:
        icons.bind(self._toggle, "down" if self._collapsed else "up")
        self._toggle.setToolTip("Show this section" if self._collapsed else "Fold this section")

    def showEvent(self, event) -> None:
        if not self._restored:  # its contents are made by now
            self._restored = True
            folded = _settings().value(FOLDED_KEY, [], type=list) or []
            if self.key in folded:
                self.set_collapsed(True, remember=False)
        super().showEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._toggle.adjustSize()
        self._toggle.move(self.width() - self._toggle.width() - 6, 5)
        self._toggle.raise_()


SECTION_FOLDED_PX = 30  # a folded card: just its heading
FOLDED_KEY = "properties/folded"  # the folded cards, "kind/section"
QWIDGETSIZE_MAX = (1 << 24) - 1


def _settings() -> QSettings:
    return QSettings(ORGANIZATION, APPLICATION)


def _section(title: str, kind: str = "") -> Section:
    """A card of the form (see :class:`Section`); ``kind`` remembers it folded."""
    return Section(title, f"{kind}/{title.split(' of ')[0]}" if kind else "")


def _label(text: str) -> QLabel:
    """A field's label, its unit in brackets ("Finger width (µm)") in a lighter colour."""
    match = re.fullmatch(r"(.*?)\s*\(([^()]*)\)", text)
    label = QLabel(text)
    if match:
        label.setTextFormat(Qt.TextFormat.RichText)
        muted = theme.color("muted").name()
        label.setText(
            f"{html.escape(match[1])} <span style='color:{muted}'>{html.escape(match[2])}</span>"
        )
        label.setToolTip(text)
    return label


def _form(parent: QWidget | None = None) -> QFormLayout:
    form = QFormLayout(parent) if parent is not None else QFormLayout()
    form.setHorizontalSpacing(10)
    form.setVerticalSpacing(5)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    if parent is not None:
        form.setContentsMargins(0, 4, 0, 4)
    return form


def _numeric(model, pair) -> bool:
    """Both fields of a pair hold values (not e.g. a mirror's axis letter)."""
    return all(isinstance(getattr(model, f), float | int | str) for f in pair) and not any(
        f == "axis" for f in pair
    )


def _format(value) -> str:
    return f"{value:g}" if isinstance(value, float | int) else str(value)


def layer_choices(levels) -> list[str]:
    """The relative layers a shape can be on with this layer stack."""
    if not levels:
        return []
    roles = sorted({role for level in levels for role in level.roles})
    return ["level", "level+1", "level-1", *(f"level.{role}" for role in roles)]


def _limit(limit, relation: str, values: dict[str, float]) -> str:
    """``≤ size / 2 = 20``: a parameter limit as shown, evaluated if an expression."""
    if limit is None:
        return ""
    text = f"{relation} {_format(limit)}"
    if isinstance(limit, str):
        with contextlib.suppress(ValueError):
            text += f" = {evaluate(limit, values):g}"
    return text


def _message(exc: Exception) -> str:
    """A readable message; validation errors list each field with its problem."""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        with contextlib.suppress(Exception):  # not a pydantic error after all
            return "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" if e["loc"] else e["msg"]
                for e in errors()
            )
    return str(exc)


def _with_button(widget: QWidget, button: QToolButton) -> QWidget:
    """``widget`` with a small button after it."""
    row = QWidget()
    line = QHBoxLayout(row)
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(2)
    line.addWidget(widget, 1)
    line.addWidget(button)
    return row


def _with_help(widget: QWidget, text: str) -> QWidget:
    """``widget`` with a "?" after it that explains ``text``."""
    row = QWidget()
    line = QHBoxLayout(row)
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(2)
    line.addWidget(widget, 1)
    line.addWidget(HelpButton(text))
    return row
