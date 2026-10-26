"""The quick-look viewer (#141): a small read-only window to look at one component.

- **The canvas**, as in the editor: wheel zooms, a middle or right drag pans
  (or Space and a left drag), F fits. Layers can be shown and hidden, and the
  Measure button (D) puts rulers down: click two points; Esc clears them.
- **Its parameters**, to try values: trial values only, nothing is saved.
- **Nothing that edits:** no project tree, no editing tools, no Properties, so
  the design can't be changed by accident.
- **Starts fast:** it builds the one component, without the editor's panels
  or the project's history.
- **Open in editor** opens the project at that component's tab.

It opens from a component's ``.msketch`` file (a double-click while its
project isn't open in the editor), from *View* in a component's right-click
menu, or with ``mems-sketch --view FILE``.
"""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.core.component import Geometry
from mems_sketch.gui import icons
from mems_sketch.gui.canvas import LayoutCanvas, layer_colors
from mems_sketch.gui.value_edit import ValueEdit, _format

VIEWER_SIZE = (1000, 680)


class Viewer(QWidget):
    """``component`` of ``project`` on a read-only canvas, with its parameters to try.
    ``path``: the file it was opened from (for *Open in editor*); the project is
    copied, so changes made in the editor afterwards don't reach it."""

    open_in_editor = Signal(str, str)  # the file to open in the editor, the component
    rebuilt = Signal()  # drawn again (or failed: see :attr:`error`)

    def __init__(self, project, component: str, path: str | Path | None = None) -> None:
        super().__init__(None, Qt.WindowType.Window)
        from mems_sketch.engine import Engine

        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.project = copy.deepcopy(project)
        self.component = component
        self.path = Path(path) if path is not None else None
        self.engine = Engine().load(self.project)
        self.trials: dict[str, float | str] = {}
        self.error = ""
        self.geometry_shown = Geometry()
        self.rulers: list[tuple[float, float, float, float]] = []
        self._first: tuple[float, float] | None = None  # a ruler's first point
        self.colors = layer_colors(self.project.layers)
        self.visible = {name: True for name in self.project.layers}
        self.setWindowTitle(f"{component} — view")
        self.setWindowIcon(icons.icon("eye"))

        self.canvas = LayoutCanvas()
        self.canvas.set_caption(component, "view only")
        self.canvas.pressed.connect(self._pressed)
        self.canvas.moved.connect(self._moved)
        self.canvas.set_tool_cursor(Qt.CursorShape.ArrowCursor)

        self.measure = self._tool_button("measure", "Measure (D): click two points", True)
        self.measure.toggled.connect(self._measure_toggled)
        self.clear = self._tool_button("clear", "Clear the rulers (Esc)")
        self.clear.clicked.connect(self.clear_rulers)
        fit = self._tool_button("fit", "Fit the view (F)")
        fit.clicked.connect(lambda: self.canvas.fit(animated=True))
        self.editor = QPushButton(icons.icon("edit"), "Open in editor")
        self.editor.setToolTip("Open the project, at this component's tab")
        self.editor.setEnabled(self.path is not None)
        self.editor.clicked.connect(self._open_in_editor)
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 6, 8, 6)
        for button in (self.measure, self.clear, fit):
            bar.addWidget(button)
        bar.addStretch(1)
        bar.addWidget(self.editor)

        self.layers = QListWidget()
        self.layers.setObjectName("viewer-layers")
        for name in self.project.layers:
            item = QListWidgetItem(_swatch(self.colors[name]), name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.layers.addItem(item)
        self.layers.itemChanged.connect(self._layer_toggled)

        self.fields: dict[str, ValueEdit] = {}
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for parameter in self.parameters():
            field = ValueEdit(parameter.default, parameters=[p.name for p in self.parameters()])
            field.integer = parameter.integer
            field.set_help(parameter.description)
            field.returnPressed.connect(lambda n=parameter.name: self._field_applied(n))
            field.editingFinished.connect(lambda n=parameter.name: self._field_applied(n))
            field.scrubbed.connect(lambda v, n=parameter.name: self.try_value(n, v))
            field.scrub_finished.connect(lambda v, n=parameter.name: self.try_value(n, v))
            label = QLabel(parameter.name)
            label.setToolTip(parameter.description)
            form.addRow(label, field)
            self.fields[parameter.name] = field
        if not self.fields:
            none = QLabel("No parameters")
            none.setObjectName("muted")
            form.addRow(none)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_widget)
        self.reset = QPushButton("Defaults")
        self.reset.setToolTip("Back to the parameters' defaults")
        self.reset.clicked.connect(self.reset_values)
        note = QLabel("Trial values only: nothing is saved.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        self.message = QLabel()
        self.message.setObjectName("error")
        self.message.setWordWrap(True)
        self.message.hide()

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(8, 8, 8, 8)
        side_layout.addWidget(_heading("Parameters"))
        side_layout.addWidget(scroll, 2)
        side_layout.addWidget(note)
        side_layout.addWidget(self.reset, 0, Qt.AlignmentFlag.AlignLeft)
        side_layout.addWidget(self.message)
        side_layout.addWidget(_heading("Layers"))
        side_layout.addWidget(self.layers, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_layout.addLayout(bar)
        left_layout.addWidget(self.canvas, 1)
        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([VIEWER_SIZE[0] - 280, 280])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        QShortcut(QKeySequence("D"), self, activated=self.measure.toggle)
        QShortcut(QKeySequence("Esc"), self, activated=self._escape)
        self.resize(*VIEWER_SIZE)
        self.rebuild()

    # -- what is shown ----------------------------------------------------------------

    def parameters(self) -> list:
        """The component's public parameters (an imported one has none)."""
        definition = getattr(self.engine.component(self.component), "definition", None)
        if definition is None:
            return []
        return [p for p in definition.parameters if not p.internal]

    def rebuild(self) -> None:
        """Build the component with the trial values and draw it; a value that does
        not build leaves the last picture, with the reason under the parameters."""
        try:
            geometry = self.engine.build(self.component, self.trials).geometry
            scope = self.engine.variables(self.component, self.trials)
        except Exception as exc:  # noqa: BLE001 - shown in the window
            self.error = str(exc)
            self.message.setText(self.error)
            self.message.show()
        else:
            self.error = ""
            self.message.hide()
            self.geometry_shown = geometry
            self.canvas.show_geometry(geometry, self.colors, self.visible, [])
            for name, field in self.fields.items():
                field.scope = scope
                if name not in self.trials:
                    field.setPlaceholderText(_format(scope.get(name, "")))
        self.rebuilt.emit()

    def try_value(self, name: str, value) -> None:
        """Try ``value`` for the parameter ``name`` (None: its default)."""
        if value is None:
            self.trials.pop(name, None)
        else:
            self.trials[name] = value
        self.rebuild()

    def reset_values(self) -> None:
        self.trials.clear()
        for name, field in self.fields.items():
            default = next(p.default for p in self.parameters() if p.name == name)
            field.setText(_format(default))
        self.rebuild()

    def _field_applied(self, name: str) -> None:
        field = self.fields[name]
        try:
            value = field.value()
        except ValueError as exc:
            self.message.setText(str(exc))
            self.message.show()
            return
        if self.trials.get(name) != value:
            self.try_value(name, value)

    def _layer_toggled(self, item: QListWidgetItem) -> None:
        shown = item.checkState() == Qt.CheckState.Checked
        self.visible[item.text()] = shown
        self.canvas.set_layer_visible(item.text(), shown)

    # -- measuring --------------------------------------------------------------------

    def _measure_toggled(self, on: bool) -> None:
        self._first = None
        cursor = Qt.CursorShape.CrossCursor if on else Qt.CursorShape.ArrowCursor
        self.canvas.set_tool_cursor(cursor)
        self.canvas.show_rulers(self.rulers)

    def _pressed(self, x: float, y: float, _modifiers) -> None:
        if not self.measure.isChecked():
            return
        if self._first is None:
            self._first = (x, y)
            return
        self.add_ruler((*self._first, x, y))
        self._first = None

    def _moved(self, x: float, y: float, _modifiers, _down: bool) -> None:
        if self._first is not None:
            self.canvas.show_rulers([*self.rulers, (*self._first, x, y)])

    def add_ruler(self, ruler: tuple[float, float, float, float]) -> None:
        self.rulers.append(ruler)
        self.canvas.show_rulers(self.rulers)

    def clear_rulers(self) -> None:
        self.rulers.clear()
        self._first = None
        self.canvas.show_rulers([])

    def _escape(self) -> None:
        if self._first is not None or self.rulers:
            self.clear_rulers()
        elif self.measure.isChecked():
            self.measure.setChecked(False)

    # -- the editor ------------------------------------------------------------------

    def _open_in_editor(self) -> None:
        self.open_in_editor.emit(str(self.path) if self.path else "", self.component)
        self.close()

    def _tool_button(self, icon: str, tip: str, checkable: bool = False) -> QToolButton:
        button = QToolButton()
        button.setIcon(icons.icon(icon))
        button.setToolTip(tip)
        button.setCheckable(checkable)
        button.setAutoRaise(True)
        return button


def _swatch(color: QColor) -> QIcon:
    pixmap = QPixmap(12, 12)
    pixmap.fill(color)
    return QIcon(pixmap)


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("section")
    return label


def open_viewer(path: str | Path) -> Viewer:
    """The viewer for the component whose file ``path`` is (a manifest or a project
    folder: its top component). Raises ProjectFormatError or OSError if it can't be
    read, ValueError if there is no component to show."""
    from mems_sketch.storage import load
    from mems_sketch.storage.project_files import locate

    _, kind, name = locate(path)
    project = load(path)
    if kind != "component" or not name:
        name = project.top
    if not name:
        raise ValueError(f"{Path(path).name} has no component to view")
    return Viewer(project, name, path)
