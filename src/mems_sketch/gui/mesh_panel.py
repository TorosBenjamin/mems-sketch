"""The Mesh tool window: how the component being edited is meshed (#50), and a
preview of its mesh on the canvas (#72).

- **Sizes:** the largest element (empty: automatic), how fast sizes may grow
  from one element to the next, and a size per layer (empty: the largest).
- **Refinements:** places the mesh is finer: around a point, in a box, along
  the edges of a layer. They are drawn on the canvas, dashed, while the
  window is open.
- **Preview:** *Mesh* makes the mesh and draws it over the layers, with its
  element count and its worst element's quality; *Export…* saves it.

Every value is an expression like any other, over the component's parameters
and the process constants, and is saved with the component (undoable).
A library's component shows its settings without letting them be changed.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mems_sketch import meshing
from mems_sketch.core.mesh_settings import (
    DEFAULT_GROWTH,
    EdgesRefinement,
    MeshSettings,
    refinement,
)
from mems_sketch.editing import EditSession
from mems_sketch.gui import widgets
from mems_sketch.gui.panels import _Panel
from mems_sketch.gui.value_edit import ValueEdit, _format

NO_GMSH = (
    "Meshing needs <b>gmsh</b>, which is not installed:<br>"
    "<code>pip install 'mems-sketch[mesh]'</code>"
)


class MeshPanel(_Panel):
    shown_changed = Signal()  # the refinements or the preview to draw changed
    export_requested = Signal()

    def __init__(self, document: EditSession) -> None:
        super().__init__()
        self.document = document
        self.mesh: meshing.Mesh | None = None  # the preview
        self._preview_key: tuple | None = None  # what the preview was made from
        self._editable = False

        self.size = self._field(optional=True)
        self.size.returnPressed.connect(lambda: self._set("size", self.size, optional=True))
        self.growth = self._field()
        self.growth.returnPressed.connect(lambda: self._set("growth", self.growth))
        sizes = widgets.Card(
            "Sizes",
            help="*Size* is the largest element (µm); empty, it is a twentieth of the "
            "component's smaller side. Elements grow by at most *Growth* from one to "
            "the next, so sizes change gradually where a fine region meets a coarse "
            "one. A layer's size applies on that layer.",
        )
        form = QFormLayout()
        form.addRow("Size", self.size)
        form.addRow("Growth", self.growth)
        sizes.body.addLayout(form)
        layer_box = QWidget()  # (a widget of its own: its rows come and go)
        self.layer_form = QFormLayout(layer_box)
        self.layer_form.setContentsMargins(0, 0, 0, 0)
        sizes.body.addWidget(layer_box)
        self.layer_fields: dict[str, ValueEdit] = {}

        self.refinements = QListWidget()
        self.refinements.setObjectName("mesh-refinements")
        self.refinements.currentRowChanged.connect(self._show_refinement)
        self.refinements.setMaximumHeight(6 * self.refinements.fontMetrics().height() + 12)
        refine = widgets.Card(
            "Refinements",
            help="Places the mesh is finer: around a point, in a box, or along the "
            "edges of a layer. They are drawn dashed on the canvas.",
        )
        self.refine_actions = refine.add_actions(
            ("plus", "Add a refinement", self._add_menu),
            ("minus", "Remove the selected refinement", self._remove),
        )
        refine.body.addWidget(self.refinements)
        refine_box = QWidget()
        self.refine_form = QFormLayout(refine_box)
        self.refine_form.setContentsMargins(0, 0, 0, 0)
        refine.body.addWidget(refine_box)
        self.refine_fields: dict[str, ValueEdit] = {}
        self.refine_layer: QComboBox | None = None  # an edges refinement's layer, when shown

        self.mesh_button = QPushButton("Mesh")
        self.mesh_button.setToolTip("Make the mesh and show it on the canvas")
        self.mesh_button.clicked.connect(lambda: self.preview())
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear_preview)
        self.export_button = QPushButton("Export…")
        self.export_button.clicked.connect(self.export_requested.emit)
        self.summary = QLabel()
        self.summary.setObjectName("muted")
        self.summary.setWordWrap(True)
        preview = widgets.Card(
            "Preview",
            help="The mesh as it would be exported, drawn over the layers. Quality is "
            "gmsh's: 1 for an equilateral triangle, near 0 for a sliver.",
        )
        buttons = QHBoxLayout()
        for button in (self.mesh_button, self.clear_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.export_button)
        preview.body.addLayout(buttons)
        preview.body.addWidget(self.summary)

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        for card in (sizes, refine, preview):
            page_layout.addWidget(card)
        page_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.unavailable = widgets.EmptyState(NO_GMSH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)
        layout.addWidget(self.unavailable)
        self.page = scroll
        has_gmsh = meshing.available()
        self.unavailable.setVisible(not has_gmsh)
        self.page.setVisible(has_gmsh)

    # -- what is shown ----------------------------------------------------------------

    def settings(self) -> MeshSettings:
        """The mesh settings of the component being edited."""
        found = self.document.project.definition(self.document.active or "")
        return found[0].mesh if found is not None else MeshSettings()

    def refresh(self) -> None:
        active = self.document.active
        self._editable = active is not None and active in self.document.project.components
        settings = self.settings()
        scope, names = self._scope()
        for field in (self.size, self.growth):
            field.scope, field.parameters = scope, names
            field.setReadOnly(not self._editable)
        automatic = self._automatic()
        self.size.setText("" if settings.size is None else _format(settings.size))
        self.size.setPlaceholderText(f"{automatic:.3g}" if automatic else "")
        self.growth.setText(_format(settings.growth))
        size = settings.size if settings.size is not None else automatic
        self._fill_layers(settings, scope, names, "" if size is None else _format(size))
        row = self.refinements.currentRow()
        self.refinements.blockSignals(True)
        self.refinements.clear()
        for place in settings.refine:
            self.refinements.addItem(place.label)
        self.refinements.blockSignals(False)
        if settings.refine:
            self.refinements.setCurrentRow(min(max(row, 0), len(settings.refine) - 1))
        self._show_refinement(self.refinements.currentRow())
        for button in self.refine_actions:
            button.setEnabled(self._editable)
        if self.mesh is not None and self._preview_key != self._key():
            self.clear_preview()  # made from an earlier version: not shown as current
        self.shown_changed.emit()

    def _key(self) -> tuple:
        try:
            geometry = self.document.results.geometry()
        except Exception:  # noqa: BLE001 - it does not build: nothing to compare
            geometry = None
        active = self.document.active
        trials = tuple(sorted(self.document.trials_for(active).items())) if active else ()
        return active, id(geometry), self.settings().model_dump_json(), trials

    def _scope(self) -> tuple[dict[str, float], list[str]]:
        try:
            scope = self.document.results.build().variables()
        except Exception:  # noqa: BLE001 - it does not build: names without values
            scope = {}
        found = self.document.project.definition(self.document.active or "")
        names = [p.name for p in found[0].parameters] if found is not None else []
        return scope, names

    def _automatic(self) -> float | None:
        try:
            bbox = self.document.results.geometry().bbox()
        except Exception:  # noqa: BLE001
            return None
        if bbox is None:
            return None
        return MeshSettings().resolved({}, bbox).size

    def _fill_layers(self, settings: MeshSettings, scope, names, size: str) -> None:
        while self.layer_form.rowCount():
            self.layer_form.removeRow(0)
        self.layer_fields = {}
        for layer in self.document.project.layers:
            field = self._field(optional=True)
            field.scope, field.parameters = scope, names
            value = settings.layers.get(layer)
            field.setText("" if value is None else _format(value))
            field.setPlaceholderText(size)  # empty: the largest size
            field.setReadOnly(not self._editable)
            field.returnPressed.connect(lambda name=layer: self._set_layer(name))
            self.layer_form.addRow(f"Size on {layer}", field)
            self.layer_fields[layer] = field

    def _show_refinement(self, row: int) -> None:
        while self.refine_form.rowCount():
            self.refine_form.removeRow(0)  # (the layer box is taken out, not deleted)
        self.refine_fields = {}
        refine = self.settings().refine
        if not 0 <= row < len(refine):
            return
        chosen = refine[row]
        scope, names = self._scope()
        if isinstance(chosen, EdgesRefinement):
            self.refine_layer = QComboBox()
            self.refine_layer.addItems(list(self.document.project.layers))
            self.refine_layer.setCurrentText(chosen.layer)
            self.refine_layer.setEnabled(self._editable)
            self.refine_layer.activated.connect(lambda _i: self._set_refine_layer())
            self.refine_form.addRow("Layer", self.refine_layer)
        for name, label in chosen.FIELDS:
            field = self._field()
            field.scope, field.parameters = scope, names
            field.setText(_format(getattr(chosen, name)))
            field.setReadOnly(not self._editable)
            field.returnPressed.connect(lambda n=name: self._set_refine(n))
            self.refine_form.addRow(label, field)
            self.refine_fields[name] = field

    def refinement_marks(self) -> list[tuple]:
        """The point and box refinements as numbers, for the canvas."""
        try:
            resolved = self.settings().resolved(self._scope()[0], None)
        except ValueError:
            return []
        return [mark for mark in (r.mark() for r in resolved.refine) if mark is not None]

    # -- editing ---------------------------------------------------------------------

    def _field(self, optional: bool = False) -> ValueEdit:
        field = ValueEdit(optional=optional)
        field.set_unit("µm")
        return field

    def _change(self, **fields) -> bool:
        """Save the settings with ``fields`` changed (undoable), if every value in them
        comes to a number that is allowed (else the reason is reported)."""
        active = self.document.active
        settings = self.settings().model_copy(update=fields)

        def change() -> None:
            scope = self._scope()[0]
            if scope:  # (a component that does not build: checked when it does)
                settings.resolved(scope, None)  # raises ValueError, said as it is
            self.document.components.set_mesh(active, settings)

        return self._guard(change)

    def _value(self, field: ValueEdit):
        try:
            return field.value()
        except ValueError as exc:
            self.error.emit(str(exc))
            return ...

    def _set(self, name: str, field: ValueEdit, optional: bool = False) -> None:
        value = self._value(field)
        if value is ...:
            return
        if value is None and not optional:
            value = DEFAULT_GROWTH
        if value != getattr(self.settings(), name) and not self._change(**{name: value}):
            self.refresh()

    def _set_layer(self, layer: str) -> None:
        value = self._value(self.layer_fields[layer])
        if value is ...:
            return
        layers = dict(self.settings().layers)
        if value is None:
            layers.pop(layer, None)
        else:
            layers[layer] = value
        if layers != self.settings().layers and not self._change(layers=layers):
            self.refresh()

    def _replace(self, row: int, **fields) -> None:
        refine = list(self.settings().refine)
        try:
            refine[row] = refinement(**{**refine[row].model_dump(), **fields})
        except ValueError as exc:
            self.error.emit(str(exc))
            return
        if not self._change(refine=refine):
            self.refresh()

    def _set_refine(self, name: str) -> None:
        row = self.refinements.currentRow()
        value = self._value(self.refine_fields[name])
        if value is ... or value is None or row < 0:
            return
        if value != getattr(self.settings().refine[row], name):
            self._replace(row, **{name: value})

    def _set_refine_layer(self) -> None:
        row = self.refinements.currentRow()
        if row >= 0:
            self._replace(row, layer=self.refine_layer.currentText())

    def _add_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("Around a point", lambda: self.add("point"))
        menu.addAction("In a box", lambda: self.add("box"))
        menu.addAction("Along the edges of a layer", lambda: self.add("edges"))
        button = self.refine_actions.buttons["Add a refinement"]
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def add(self, kind: str) -> None:
        """A new refinement of ``kind``, in the middle of the component, a quarter of
        the automatic size; it is selected to be filled in."""
        try:
            bbox = self.document.results.geometry().bbox()
        except Exception:  # noqa: BLE001
            bbox = None
        x0, y0, x1, y1 = bbox or (0.0, 0.0, 100.0, 100.0)
        cx, cy = round((x0 + x1) / 2, 3), round((y0 + y1) / 2, 3)
        size = float(f"{(self._automatic() or 4) / 4:.2g}")
        dx, dy = (x1 - x0) / 8, (y1 - y0) / 8
        start = {  # a new one of each kind
            "point": {"x": cx, "y": cy, "radius": float(f"{size * 2:.2g}")},
            "box": {
                "x0": round(cx - dx, 3),
                "y0": round(cy - dy, 3),
                "x1": round(cx + dx, 3),
                "y1": round(cy + dy, 3),
            },
            "edges": {"layer": next(iter(self.document.project.layers), "device")},
        }
        refine = [*self.settings().refine, refinement(kind=kind, size=size, **start[kind])]
        if self._change(refine=refine):
            self.refinements.setCurrentRow(len(refine) - 1)

    def _remove(self) -> None:
        row = self.refinements.currentRow()
        refine = list(self.settings().refine)
        if 0 <= row < len(refine):
            del refine[row]
            self._change(refine=refine)

    # -- the preview -----------------------------------------------------------------

    def preview(self) -> meshing.Mesh | None:
        """Mesh the component as it is now and show it; None (and the reason, in the
        panel and in Messages) if that fails."""
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            build = self.document.results.build()
            self.mesh = meshing.generate(build.geometry, self.settings(), build.variables())
        except Exception as exc:  # noqa: BLE001 - shown in the panel
            self.mesh = None
            self.summary.setText(f"Not meshed: {exc}")
            self.error.emit(f"Mesh: {exc}")
            return None
        finally:
            QApplication.restoreOverrideCursor()
        self._preview_key = self._key()
        self.summary.setText(self.mesh.summary())
        self.shown_changed.emit()
        return self.mesh

    def clear_preview(self) -> None:
        self.mesh = None
        self._preview_key = None
        self.summary.setText("")
        self.shown_changed.emit()

    def preview_arrays(self):
        """The preview's nodes and triangles, or (None, None)."""
        if self.mesh is None:
            return None, None
        return self.mesh.nodes, self.mesh.triangles
