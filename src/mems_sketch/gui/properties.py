"""Property editor generated from a shape node's schema.

Numeric fields accept a number or an expression; the evaluated value is shown
next to the field. For a component reference the component's own parameter
schema is shown, with defaults as placeholders. Edits are applied with the
Apply button or Enter and go through the document, so invalid input is
rejected without changing the design.
"""

from __future__ import annotations

import contextlib
import typing

import klayout.db as kdb
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.core.expressions import evaluate
from mems_sketch.core.shapes import NodePath, Shape
from mems_sketch.gui.document import ProjectDocument
from mems_sketch.gui.panels import parse_value

# Fields edited by dedicated widgets, or not at all (children are edited in the tree).
_SPECIAL = {
    "kind",
    "name",
    "enabled",
    "repeat",
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


class PropertyEditor(QScrollArea):
    error = Signal(str)
    applied = Signal()

    def __init__(self, document: ProjectDocument) -> None:
        super().__init__()
        self.document = document
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.path: NodePath | None = None
        self._editors: dict[str, typing.Callable[[], object]] = {}
        self._scope: dict[str, float] = {}
        self._show_placeholder("Select a shape to edit its properties.")

    # -- building ----------------------------------------------------------

    def show_node(self, path: NodePath | None) -> None:
        self.path = path
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
            self._scope = {**self.document.scope(path), "i": 0.0, "j": 0.0}
        except Exception:  # noqa: BLE001 - previews then show "?"
            self._scope = {}
        body = QWidget()
        body.setObjectName("properties-body")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(4)
        form = _form()
        layout.addLayout(form)
        kind = QLabel(node.kind)
        kind.setObjectName("heading")
        form.addRow(kind)

        name = QLineEdit(node.name or "")
        name.returnPressed.connect(self.apply)
        form.addRow("Name", name)
        self._editors["name"] = lambda: name.text().strip() or None
        enabled = QCheckBox()
        enabled.setChecked(node.enabled)
        form.addRow("Enabled", enabled)
        self._editors["enabled"] = enabled.isChecked
        extent = self.document.highlight([path])
        if extent is not None:
            box = kdb.Box()
            for region in extent.layers.values():
                box += region.bbox()
            where = QLabel(
                "x {:g} … {:g}, y {:g} … {:g} µm".format(
                    *(v / 1000 for v in (box.left, box.right, box.bottom, box.top))
                )
            )
            where.setObjectName("muted")
            where.setToolTip("Where the shape ends up in this component (stored values are local)")
            form.addRow("Extent", where)

        for field, info in type(node).model_fields.items():
            if field in _SPECIAL:
                continue
            label = _LABELS.get(field, field.replace("_", " ").capitalize())
            form.addRow(label, self._field_editor(field, info.annotation, getattr(node, field)))

        if node.kind in ("polygon", "path"):
            form.addRow("Points (x, y per line)", self._points_editor(node.points))
        if node.kind == "layer_map":
            form.addRow("Mapping (from → to)", self._mapping_editor(node.mapping))
        if node.kind == "ref":
            layout.addWidget(self._params_editor(node))
        layout.addWidget(self._align_editor(node, path))
        layout.addWidget(self._repeat_editor(node))

        apply = QPushButton("Apply")
        apply.setDefault(True)
        apply.clicked.connect(self.apply)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(apply)
        layout.addLayout(row)
        layout.addStretch()
        self.setWidget(body)

    def _field_editor(self, field: str, annotation, value) -> QWidget:
        args = typing.get_args(annotation)
        if typing.get_origin(annotation) is typing.Literal:
            combo = QComboBox()
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
            combo = QComboBox()
            combo.setEditable(True)
            combo.addItems(list(self.document.project.layers))
            combo.setCurrentText(value)
            self._editors[field] = lambda: combo.currentText().strip()
            return combo
        if field == "component":
            combo = QComboBox()
            combo.addItems(self.document.component_names())
            combo.setCurrentText(value)
            self._editors[field] = combo.currentText
            return combo
        optional = type(None) in args
        return self._value_editor(field, value, optional)

    def _value_editor(self, field: str, value, optional: bool = False) -> QWidget:
        """A line edit for a number or expression, with the evaluated value beside it."""
        edit = QLineEdit("" if value is None else _format(value))
        edit.returnPressed.connect(self.apply)
        result = QLabel()
        result.setMinimumWidth(54)
        result.setObjectName("muted")

        def update_result() -> None:
            text = edit.text().strip()
            if not text:
                result.setText("default" if optional else "")
                return
            try:
                result.setText(f"= {evaluate(parse_value(text), self._scope):g}")
            except Exception:  # noqa: BLE001 - only a preview
                result.setText("?")

        edit.textChanged.connect(update_result)
        update_result()

        def read():
            text = edit.text().strip()
            if not text and optional:
                return None
            return parse_value(text)

        self._editors[field] = read
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit, 1)
        row.addWidget(result)
        return container

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
            schema = self.document.component(node.component).Params.model_fields
            defaults = self.document.parameter_defaults(node.component)
        except KeyError:
            form.addRow(QLabel("Unknown component."))
            self._editors["params"] = lambda: node.params
            return box
        readers = {}
        for field, info in schema.items():
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
                widget.findChild(QLineEdit).setPlaceholderText(_format(default))
            form.addRow(info.description or field, widget)

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
        own = QComboBox()
        own.setEditable(True)
        own.addItems([name for name, _, _ in self.document.node_points(path)] or ["center"])
        own.setCurrentText(align.point if align else "center")
        target = QComboBox()
        target.setEditable(True)
        target.addItems([name for name, *_ in self.document.align_targets(path)])
        target.setCurrentText(align.to if align else "")
        form.addRow("Point", own)
        form.addRow("To", target)
        offsets = {}
        for field in ("dx", "dy"):
            value = getattr(align, field) if align else 0.0
            form.addRow(f"Offset {field[1]}", self._value_editor(f"align:{field}", value))
            offsets[field] = self._editors.pop(f"align:{field}")
        if node.kind in ("ref", "transform"):
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

    def _repeat_editor(self, node: Shape) -> QWidget:
        box = _section("Repeat on grid (index i, j)")
        box.setCheckable(True)
        box.setChecked(node.repeat is not None)
        form = _form(box)
        repeat = node.repeat
        fields = {}
        for field, default in (("columns", 1), ("rows", 1), ("dx", 0.0), ("dy", 0.0)):
            value = getattr(repeat, field) if repeat else default
            form.addRow(field, self._value_editor(f"repeat:{field}", value))
            fields[field] = self._editors.pop(f"repeat:{field}")

        def read():
            if not box.isChecked():
                return None
            return {field: reader() for field, reader in fields.items()}

        self._editors["repeat"] = read
        return box

    def _show_placeholder(self, text: str) -> None:
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setObjectName("muted")
        self.setWidget(label)

    # -- applying ----------------------------------------------------------

    def apply(self) -> None:
        if self.path is None:
            return
        try:
            node = self.document.node(self.path)
            data = node.model_dump()
            for field, read in self._editors.items():
                data[field] = read()
            new = type(node).model_validate(data)
            self.document.replace_node(self.path, new)
            self.applied.emit()
        except Exception as exc:  # noqa: BLE001 - reported to the user
            self.error.emit(_message(exc))


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
