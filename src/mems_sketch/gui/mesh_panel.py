"""The Mesh tool window: how the component being edited is meshed (#50), and a
preview of its mesh on the canvas (#72).

- **Sizes:** the largest element (empty: automatic), how fast sizes may grow
  from one element to the next, and a size per layer (empty: the largest).
- **Refinements:** places the mesh is finer: around a point, in a box, along
  the edges of a layer. They are drawn on the canvas, dashed, while the
  window is open.
- **Elements:** first or second order, and in 3D how many prisms go through
  each layer's thickness (0: free tetrahedra) (#346).
- **Preview:** *Bake* makes the mesh in the background (another process, so
  the window stays usable and *Cancel* stops it) and draws it over the layers,
  with its element count and its worst element's quality; *Export…* saves it,
  using the baked mesh when it is current (#351). When the design or the
  settings change, the mesh is kept but shown as out of date; one that took
  under a second is baked again by itself shortly after the last edit.

Every value is an expression like any other, over the component's parameters
and the process constants, and is saved with the component (undoable).
A library's component shows its settings without letting them be changed.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMenu,
    QPushButton,
    QScrollArea,
    QSpinBox,
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
from mems_sketch.mesh_bake import Bake, BakeJob

LIVE_SECONDS = 1.0  # a mesh quicker than this is baked again by itself after an edit
LIVE_DELAY_MS = 400  # after the last edit
POLL_MS = 50  # how often a running bake is asked how it is getting on
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
        self.stale = False  # the preview was made from an earlier design or settings
        self._bake: Bake | None = None  # the bake running
        self._bake_key: tuple | None = None  # what it is made from
        self._bake_seconds: float | None = None  # how long the last bake took
        self._step = ""
        self._failed = False
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_MS)
        self._poll_timer.timeout.connect(self._poll)
        self._live_timer = QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.setInterval(LIVE_DELAY_MS)
        self._live_timer.timeout.connect(self._live_bake)
        self._editable = False

        self.size = self._field(optional=True)
        self.size.returnPressed.connect(lambda: self._set("size", self.size, optional=True))
        self.growth = self._field()
        self.growth.set_unit("")
        self.growth.returnPressed.connect(lambda: self._set("growth", self.growth))
        self.automatic = QCheckBox("Finer in narrow parts, gaps and curves")
        self.automatic.setToolTip(
            "The automatic size map: about *Across* elements across each narrow beam "
            "and small gap, and enough along each curve"
        )
        self.automatic.clicked.connect(lambda on: self._change_or_refresh(automatic=on))
        self.across = self._field()
        self.across.set_unit("")
        self.across.set_help("Elements across the narrowest part of a beam or a gap")
        self.across.returnPressed.connect(lambda: self._set("across", self.across))
        sizes = widgets.Card(
            "Sizes",
            help="*Size* is the largest element (µm); empty, it is a twentieth of the "
            "component's smaller side. Elements grow by at most *Growth* from one to "
            "the next, so sizes change gradually where a fine region meets a coarse "
            "one. A layer's size applies on that layer.\n\n"
            "*Automatic* makes the mesh finer where the geometry needs it: *Across* "
            "elements across each narrow beam and small gap (e.g. between comb "
            "fingers), and enough along tight curves. *Show the size map* draws "
            "where, before meshing.",
        )
        form = QFormLayout()
        form.addRow("Size", self.size)
        form.addRow("Growth", self.growth)
        form.addRow("Automatic", self.automatic)
        form.addRow("Across", self.across)
        sizes.body.addLayout(form)
        layer_box = QWidget()  # (a widget of its own: its rows come and go)
        self.layer_form = QFormLayout(layer_box)
        self.layer_form.setContentsMargins(0, 0, 0, 0)
        sizes.body.addWidget(layer_box)
        self.layer_fields: dict[str, ValueEdit] = {}

        self.order = QComboBox()
        self.order.addItems(["First (linear)", "Second (quadratic)"])
        self.order.setToolTip(
            "Second-order elements have a node mid-edge too. First-order ones are far "
            "too stiff in bending: a spring's stiffness or a resonator's frequency "
            "comes out tens of percent off unless the mesh is very fine"
        )
        self.order.activated.connect(lambda index: self._change_or_refresh(order=index + 1))
        self.through = QSpinBox()
        self.through.setRange(0, 50)
        self.through.setSpecialValueText("Free tetrahedra")
        self.through.setSuffix(" prisms")
        self.through.setToolTip(
            "3D: elements through each layer's thickness, the 2D mesh extruded into "
            "prisms; 0, the layers filled with free tetrahedra instead"
        )
        self.through.editingFinished.connect(self._set_through)
        elements = widgets.Card(
            "Elements",
            help="*Order*: second-order elements (a node mid-edge) bend as they should; "
            "first-order ones need a much finer mesh for the same answer.\n\n"
            "*Through each layer* (3D): each layer meshed in this many prisms through "
            "its thickness, well shaped in thin films with far fewer elements than "
            "tetrahedra; 0 fills the layers with tetrahedra instead.",
        )
        element_form = QFormLayout()
        element_form.addRow("Order", self.order)
        element_form.addRow("Through each layer", self.through)
        elements.body.addLayout(element_form)

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

        self.mesh_button = QPushButton("Bake")
        self.mesh_button.setToolTip(
            "Make the mesh in the background and show it on the canvas; the window "
            "stays usable meanwhile"
        )
        self.mesh_button.clicked.connect(lambda: self.start_bake())
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setToolTip("Stop the mesh being made")
        self.cancel_button.clicked.connect(self.cancel_bake)
        self.cancel_button.hide()
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear_preview)
        self.live = QCheckBox("Bake again after each change, when it is quick")
        self.live.setChecked(True)
        self.live.setToolTip(
            f"A mesh that took under {LIVE_SECONDS:g} s is made again by itself shortly "
            "after the last change; a slower one is only marked out of date"
        )
        self.show_map = QCheckBox("Show the size map")
        self.show_map.setToolTip(
            "Where the automatic sizes make the mesh finer: a circle as wide as the "
            "element there, finer ones darker"
        )
        self.show_map.toggled.connect(lambda _on: self.shown_changed.emit())
        self._size_map: list | None = None
        self.export_button = QPushButton("Export…")
        self.export_button.clicked.connect(self.export_requested.emit)
        self.summary = QLabel()
        self.summary.setObjectName("muted")
        self.summary.setWordWrap(True)
        preview = widgets.Card(
            "Preview",
            help="The mesh as it would be exported, made in the background: in 2D drawn "
            "over the layers, in 3D (the layers extruded through the stack) counted. "
            "After a change it is kept but out of date (drawn faintly); a quick one is "
            "baked again by itself. Export uses it while it is current. Quality is gmsh's: 1 "
            "for a regular triangle or tetrahedron, near 0 for a sliver; prisms in a "
            "thin film score low for being flat, which is fine.",
        )
        self.dimensions = QComboBox()
        self.dimensions.addItems(["2D", "3D"])
        self.dimensions.setToolTip(
            "2D: the layers' faces, drawn on the canvas. 3D: the layers extruded "
            "through the stack (their z and thickness in the Process tab): counted, "
            "not drawn"
        )
        buttons = QHBoxLayout()
        self.dimensions.currentTextChanged.connect(lambda _text: self._check_current())
        for button in (self.dimensions, self.mesh_button, self.cancel_button, self.clear_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.export_button)
        preview.body.addLayout(buttons)
        preview.body.addWidget(self.live)
        preview.body.addWidget(self.show_map)
        preview.body.addWidget(self.summary)

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        for card in (sizes, elements, refine, preview):
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
        for field in (self.size, self.growth, self.across):
            field.scope, field.parameters = scope, names
            field.setReadOnly(not self._editable)
        self.automatic.setChecked(settings.automatic)
        self.automatic.setEnabled(self._editable)
        self.across.setText(_format(settings.across))
        self.across.setEnabled(settings.automatic)
        self.order.setCurrentIndex(settings.order - 1)
        self.through.blockSignals(True)
        self.through.setValue(settings.through)
        self.through.blockSignals(False)
        for control in (self.order, self.through):
            control.setEnabled(self._editable)
        self._size_map = None  # made again when shown
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
        self._check_current()

    def _check_current(self) -> None:
        """Whether the preview is of the design and settings as they are: if not,
        it is kept but said to be out of date, and made again soon if that is
        quick (see LIVE_SECONDS)."""
        if self.mesh is not None:
            self.stale = self._preview_key != self._key()
            quick = self._bake_seconds is not None and self._bake_seconds < LIVE_SECONDS
            if self.stale and quick and self.live.isChecked():
                self._live_timer.start()  # (again at each edit: from the last one)
            if self._bake is None:
                self._show_summary()
        self.shown_changed.emit()

    def _key(self) -> tuple:
        try:
            geometry = self.document.results.geometry()
        except Exception:  # noqa: BLE001 - it does not build: nothing to compare
            geometry = None
        active = self.document.active
        trials = tuple(sorted(self.document.trials_for(active).items())) if active else ()
        dimensions = self.dimensions.currentText()
        heights = None
        if dimensions == "3D":
            try:
                heights = tuple(sorted(self.document.project.process.heights().items()))
            except ValueError:
                heights = "unknown"
        settings = self.settings().model_dump_json()
        return active, id(geometry), settings, trials, dimensions, heights

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

    def start_bake(self) -> bool:
        """Start making the mesh of the component as it is now, in another process
        (a bake already running is stopped first); False (and the reason, in the
        panel and in Messages) if it cannot start."""
        self._live_timer.stop()
        if self._bake is not None:
            self._bake.cancel()
            self._bake = None
        try:
            build = self.document.results.build()
            heights = None
            if self.dimensions.currentText() == "3D":
                heights = meshing.stack_heights(self.document.project.process)
            job = BakeJob(
                build.geometry,
                self.settings(),
                build.variables(),
                heights,
                meshing.named_shapes(build),
            )
            self._bake = Bake(job)
        except Exception as exc:  # noqa: BLE001 - shown in the panel
            self._fail(str(exc))
            return False
        self._bake_key = self._key()
        self._failed = False
        self._step = "starting"
        self._poll_timer.start()
        self._buttons()
        self._show_summary()
        return True

    def cancel_bake(self) -> None:
        if self._bake is not None:
            self._bake.cancel()
            self._bake = None
            self._poll_timer.stop()
            self._buttons()
            self._show_summary()
            if self.mesh is None:
                self.summary.setText("Cancelled.")

    @property
    def baking(self) -> bool:
        return self._bake is not None

    def _poll(self) -> None:
        bake = self._bake
        if bake is None:
            self._poll_timer.stop()
            return
        for kind, value in bake.poll():
            if kind == "step":
                self._step = str(value)
            elif kind == "done":
                self.mesh = value
                meshing.keep(value)  # an export of the same design uses it again
                self._preview_key = self._bake_key
                self._bake_seconds = bake.seconds
                self.stale = self._preview_key != self._key()
            elif kind == "error":
                self._fail(str(value))
        if bake.finished:
            self._bake = None
            self._poll_timer.stop()
            self._buttons()
            if not self._failed:
                self._show_summary()
                self.shown_changed.emit()
                if self.stale and self.live.isChecked() and self._bake_seconds < LIVE_SECONDS:
                    self._live_timer.start()  # it changed while baking
        else:
            self._show_summary()

    def _fail(self, reason: str) -> None:
        self._failed = True
        self.summary.setText(f"Not meshed: {reason}")
        self.error.emit(f"Mesh: {reason}")

    def _live_bake(self) -> None:
        if self.stale and self.isVisible() and self._bake is None:
            self.start_bake()

    def _buttons(self) -> None:
        self.cancel_button.setVisible(self._bake is not None)
        self.mesh_button.setText("Bake again" if self._bake is not None else "Bake")

    def _show_summary(self) -> None:
        parts = []
        if self._bake is not None:
            parts.append(f"Baking: {self._step}… ({self._bake.seconds:.0f} s)")
        if self.mesh is not None:
            text = self.mesh.summary()
            parts.append(
                f"Out of date (the design or settings changed): {text}" if self.stale else text
            )
        self.summary.setText("\n".join(parts))

    def preview(self, timeout: float = 600.0) -> meshing.Mesh | None:
        """Bake the mesh and wait for it (the window still answers meanwhile): the
        mesh, or None (and the reason, in the panel and in Messages)."""
        if not self.start_bake():
            return None
        deadline = time.monotonic() + timeout
        while self._bake is not None and time.monotonic() < deadline:
            QApplication.processEvents()
            self._poll()
            time.sleep(0.01)
        if self._bake is not None:
            self.cancel_bake()
            return None
        return None if self._failed else self.mesh

    def clear_preview(self) -> None:
        self.mesh = None
        self._preview_key = None
        self.stale = False
        self._live_timer.stop()
        self.summary.setText("")
        self.shown_changed.emit()

    def size_map_marks(self) -> list[tuple[float, float, float]]:
        """The automatic size map to draw: (x, y, size) at each corner that needs a
        finer mesh, while *Show the size map* is on (and the map is)."""
        settings = self.settings()
        if not self.show_map.isChecked() or not settings.automatic:
            return []
        if self._size_map is None:
            from mems_sketch.mesh_sizes import rings_of, size_map

            try:
                build = self.document.results.build()
                geometry = build.geometry
                resolved = settings.resolved(build.variables(), geometry.bbox())
            except Exception:  # noqa: BLE001 - nothing to show until it builds
                return []
            largest = max([resolved.size, *resolved.layers.values()])
            points = size_map(rings_of(geometry), resolved.across, largest)
            self._size_map = [(p.x, p.y, p.size) for p in points]
        return self._size_map

    def _set_through(self) -> None:
        if self.through.value() != self.settings().through:
            self._change_or_refresh(through=self.through.value())

    def _change_or_refresh(self, **fields) -> None:
        if not self._change(**fields):
            self.refresh()

    def preview_arrays(self):
        """The preview's nodes and triangles, or (None, None) (none, or a 3D mesh)."""
        if self.mesh is None or self.mesh.dim != 2:
            return None, None
        return self.mesh.nodes, self.mesh.corners  # (second order: its corners only)
