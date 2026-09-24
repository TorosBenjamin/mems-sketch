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
import typing

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
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

from mems_sketch.core.shapes import MODIFIER_KINDS, Modifier, NodePath, Shape
from mems_sketch.editing import EditSession
from mems_sketch.gui import icons
from mems_sketch.gui.panels import parse_value
from mems_sketch.gui.value_edit import ElidedLineEdit, ValueEdit

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
}
MODIFIER_TITLES = {"array": "Array", "polar_array": "Polar array", "mirror": "Mirror"}
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
}
MODIFIER_TIPS = {
    "array": "Copies on a grid; i and j are each copy's column and row",
    "polar_array": "Copies around a centre (a full circle unless an angle step is given); "
    "i is each copy's index",
    "mirror": "The shape and its mirror image: across a vertical or horizontal line, across "
    "a guide, or through a point",
}
SELF_POINTS = ("self.center", "self.left", "self.right", "self.top", "self.bottom")
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
}


class PropertyEditor(QScrollArea):
    error = Signal(str)
    applied = Signal()

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
        self._show_placeholder("Select a shape to edit its properties.")

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
        layout.setSpacing(4)
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
        kind = QLabel(node.kind)
        kind.setObjectName("muted")
        title.addWidget(glyph)
        title.addWidget(name, 1)
        title.addWidget(kind)
        title.addWidget(self._enabled_toggle(node))
        layout.addLayout(title)
        self.name_edit = name
        self._editors["name"] = lambda: name.text().strip() or None
        form = _form()
        layout.addLayout(form)

        self._add_fields(form, node, node.kind, "", skip=_SPECIAL)

        fields = type(node).model_fields
        if "points" in fields:
            form.addRow("Points (x, y per line)", self._points_editor(node.points))
        if "mapping" in fields:
            form.addRow("Mapping (from → to)", self._mapping_editor(node.mapping))
        if "params" in fields:
            layout.addWidget(self._params_editor(node))
        layout.addWidget(self._align_editor(node, path))
        layout.addWidget(self._modifiers_editor(node, path))

        apply = QPushButton("Apply")
        apply.setDefault(True)
        apply.clicked.connect(self.apply)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(apply)
        layout.addLayout(row)
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
            return combo
        if annotation is bool:
            box = QCheckBox()
            box.setChecked(bool(value))
            self._editors[field] = box.isChecked
            return box
        if field == "layer":
            combo = _combo()
            combo.setEditable(True)
            combo.addItems(list(self.document.project.layers))
            combo.setCurrentText(value)
            self._editors[field] = lambda: combo.currentText().strip()
            return combo
        if field == "component":
            combo = _combo()
            combo.addItems(self.document.component_names())
            combo.setCurrentText(value)
            self._editors[field] = combo.currentText
            return combo
        optional = type(None) in args
        return self._value_editor(field, value, optional)

    def _value_editor(
        self, field: str, value, optional: bool = False, prefix: str = ""
    ) -> ValueEdit:
        """A field for a number or an expression (see :class:`ValueEdit`)."""
        parameters = [p.name for p in self.document.active_definition.parameters]
        edit = ValueEdit(value, self._scope, parameters, prefix=prefix, optional=optional)
        edit.returnPressed.connect(self.apply)
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
            form.addRow(label, widget)
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
        box = _section(f"Parameters of {node.component}")
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
                readers[field] = lambda e=edit: e.text().strip() or None
                widget = edit
            else:
                widget = self._value_editor(f"param:{field}", current, optional=True)
                readers[field] = self._editors.pop(f"param:{field}")
                widget.setPlaceholderText(_format(default))
            label = info.description or field
            if field in component.internal:
                label = f"{field} (internal)"
                widget.setProperty("invalid", True)
                widget.setToolTip(
                    f"'{field}' is internal to {node.component}: clear it (it cannot be set here)"
                )
            form.addRow(label, widget)

        def read():
            values = {field: reader() for field, reader in readers.items()}
            return {field: value for field, value in values.items() if value is not None}

        self._editors["params"] = read
        return box

    def _align_editor(self, node: Shape, path: NodePath) -> QWidget:
        box = _section("Align to another shape's point")
        box.setCheckable(True)
        box.setChecked(node.align is not None)
        form = _form(box)
        align = node.align
        own = _combo()
        own.setEditable(True)
        own.addItems([name for name, _, _ in self.document.results.node_points(path)] or ["center"])
        own.setCurrentText(align.point if align else "center")
        target = _combo()
        target.setEditable(True)
        target.addItems([name for name, *_ in self.document.results.align_targets(path)])
        target.setCurrentText(align.to if align else "")
        form.addRow("Point", own)
        form.addRow("To", target)
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
        if type(node).placed:
            note = QLabel("While aligned, x and y do not move it; rotation and mirroring do.")
            note.setWordWrap(True)
            note.setObjectName("muted")
            form.addRow(note)

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

    def _modifiers_editor(self, node: Shape, path: NodePath) -> QWidget:
        """The modifier stack: one card each, and "Add modifier"."""
        box = _section("Modifiers")
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
        widgets |= self._add_fields(form, modifier, kind, key, skip=("kind", "enabled", "about"))
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
        library = "." in name
        glyph.setPixmap(icons.pixmap("component_library" if library else "component_builtin", 18))
        label = ElidedLabel(name.rpartition(".")[2])
        label.setObjectName("card-title")
        kind = QLabel("library component" if library else "built-in component")
        kind.setObjectName("muted")
        title.addWidget(glyph)
        title.addWidget(label, 1)
        title.addWidget(kind)
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
                f"≥ {parameter.min:g}" if parameter.min is not None else "",
                f"≤ {parameter.max:g}" if parameter.max is not None else "",
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
        note = QLabel(
            "Its shapes are how it is built: View › Show implementation of read-only "
            "components shows them. Try other values in the Parameters panel (Trial)."
        )
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
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
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setObjectName("muted")
        self._show(label)

    # -- applying ----------------------------------------------------------

    def _collect(self) -> Shape:
        """The node as the form describes it (validated)."""
        node = self.document.node(self.path)
        data = node.model_dump()
        for field, read in self._editors.items():
            data[field] = read()
        return type(node).model_validate(data)

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


def _combo() -> QComboBox:
    """A combo box that may shrink below its longest item (long names never widen
    the panel; the list still opens wide enough)."""
    combo = QComboBox()
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(6)
    combo.view().setTextElideMode(Qt.TextElideMode.ElideNone)
    return combo


def _section(title: str) -> QGroupBox:
    """A titled section: flat, with one divider above it (see the theme)."""
    box = QGroupBox(title)
    box.setObjectName("section")
    return box


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
