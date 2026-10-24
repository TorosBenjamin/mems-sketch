"""Property editor generated from a shape node's schema.

Numeric fields accept a number or an expression (see
:mod:`mems_sketch.gui.value_edit`: expressions show their value inside the
field, names are completed, and a button uses or makes parameters). Pairs such
as x and y sit side by side. The name is the panel's title, edited in place,
with an eye that switches the shape off or on at once. Long texts are cut
with "…" so they never widen the panel. For a component reference the
component's own parameter
schema is shown, with defaults as placeholders. Every field applies by itself
(Enter, leaving it, choosing) and goes through the document, so invalid input
is rejected without changing the design.

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
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.core.errors import explain
from mems_sketch.core.expressions import evaluate
from mems_sketch.core.levels import Stack, is_relative
from mems_sketch.core.shapes import (
    MODIFIER_KINDS,
    CornersModifier,
    Modifier,
    NodePath,
    Shape,
)
from mems_sketch.editing import EditSession
from mems_sketch.gui import icons, theme, widgets
from mems_sketch.gui.actions import OPERATIONS, PRIMITIVES
from mems_sketch.gui.canvas import layer_color
from mems_sketch.gui.help import HelpButton
from mems_sketch.gui.panels import component_icon, parse_value, read_only_kind, swatch_icon
from mems_sketch.gui.settings import APPLICATION, ORGANIZATION
from mems_sketch.gui.value_edit import (
    ElidedLineEdit,
    ExpandedEdit,
    ValueEdit,
    ask_parameter_name,
)

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
    "via",
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
    "mirror_x": "Mirror about x axis",
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
        "even_rim": "Even rim",
    },
}
# What a field does, behind a "?" by its name: (kind, field) -> explanation.
FIELD_HELP = {
    ("perforate", "grid"): "*square*: rows of holes straight above each other. *hex*: every "
    "other row shifted by half a pitch, rows pitch × √3/2 apart.",
    ("perforate", "pitch"): "From one hole's centre to the next along a row. The webs "
    "between holes are *pitch − hole size* wide.",
    ("perforate", "row_pitch"): "From one row's centre to the next. Empty: the pitch (on a "
    "hex grid, pitch × √3/2).",
    ("perforate", "hole_size"): "How wide each hole is. Bigger holes make the webs "
    "(pitch − hole size) narrower.",
    ("perforate", "hole_height"): "How high each hole is. Empty: as high as it is wide.",
    ("perforate", "corner_radius"): "How round the holes' corners are. Empty: half the "
    "shorter side, so round holes; 0: square holes.",
    ("perforate", "margin"): "The narrowest the solid rim between the outline and the holes "
    "gets. A hole goes only where it fits whole, so the rim comes out wider where the "
    "holes do not fill the shape exactly.\n\nFor a rim as wide as the webs, tick *Even rim*.",
    ("perforate", "keep_out"): "Shapes the holes keep clear of, by name: an anchor, or "
    "where a spring lands on the plate.",
    ("perforate", "keep_out_margin"): "How far the holes stay from the shapes to keep clear "
    "of. Empty: the margin.",
    ("perforate", "x"): "A hole's centre goes here. Empty: the grid is centred on the shape.",
    ("perforate", "even_rim"): "Every web and the rim the same width: the pitch stretches a "
    "little, along x and y, so that the holes fill the shape's box. Square grid only; the "
    "margin and the hole position are then not used.",
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


# What a field means, where its name does not say: (kind, field) -> tooltip.
FIELD_TIPS = {
    ("ref", "mirror_x"): "Mirrored about the x axis (y → −y), before it is turned",
    ("transform", "mirror_x"): "Mirrored about the x axis (y → −y), before it is turned",
    ("route", "style"): "manhattan: along x and y only; l: along the start's direction, then "
    "the end's, with one bend; straight: straight lines through the via points",
    ("route", "bend"): "sharp; round: an arc of the radius; chamfer: cut by the radius along "
    "each side",
    ("route", "radius"): "The bends' radius (round) or cut (chamfer); not used by sharp bends",
    ("route", "width"): "0: the ports' width",
    ("fillet", "radius"): "For the corners that turn out (convex); 0: sharp",
    ("fillet", "inner_radius"): "For the corners that turn in (concave); 0: sharp",
    ("connection", "radius"): "Where it turns out; 0: sharp",
    ("connection", "inner_radius"): "Where it turns in, between the beams (the usual case); "
    "0: sharp",
    ("connection", "width"): "For an end whose port has no width",
    ("mirror", "axis"): "x: mirrors x, across the vertical line at x; y: mirrors y, across "
    "the horizontal line at y; both: four copies",
}
# The choices of a list in words: (kind, field) -> {value: text}.
LITERAL_TEXTS: dict[tuple[str, str], dict[str, str]] = {}
# Fields measured in µm: they show it at their end.
LENGTH_FIELDS = {
    "radius",
    "inner_radius",
    "outer_radius",
    "width",
    "distance",
    "pitch",
    "row_pitch",
    "hole_size",
    "hole_height",
    "corner_radius",
    "margin",
    "keep_out_margin",
}
LENGTH_PAIRS = {("x0", "y0"), ("x1", "y1"), ("x", "y"), ("dx", "dy")}


def default_hint(kind: str, field: str, model, scope: dict[str, float]) -> str:
    """What an empty optional field stands for, shown greyed in it: a number when it
    can be worked out, else in words."""

    def value(name: str, fallback: float | None = None) -> float | None:
        written = getattr(model, name, None)
        if written is None:
            return fallback
        try:
            return float(written) if isinstance(written, int | float) else evaluate(written, scope)
        except (ValueError, KeyError):
            return None

    def shown(number: float | None, words: str) -> str:
        return f"{number:g}" if number is not None else words

    if field == "segments":
        return "auto (curve tolerance)"
    if (kind, field) == ("polar_array", "step"):
        count = value("count")
        return shown(360 / count if count else None, "360 / count")
    if kind == "perforate":
        pitch, size = value("pitch"), value("hole_size")
        if field == "row_pitch":
            hex_rows = getattr(model, "grid", "square") == "hex" and pitch is not None
            return shown(pitch * 3**0.5 / 2 if hex_rows else pitch, "pitch")
        if field == "hole_height":
            return shown(size, "hole size")
        if field == "corner_radius":
            height = value("hole_height", size)
            if size is None or height is None:
                return "round"
            return f"{min(size, height) / 2:g} (round)"
        if field == "keep_out_margin":
            return shown(value("margin"), "margin")
        if field in ("x", "y"):
            return "auto"
    return ""


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
        # The fields shrink to the panel; only a panel narrower than they can go
        # scrolls sideways, so nothing is ever cut off out of reach.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.path: NodePath | None = None
        self._shown: NodePath | None = None  # the node the form shows (to keep its scroll)
        self.hide_implementation = False  # a read-only component: show its interface
        self._editors: dict[str, typing.Callable[[], object]] = {}
        self._scope: dict[str, float] = {}
        self._offered: tuple[str, ...] = ()  # names completed besides those of the scope
        # The tool window's header says what the shape is ("Rectangle").
        self.header_context = widgets.header_context()
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
            self._show_component()
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
        layout.setSpacing(4)  # between the parts
        # One header row: what it is (its icon; the kind in words is in the panel's
        # header), its name and whether it is drawn.
        title = QHBoxLayout()
        title.setSpacing(6)
        glyph = QLabel()
        glyph.setPixmap(icons.pixmap(node.icon_name(), 18))
        glyph.setToolTip(kind_title(node))
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
        layout.addSpacing(4)
        self.header_context.setText(kind_title(node))
        self.name_edit = name
        self._editors["name"] = lambda: name.text().strip() or None
        main = _section("Placement" if type(node).placed else "Geometry", node.kind)
        form = _form(main)
        layout.addWidget(main)

        fields_shown = self._add_fields(form, node, node.kind, "", skip=_SPECIAL)
        bend = fields_shown.get("bend")
        if isinstance(bend, QComboBox) and "radius" in fields_shown:  # a route: sharp has none

            def update_radius(*_) -> None:
                _set_active(form, fields_shown["radius"], bend.currentData() != "sharp")

            bend.currentIndexChanged.connect(update_radius)
            update_radius()

        fields = type(node).model_fields
        if form.rowCount() == 0:  # nothing of its own to set (e.g. an operation)
            main.hide()
        if "points" in fields:
            form.addRow("Points (x, y per line)", self._points_editor(node.points))
        if "via" in fields:
            form.addRow("Via (x, y per line)", self._points_editor(node.via, "via"))
        if "mapping" in fields:
            form.addRow("Mapping (from → to)", self._mapping_editor(node.mapping))
        if "params" in fields:
            layout.addWidget(self._params_editor(node))
        layout.addWidget(self._align_editor(node, path))
        # Every field applies by itself: Enter or leaving it (text boxes of several
        # lines: leaving them or Ctrl+Enter), lists and boxes when changed.
        layout.addWidget(self._modifiers_editor(node, path))
        layout.addStretch()
        _line_up(body)
        # The same node again (a field applied): stay where the panel was scrolled to.
        self._show(body, keep_scroll=path == self._shown)
        self._shown = path

    def show_several(self, count: int) -> None:
        """Several shapes selected: say how many (one at a time is edited here)."""
        self.path = None
        self._show_placeholder(f"{count} shapes selected. Select one to edit its properties.")

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

    def _field_editor(self, field: str, annotation, value, kind: str = "") -> QWidget:
        args = typing.get_args(annotation)
        bare = field.rpartition(":")[2]
        if typing.get_origin(annotation) is typing.Literal:
            combo = _combo()
            texts = LITERAL_TEXTS.get((kind, bare), {})
            for choice in args:
                combo.addItem(texts.get(choice, str(choice)), choice)
            combo.setCurrentIndex(max(combo.findData(value), 0))
            self._editors[field] = combo.currentData
            return self._applies(combo)
        if annotation is bool:
            box = QCheckBox()
            box.setChecked(bool(value))
            box.clicked.connect(self.apply)
            self._editors[field] = box.isChecked
            return box
        if field == "layer":
            combo = self._layer_picker(value, optional=type(None) in args)
            self._editors[field] = lambda: combo.currentData()
            return self._applies(combo)
        if field in ("start", "end") and annotation is str:  # a route's ports
            combo = _editable(_combo(), "A port of a shape beside it: shape.port.name")
            with contextlib.suppress(Exception):  # it does not build: type one in
                combo.addItems(self._ports_beside())
            combo.setCurrentText(value)
            self._editors[field] = lambda: combo.currentText().strip()
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
            return self._component_field(field, value)
        if typing.get_origin(annotation) is list and args == (str,):  # names, e.g. keep_out
            edit = QLineEdit(", ".join(value))
            edit.returnPressed.connect(self.apply)
            edit.setReadOnly(self.document.read_only)
            self._editors[field] = lambda: [n.strip() for n in edit.text().split(",") if n.strip()]
            if bare != "ends":
                edit.setPlaceholderText("shape names, comma-separated")
                return edit
            # A connection's ends are ports: typed, or added from a list of them.
            edit.setPlaceholderText("ports, comma-separated: beam.port.end, …")
            add = QToolButton()
            icons.bind(add, "add")
            add.setToolTip("Add a port")
            add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            add.setEnabled(not self.document.read_only)
            menu = QMenu(add)
            with contextlib.suppress(Exception):  # it does not build: type them in
                for port in self._ports_beside():

                    def append(_=False, port=port) -> None:
                        names = [n.strip() for n in edit.text().split(",") if n.strip()]
                        edit.setText(", ".join([*names, port]))
                        self.apply()

                    menu.addAction(port, append)
            add.setMenu(menu)
            return _with_button(edit, add)
        optional = type(None) in args
        return self._value_editor(field, value, optional)

    def _component_field(self, field: str, value: str) -> QWidget:
        """The component a placement places: its name, and a button that opens it in
        its own tab, whose menu replaces it with another component."""
        chosen = [value]
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(4)
        label = QLabel(_qualified_html(value))
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setToolTip(value)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        line.addWidget(label, 1)
        opener = QToolButton()
        opener.setObjectName("open-component")
        icons.bind(opener, "edit")
        opener.setToolTip(f"Open {value} in its own tab")
        opener.setAutoRaise(True)
        opener.clicked.connect(lambda: self.open_component.emit(value))
        others = [n for n in self.document.component_names() if n != value]
        if others and not self.document.read_only:
            opener.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            menu = QMenu(opener)
            replace = menu.addMenu("Replace with")
            for other in others:

                def swap(_=False, other=other) -> None:
                    chosen[0] = other
                    self.apply()

                replace.addAction(other, swap)
            opener.setMenu(menu)
        line.addWidget(opener)
        self._editors[field] = lambda: chosen[0]
        return row

    def _ports_beside(self) -> list[str]:
        """The ports a route or connection can join: every port but its own."""
        own = None
        with contextlib.suppress(KeyError, TypeError):
            own = self.document.node(self.path).name
        ports = self.document.results.all_ports()
        return sorted(p for p in ports if own is None or not p.startswith(f"{own}.port."))

    def _layer_picker(self, value: str | None, optional: bool = False) -> QComboBox:
        """The layers a shape can be on, each with the colour of the layer it lands
        on: relative ones first (``level+1 · metal``), then the layers by name.
        ``optional``: first, none (a connection or route on its ports' layer)."""
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
        if optional:
            combo.addItem(icons.icon("layers"), "ports' layer", None)
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
        combo.setCurrentIndex(0 if value is None else max(combo.findData(value), 0))
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
        edit.setProperty("field", field.rpartition(":")[2])  # the name a new parameter gets
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
                    optional = type(None) in typing.get_args(
                        type(model).model_fields[name].annotation
                    )
                    value = getattr(model, name)
                    edit = self._value_editor(key + name, value, optional=optional, prefix=prefix)
                    hint = default_hint(kind, name, model, self._scope) if value is None else ""
                    if optional and value is None:
                        edit.setPlaceholderText(hint)
                    if pair in LENGTH_PAIRS and not hint.isalpha():  # a word needs the room
                        edit.set_unit("µm")
                    line.addWidget(edit, 1)
                    widgets[name] = edit
                    done.add(name)
                help_text = FIELD_HELP.get((kind, field))
                form.addRow(_label_with_help(label, help_text) if help_text else label, row)
                continue
            label = labels.get(field) or _LABELS.get(field, field.replace("_", " ").capitalize())
            value = getattr(model, field)
            widget = self._field_editor(key + field, info.annotation, value, kind)
            if isinstance(widget, ValueEdit) and field in LENGTH_FIELDS:
                widget.set_unit("µm")
            if isinstance(widget, ValueEdit) and value is None:
                hint = default_hint(kind, field, model, self._scope)
                if hint:
                    widget.setPlaceholderText(hint)
            tip = FIELD_TIPS.get((kind, field))
            help_text = FIELD_HELP.get((kind, field))
            shown = _label_with_help(label, help_text) if help_text else _label(label)
            if tip:
                if isinstance(widget, ValueEdit):
                    widget.set_help(tip)
                else:
                    widget.setToolTip(tip)
                shown.setToolTip(tip)
            widgets[field] = widget
            form.addRow(shown, widget)
        return widgets

    def _make_parameter(self, edit: ValueEdit, value) -> None:
        """Make a parameter with this value as its default, and use it in the field."""
        taken = {p.name for p in self.document.active_definition.parameters}
        try:
            name = ask_parameter_name(
                self, self.document.active, taken, value, edit.property("field") or "param"
            )
        except ValueError as exc:
            self.error.emit(str(exc))
            return
        if name is None:
            return
        edit.setText(name)
        try:
            new = self._collect()  # read the form before the panel is rebuilt
            self.document.parameters.set(name, value)
            self.document.nodes.replace(self.path, new)
            self.applied.emit()
        except Exception as exc:  # noqa: BLE001 - reported to the user
            self.error.emit(_message(exc))

    def _multiline(self, edit: QPlainTextEdit) -> QPlainTextEdit:
        """A text box of several lines: Enter is a new line, so leaving it or
        Ctrl+Enter applies it; Tab moves on."""
        edit.setTabChangesFocus(True)
        edit.setReadOnly(self.document.read_only)
        for keys in ("Ctrl+Return", "Ctrl+Enter"):
            shortcut = QShortcut(QKeySequence(keys), edit)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(self.apply)
        edit.setToolTip("Leaving the box or Ctrl+Enter applies it")
        return edit

    def _points_editor(self, points, field: str = "points") -> QWidget:
        edit = QPlainTextEdit("\n".join(f"{_format(x)}, {_format(y)}" for x, y in points))
        edit.setFixedHeight(110)
        self._multiline(edit)

        def read():
            rows = [line.split(",") for line in edit.toPlainText().splitlines() if line.strip()]
            if any(len(r) != 2 for r in rows):
                raise ValueError("each point needs two comma-separated values")
            return [(parse_value(x), parse_value(y)) for x, y in rows]

        self._editors[field] = read
        return edit

    def _mapping_editor(self, mapping: dict[str, str]) -> QWidget:
        edit = QPlainTextEdit("\n".join(f"{a} -> {b}" for a, b in mapping.items()))
        edit.setFixedHeight(80)
        self._multiline(edit)

        def read():
            result = {}
            layers = self.document.project.layers
            for line in edit.toPlainText().replace("→", "->").splitlines():
                if not line.strip():
                    continue
                source, sep, target = (part.strip() for part in line.partition("->"))
                if not sep or not source or not target:
                    raise ValueError(
                        f"write each mapping as 'source -> target', not '{line.strip()}'"
                    )
                if source in result:
                    raise ValueError(f"'{source}' is mapped twice")
                for layer in (source, target):
                    if layer not in layers and not is_relative(layer):
                        raise ValueError(f"there is no layer '{layer}'")
                result[source] = target
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
            # The name, as expressions write it; the description behind a "?".
            widget.setMinimumWidth(PARAMETER_FIELD_MIN_PX)
            if field in component.internal:
                label = _label(f"{field} (internal)")
                widget.setProperty("invalid", True)
                internal = (
                    f"'{field}' is internal to {node.component}: clear it (it cannot be set here)"
                )
                if isinstance(widget, ValueEdit):
                    widget.set_help(internal)
                else:
                    widget.setToolTip(internal)
            elif info.description:
                label = _label_with_help(field, info.description)
                unit = re.search(r"\((µm|nm|mm|°)\)", info.description)
                if unit and isinstance(widget, ValueEdit):  # "Hole pitch (µm)": in the field
                    widget.set_unit(unit[1])
            else:
                label = _label(field)
            form.addRow(label, widget)

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
            _label_with_help("To", explain),
            _with_button(
                self._applies(target),
                picker("to", "Pick the point to align to on the canvas"),
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
            offset = self._value_editor(f"align:{field}", value, prefix=field[1])
            offset.set_unit("µm")
            line.addWidget(offset, 1)
            offsets[field] = self._editors.pop(f"align:{field}")
        form.addRow("Offset", row)

        def read():
            if not box.isChecked():
                return None
            to = target.currentText().strip()
            if not to:  # ticked, no point chosen yet: not an edit until one is
                return align
            return {
                "point": own.currentText().strip() or "center",
                "to": to,
                **{field: reader() for field, reader in offsets.items()},
            }

        self._editors["align"] = read
        return box

    def _modifiers_editor(self, node: Shape, path: NodePath) -> QWidget:
        """The modifier stack: one card each, then "Add modifier"."""
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
        if kind == "perforate":  # an even rim places the holes itself

            def update_rim(*_) -> None:
                even = widgets["even_rim"].isChecked()
                for field in ("margin", "x", "y"):
                    _set_active(form, widgets[field], not even)

            widgets["even_rim"].toggled.connect(update_rim)
            update_rim()
        if kind == "mirror":  # a line or point given by "about" replaces axis, x and y
            about = widgets["about"].findChild(QComboBox) or widgets["about"]
            axis = widgets["axis"]

            def update_axis(*_) -> None:
                free = not about.currentText().strip()
                lines = {"x": ("x",), "y": ("y",), "both": ("x", "y")}[axis.currentData() or "x"]
                for field, on in (
                    ("axis", free),
                    ("x", free and "x" in lines),  # the line at x only mirrors across x
                    ("y", free and "y" in lines),
                ):
                    _set_active(form, widgets[field], on)

            about.currentTextChanged.connect(update_axis)
            axis.currentIndexChanged.connect(update_axis)
            update_axis()

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
        icons.bind(pick, "pick_corner")
        pick.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        pick.setToolTip("The Corners tool (O): click a corner to round it, drag to set its radius")
        pick.setEnabled(not read_only)
        pick.clicked.connect(self.pick_corners)
        rows.addWidget(pick)
        self._editors[key + "corners"] = lambda: [read() for read in readers]
        return box

    def _about_editor(self, key: str, value: str | None, path: NodePath) -> QWidget:
        """Where a mirror mirrors: nothing (use the axis), a guide, or a point."""
        combo = _editable(
            _combo(), "A guide's name (mirror across it), or a point (mirror through it)"
        )
        combo.addItem("")
        guides = [name for p, name, *_ in self.document.results.guides() if p != path]
        combo.addItems(guides)
        points = [name for name, *_ in self.document.results.align_targets(path)]
        combo.addItems([*SELF_POINTS, *points])
        combo.setCurrentText(value or "")
        self._applies(combo)
        combo.lineEdit().setPlaceholderText("the axis below")
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
        self.header_context.setText("")
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
            layout.addWidget(widgets.markdown_label(definition.description))
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

    def _show(self, content: QWidget, keep_scroll: bool = False) -> None:
        """Replace the panel's content. The old content is deleted later, not now: the
        change may come from one of its own fields or buttons (Enter in a field,
        the eye), which Qt is still delivering an event to. ``keep_scroll``: the
        new content is scrolled as the old was (the same node, built again)."""
        bar = self.verticalScrollBar()
        scrolled = bar.value() if keep_scroll else 0
        if not keep_scroll:
            self._shown = None
        old = self.takeWidget()
        if old is not None:
            old.hide()
            old.deleteLater()
        self.setWidget(content)
        if not scrolled:
            bar.setValue(0)  # another node: from its top
        if scrolled:
            content.adjustSize()  # its height now, so the scroll bar can reach as far
            bar.setValue(scrolled)
            QTimer.singleShot(  # once laid out, unless replaced meanwhile
                0, lambda: bar.setValue(scrolled) if self.widget() is content else None
            )

    def _show_component(self) -> None:
        """Nothing selected: the open component's own settings (its description),
        when it is one of the project's, then how to see a shape's."""
        name = self.document.active
        if self.document.read_only or name not in self.document.project.components:
            self._show_placeholder("Select a shape to edit its properties.")
            return
        definition = self.document.project.components[name]
        body = QWidget()
        body.setObjectName("properties-body")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(4)
        title = QHBoxLayout()
        title.setSpacing(6)
        glyph = QLabel()
        glyph.setPixmap(icons.pixmap("component", 18))
        heading = QLabel(name)
        heading.setObjectName("heading")
        title.addWidget(glyph)
        title.addWidget(heading, 1)
        layout.addLayout(title)
        layout.addSpacing(4)
        part = _section("Component", "component")
        form = _form(part)
        description = widgets.DescriptionEdit(definition.description)
        description.setObjectName("component-description")
        description.setPlaceholderText("What it is, shown when placing it")
        description.setToolTip(
            "A sentence or two on what the component is: the Place popup and Search "
            "Everywhere show it. `code` and *emphasis* are shown as such. Leaving the "
            "box or Ctrl+Enter applies it"
        )

        def describe() -> None:
            if description.text().strip() == definition.description.strip():
                return
            try:
                self.document.components.set_description(name, description.text())
                self.applied.emit()
            except Exception as exc:  # noqa: BLE001 - reported to the user
                self.error.emit(_message(exc))

        description.editingFinished.connect(describe)
        form.addRow("Description", description)
        layout.addWidget(part)
        hint = QLabel("Select a shape to edit its properties.")
        hint.setObjectName("muted")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setContentsMargins(0, 12, 0, 0)
        layout.addWidget(hint)
        layout.addStretch(1)
        self.description_edit = description
        self._show(body)
        self.header_context.setText("Component")

    def _show_placeholder(self, text: str) -> None:
        self.header_context.setText("")
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
            not isinstance(old, QLineEdit | QPlainTextEdit)
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
        # The field the focus went to, by its place in the form: the form is built
        # again once applied, and the focus goes back to the same place.
        fields = _focusable(body)
        at = fields.index(new) if new in fields else None
        left = fields.index(old) if old in fields else None  # the field that was edited
        try:
            node = self._collect()
        except Exception as exc:  # noqa: BLE001 - reported, as Enter would
            message = _message(exc)
            self.error.emit(message)
            QTimer.singleShot(0, lambda: self._rejected(path, at, left, message))
            return
        with contextlib.suppress(KeyError):
            if node == self.document.node(path):
                return
        # After the focus has moved, not inside the focus change being delivered.
        QTimer.singleShot(0, lambda: self._apply_left(path, node, at, left))

    def _apply_left(self, path: NodePath, node: Shape, at: int | None, left: int | None) -> None:
        try:
            self.document.nodes.replace(path, node)
        except Exception as exc:  # noqa: BLE001 - reported to the user
            message = _message(exc)
            self.error.emit(message)
            self._rejected(path, at, left, message)
            return
        self.applied.emit()
        self._focus_field(path, at)

    def apply(self) -> None:
        """Apply the form (Enter in a field). The focus stays on the field, in the
        form built again; a value refused goes back to the one stored."""
        if self.path is None:
            return
        path, body, focused = self.path, self.widget(), QApplication.focusWidget()
        fields = _focusable(body) if body is not None else []
        at = fields.index(focused) if focused in fields else None
        try:
            self.document.nodes.replace(path, self._collect())
        except Exception as exc:  # noqa: BLE001 - reported to the user
            message = _message(exc)
            self.error.emit(message)
            self._rejected(path, at, at, message)
            return
        self.applied.emit()
        self._focus_field(path, at)

    def _rejected(
        self, path: NodePath, at: int | None, culprit: int | None = None, message: str = ""
    ) -> None:
        """A value was refused: show the node as it is (the value stored, not the
        one typed), so the refused text cannot block the next edit, and mark the
        field it was typed in (field number ``culprit``) with why."""
        if self.path != path:
            return
        self.show_node(path)
        self._focus_field(path, at)
        body = self.widget()
        fields = _focusable(body) if body is not None else []
        if culprit is not None and culprit < len(fields) and message:
            field = fields[culprit]
            field.setProperty("invalid", True)
            if isinstance(field, ValueEdit):
                field.set_help(f"Not applied: {message}")
            else:
                field.setToolTip(f"Not applied: {message}")
            field.style().unpolish(field)
            field.style().polish(field)

    def _focus_field(self, path: NodePath, at: int | None) -> None:
        """Put the focus on the form's field number ``at`` (the form was built again)."""
        body = self.widget()
        if at is None or self.path != path or body is None:
            return
        fields = _focusable(body)
        if at < len(fields):
            fields[at].setFocus(Qt.FocusReason.OtherFocusReason)


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


COMBO_MIN_PX = 64  # the narrowest a list of the form gets


def _combo() -> QComboBox:
    """A combo box that may shrink below its longest item (long names never widen
    the panel; the list still opens wide enough)."""
    combo = QComboBox()
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(4)
    combo.setMinimumWidth(COMBO_MIN_PX)  # below its hint, which counts the placeholder too
    combo.view().setTextElideMode(Qt.TextElideMode.ElideNone)
    return combo


class _StartEdit(QLineEdit):
    """An editable list's text: when not being edited it shows its start (the
    shape of a point reference, not the middle of it, #227)."""

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.setCursorPosition(0)


def _editable(combo: QComboBox, tip: str = "") -> QComboBox:
    """Let ``combo`` take typed text, shown from its start; its tooltip has all of
    the text, then ``tip``."""
    edit = _StartEdit()
    combo.setEditable(True)
    combo.setLineEdit(edit)

    def changed(text: str) -> None:
        if not edit.hasFocus():
            edit.setCursorPosition(0)
        combo.setToolTip("\n".join(t for t in (text, tip) if t))

    combo.currentTextChanged.connect(changed)
    changed(combo.currentText())
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
    away. Folded cards are remembered per kind of shape (``key``), between sessions.

    A long heading ends in "…" rather than widening the panel."""

    def __init__(self, title: str, key: str = "") -> None:
        super().__init__(title)
        self._title = title
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

    def title(self) -> str:  # the whole heading, also when it is shown shortened
        return self._title

    def setTitle(self, title: str) -> None:
        self._title = title
        super().setTitle(title)
        self._elide_title()

    def minimumSizeHint(self) -> QSize:
        """As wide as the fields need, not as the heading: that one is shortened."""
        hint = super().minimumSizeHint()
        layout = self.layout()
        if layout is None:
            return QSize(min(hint.width(), SECTION_MIN_PX), hint.height())
        sides = self.width() - self.contentsRect().width()
        return QSize(max(layout.minimumSize().width() + sides, SECTION_MIN_PX), hint.height())

    def _elide_title(self) -> None:
        indicator = 24 if self.isCheckable() else 0  # the check box before the heading
        room = self.width() - indicator - 30  # the fold chevron
        shown = self.fontMetrics().elidedText(self._title, Qt.TextElideMode.ElideRight, room)
        if shown != super().title():
            super().setTitle(shown)

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
        self._elide_title()
        self._toggle.adjustSize()
        self._toggle.move(self.width() - self._toggle.width(), 4)
        self._toggle.raise_()


SECTION_MIN_PX = 120  # the narrowest a card gets (its heading then ends in "…")
SECTION_FOLDED_PX = 28  # a folded card: just its heading
FOLDED_KEY = "properties/folded"  # the folded cards, "kind/section"
QWIDGETSIZE_MAX = (1 << 24) - 1


def _settings() -> QSettings:
    return QSettings(ORGANIZATION, APPLICATION)


def _section(title: str, kind: str = "") -> Section:
    """A card of the form (see :class:`Section`); ``kind`` remembers it folded."""
    return Section(title, f"{kind}/{title.split(' of ')[0]}" if kind else "")


def _qualified_html(name: str) -> str:
    """``std.perforated_plate`` with where it is from ("std.") in a lighter colour."""
    head, dot, last = name.rpartition(".")
    if not dot:
        return html.escape(name)
    muted = theme.color("muted").name()
    return f"<span style='color:{muted}'>{html.escape(head)}.</span>{html.escape(last)}"


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
    return all(isinstance(getattr(model, f), float | int | str | None) for f in pair) and not any(
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
    return explain(exc)


def _set_active(form: QFormLayout, widget: QWidget, on: bool) -> None:
    """Enable or grey out a field that does nothing as the others are set (its label
    too, when it has a row of its own; one of a pair greys out alone)."""
    widget.setEnabled(on)
    label = form.labelForField(widget)
    if label is not None:
        label.setEnabled(on)
        return
    row = widget.parentWidget()
    if row is not None and form.labelForField(row) is not None:
        siblings = [w for w in row.findChildren(QWidget) if isinstance(w, ValueEdit)]
        form.labelForField(row).setEnabled(any(w.isEnabled() for w in siblings))


def _with_button(widget: QWidget, button: QToolButton) -> QWidget:
    """``widget`` with a small button after it."""
    row = QWidget()
    line = QHBoxLayout(row)
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(2)
    line.addWidget(widget, 1)
    line.addWidget(button)
    return row


LINE_UP_MAX_PX = 110
PARAMETER_FIELD_MIN_PX = 60  # a placed component's parameter field never narrower  # the widest the labels of a form are made to line up to


def _line_up(body: QWidget) -> None:
    """Give the labels of every part of the form one width, so that the fields of
    all the parts start at the same place."""
    forms = body.findChildren(QFormLayout)
    labels = [
        item.widget()
        for form in forms
        for row in range(form.rowCount())
        if (item := form.itemAt(row, QFormLayout.ItemRole.LabelRole)) and item.widget()
    ]
    if labels:
        # Up to a point: one very long label ("Initial finger overlap") would
        # otherwise narrow every field of the form.
        width = min(max(label.sizeHint().width() for label in labels), LINE_UP_MAX_PX)
        for label in labels:
            label.setMinimumWidth(width)


def _label_with_help(text: str, help_text: str) -> QWidget:
    """A field's label with a "?" right after it that explains ``help_text``: the
    "?" sits by the name, so every field of the form is as wide as the others."""
    row = QWidget()
    line = QHBoxLayout(row)
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(2)
    line.addWidget(_label(text))
    line.addWidget(HelpButton(help_text))
    line.addStretch(1)
    return row
