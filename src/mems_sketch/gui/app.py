"""Main window and application entry point (``mems-sketch`` or ``python -m mems_sketch.gui``).

The window is a frontend only: it shows and edits the project through
:class:`ProjectDocument`, which is the single way into the backend.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import klayout.db as kdb
from PySide6.QtCore import QRectF, QSettings, Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QToolButton,
)

from mems_sketch.core.component import to_dbu
from mems_sketch.core.shapes import Align, NodePath
from mems_sketch.export.base import available_exporters
from mems_sketch.gui.canvas import LayoutCanvas
from mems_sketch.gui.document import VIEW_MODES, ProjectDocument
from mems_sketch.gui.panels import (
    ComponentsPanel,
    ConstantsPanel,
    LayersPanel,
    MessagesPanel,
    ParametersPanel,
    PointsPanel,
    ShapeTree,
)
from mems_sketch.gui.properties import PropertyEditor
from mems_sketch.gui.views import ComponentView, EditorArea

OPEN_FILTER = "MEMS projects (project.yaml);;Legacy designs (*.mems)"
PRIMITIVES = [
    ("rect", "Rectangle"),
    ("circle", "Circle"),
    ("arc", "Arc / ring"),
    ("polygon", "Polygon"),
    ("path", "Path"),
]
OPERATIONS = [
    ("transform", "Transform"),
    ("union", "Union"),
    ("subtract", "Subtract"),
    ("intersect", "Intersect"),
    ("xor", "XOR"),
    ("offset", "Offset"),
    ("fillet", "Fillet"),
    ("layer_map", "Layer map"),
]


class MainWindow(QMainWindow):
    def __init__(self, document: ProjectDocument | None = None) -> None:
        super().__init__()
        self.document = document or ProjectDocument()
        # The Align tool: None, or the step ("own" / "target") with its path and candidates.
        self.align_step: str | None = None
        self._align_path: NodePath | None = None
        self._align_point: str | None = None
        self._candidates: list[tuple[str, float, float]] = []
        self._problems: list[str] = []
        self._restoring = False  # while opening a project, the tab layout is not saved

        self.area = EditorArea(self.document)
        self.setCentralWidget(self.area)
        self.components = ComponentsPanel(self.document)
        self.tree = ShapeTree(self.document)
        self.properties = PropertyEditor(self.document)
        self.parameters = ParametersPanel(self.document)
        self.layers = LayersPanel(self.document)
        self.constants = ConstantsPanel(self.document)
        self.points = PointsPanel(self.document)
        self.messages = MessagesPanel()
        self._build_docks()

        self.coordinates = QLabel()
        self.statusBar().addPermanentWidget(self.coordinates)
        self._build_actions()

        self.document.changed.connect(self.refresh)
        self.document.active_changed.connect(self._active_changed)
        self.document.file_changed.connect(self._update_title)
        self.document.component_renamed.connect(self.area.rename)
        self.area.current_changed.connect(self._view_activated)
        self.area.tabs_changed.connect(self._save_tabs)
        self.tree.selection_changed_paths.connect(self._tree_selected)
        self.tree.enabled_toggled.connect(
            lambda p, e: self._run(lambda: self.document.set_enabled(p, e))
        )
        self.tree.itemDoubleClicked.connect(self._tree_double_clicked)
        self.components.place_requested.connect(self.add_component)
        self.components.open_requested.connect(self.open_component)
        self.layers.visibility_changed.connect(self._set_layer_visible)
        self.messages.zoom_requested.connect(self._zoom_to_bbox)
        for panel in (
            self.properties,
            self.parameters,
            self.layers,
            self.constants,
            self.points,
            self.components,
        ):
            panel.error.connect(self.report_error)

        self.resize(1500, 950)
        self.refresh()
        self._update_title()

    # -- construction ------------------------------------------------------

    # -- the current tab ---------------------------------------------------

    @property
    def view(self) -> ComponentView:
        if self.area.current is None:
            self._render(self.area.open(self.document.active, anywhere=True))
        return self.area.current

    @property
    def canvas(self) -> LayoutCanvas:
        return self.view.canvas

    @property
    def selection(self) -> list[NodePath]:
        return self.view.selection

    @selection.setter
    def selection(self, paths: list[NodePath]) -> None:
        self.view.selection = paths

    @property
    def view_mode(self) -> str:
        return self.view.view_mode

    def open_component(self, name: str) -> None:
        """Open a component in a tab (or go to its tab), like opening a file."""
        if not self.document.exists(name):
            self.report_error(f"unknown component '{name}'")
            return
        self._render(self.area.open(name))

    def _render(self, view: ComponentView) -> ComponentView:
        if not getattr(view, "rendered", False):
            view.rendered = True
            view.refresh(self.layers.colors, self.layers.visible)
            view.canvas.clicked.connect(
                lambda x, y, additive, v=view: self._view_clicked(v, x, y, additive)
            )
            view.canvas.double_clicked.connect(
                lambda x, y, v=view: self._view_double_clicked(v, x, y)
            )
            view.canvas.cursor_moved.connect(self._cursor_moved)
        return view

    def _view_activated(self, view: ComponentView) -> None:
        """The user switched tabs (or panes): the panels follow the new current tab."""
        self.cancel_align(quiet=True)
        self._render(view)
        if self.document.active != view.component:
            self.document.set_active(view.component)  # emits active_changed
        else:
            self._refresh_panels()

    def _active_changed(self) -> None:
        """Another component became active (e.g. from the components panel)."""
        self._render(self.area.open(self.document.active, anywhere=True))
        self._refresh_panels()

    def _set_layer_visible(self, layer: str, visible: bool) -> None:
        for view in self.area.views():
            view.canvas.set_layer_visible(layer, visible)

    def split_view(self) -> None:
        view = self.area.split_view()
        if view is not None:
            self._render(view)

    def close_tab(self) -> None:
        if self.area.current is not None:
            self.area.close_view(self.area.current)

    def _save_tabs(self) -> None:
        if self.document.path is not None and not self._restoring:
            QSettings("mems-sketch", "mems-sketch").setValue(
                self._tabs_key(), json.dumps(self.area.layout_state())
            )

    def _restore_tabs(self) -> None:
        """Reopen the tabs that were open the last time this project was open."""
        if self.document.path is None:
            return
        try:
            state = json.loads(
                str(QSettings("mems-sketch", "mems-sketch").value(self._tabs_key(), ""))
            )
        except (json.JSONDecodeError, TypeError):
            return
        panes = [[n for n in names if self.document.exists(n)] for names in state["panes"]]
        panes = [names for names in panes if names]
        if not panes:
            return
        self.area.close_all()
        for index, names in enumerate(panes[:2]):
            if index:
                self.area.split_view()
                pane = self.area.panes[1]
                while pane.count():  # split_view copied the current tab; use the saved ones
                    pane.removeTab(0)
            for name in names:
                self._render(self.area.open(name, self.area.panes[index]))
        current = state.get("current")
        pane = self.area.panes[min(state.get("current_pane", 0), len(self.area.panes) - 1)]
        view = self.area.find(current, pane) if current else None
        if view is not None:
            self.area.set_current(view)

    def _tabs_key(self) -> str:
        return "tabs/" + str(Path(self.document.path).resolve())

    def _dock(self, title: str, widget, area) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(title)
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
        return dock

    def _build_docks(self) -> None:
        left, right = Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea
        components = self._dock("Components", self.components, left)
        shapes = self._dock("Shapes", self.tree, left)
        layers = self._dock("Layers", self.layers, left)
        properties = self._dock("Properties", self.properties, right)
        parameters = self._dock("Parameters", self.parameters, right)
        points = self._dock("Points", self.points, right)
        constants = self._dock("Process constants", self.constants, right)
        self.tabifyDockWidget(parameters, points)
        self.tabifyDockWidget(parameters, constants)
        parameters.raise_()
        messages = self._dock("Messages", self.messages, Qt.DockWidgetArea.BottomDockWidgetArea)
        vertical, horizontal = Qt.Orientation.Vertical, Qt.Orientation.Horizontal
        self.resizeDocks([components, shapes, layers], [240, 330, 200], vertical)
        self.resizeDocks([properties, parameters], [560, 220], vertical)
        self.resizeDocks([shapes, properties], [320, 360], horizontal)
        self.resizeDocks([messages], [110], vertical)

    def _action(self, text: str, slot, shortcut=None, menu: QMenu | None = None) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(slot)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        if menu is not None:
            menu.addAction(action)
        return action

    def _build_actions(self) -> None:
        bar = self.menuBar()
        file = bar.addMenu("&File")
        self._action("New project", self.new_project, QKeySequence.StandardKey.New, file)
        self._action("Open project…", self.open_project, QKeySequence.StandardKey.Open, file)
        self._action("Save", self.save_project, QKeySequence.StandardKey.Save, file)
        self._action("Save as…", self.save_project_as, QKeySequence.StandardKey.SaveAs, file)
        file.addSeparator()
        export = file.addMenu("Export")
        for mode, label in VIEW_MODES.items():
            self._action(
                f"{label} geometry…", lambda _=False, m=mode: self.export_file(m), menu=export
            )
        file.addSeparator()
        self._action("Quit", self.close, QKeySequence.StandardKey.Quit, file)

        edit = bar.addMenu("&Edit")
        self.undo_action = self._action(
            "Undo", self.document.undo, QKeySequence.StandardKey.Undo, edit
        )
        self.redo_action = self._action(
            "Redo", self.document.redo, QKeySequence.StandardKey.Redo, edit
        )
        edit.addSeparator()
        self._action("Duplicate", self.duplicate, "Ctrl+D", edit)
        self._action("Delete", self.delete, QKeySequence.StandardKey.Delete, edit)
        self._action("Unwrap operation", self.unwrap, "Ctrl+Shift+U", edit)
        self._action("Make component from selection…", self.make_component, "Ctrl+K", edit)
        self._action("Unpack component", self.unpack, "Ctrl+Shift+K", edit)
        edit.addSeparator()
        self._action("Align…", self.start_align, "Ctrl+L", edit)
        self._action("Remove alignment", self.remove_alignment, None, edit)
        self._action("Cancel", self.cancel_align, "Esc", edit)

        insert = bar.addMenu("&Insert")
        self.primitive_menu = insert.addMenu("Primitive")
        for kind, label in PRIMITIVES:
            self._action(
                label, lambda _=False, k=kind: self.add_primitive(k), menu=self.primitive_menu
            )
        self.component_menu = insert.addMenu("Component")
        self.component_menu.aboutToShow.connect(self._fill_component_menu)

        operations = bar.addMenu("&Operations")
        for op, label in OPERATIONS:
            self._action(label, lambda _=False, o=op: self.wrap(o), menu=operations)

        view = bar.addMenu("&View")
        self._action("Fit", lambda: self.canvas.fit(), "F", view)
        self._action("Recompile and check", self.refresh, "F5", view)
        view.addSeparator()
        self._action("Open top component", self._edit_top, "Ctrl+T", view)
        self._action("Split view", self.split_view, "Ctrl+\\", view)
        self._action("Merge split view", self.area.unsplit, None, view)
        self._action("Close tab", self.close_tab, QKeySequence.StandardKey.Close, view)
        panels = view.addMenu("Panels")
        for dock in self.findChildren(QDockWidget):
            panels.addAction(dock.toggleViewAction())

        tools = self.addToolBar("Main")
        tools.setObjectName("main-toolbar")
        for text, slot in (
            ("New", self.new_project),
            ("Open", self.open_project),
            ("Save", self.save_project),
        ):
            tools.addAction(text, slot)
        tools.addSeparator()
        tools.addAction(self.undo_action)
        tools.addAction(self.redo_action)
        tools.addSeparator()
        for label, menu in (("Primitive", self.primitive_menu), ("Component", self.component_menu)):
            button = QToolButton()
            button.setText(label)
            button.setMenu(menu)
            button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            tools.addWidget(button)
        tools.addSeparator()
        for op in ("union", "subtract", "intersect", "offset", "fillet", "transform"):
            tools.addAction(dict(OPERATIONS)[op], lambda o=op: self.wrap(o))
        tools.addAction("Align", self.start_align)
        tools.addAction("Make component", self.make_component)
        tools.addSeparator()
        tools.addWidget(QLabel(" View: "))
        self.mode_box = QComboBox()
        for mode, label in VIEW_MODES.items():
            self.mode_box.addItem(label, mode)
        self.mode_box.currentIndexChanged.connect(self._mode_changed)
        tools.addWidget(self.mode_box)
        tools.addAction("Fit", lambda: self.canvas.fit())
        tools.addAction("Split", self.split_view)

    def _fill_component_menu(self) -> None:
        self.component_menu.clear()
        for name in self.document.component_names():
            if name != self.document.active:
                self._action(
                    name, lambda _=False, n=name: self.add_component(n), menu=self.component_menu
                )

    # -- refresh -----------------------------------------------------------

    def refresh(self) -> None:
        """Recompile every open tab and update the panels (after any change)."""
        self.cancel_align(quiet=True)
        for view in self.area.views():
            if not self.document.exists(view.component):
                self.area.close_view(view)  # its component was deleted (or undone)
        if self.area.current is None or self.area.current.component != self.document.active:
            # e.g. undo went back to another component: go to its tab, wherever it is
            self._render(self.area.open(self.document.active, anywhere=True))
        self.layers.refresh()
        self.constants.refresh()
        for view in self.area.views():
            view.rendered = True
            view.refresh(self.layers.colors, self.layers.visible)
        self.area.update_titles()
        self._problems = self.document.problems()
        self._refresh_panels()

    def _refresh_panels(self) -> None:
        """Show the current tab in the panels, toolbar and title."""
        view = self.view
        self.parameters.refresh()
        self.points.refresh()
        self.components.refresh()
        self.mode_box.blockSignals(True)
        self.mode_box.setCurrentIndex(self.mode_box.findData(view.view_mode))
        self.mode_box.blockSignals(False)
        self.tree.rebuild([p for p in view.selection if self._exists(p)])
        self._show_messages()
        self.undo_action.setEnabled(self.document.can_undo())
        self.redo_action.setEnabled(self.document.can_redo())
        self.undo_action.setText(f"Undo {self.document.undo_text()}".strip())
        self.redo_action.setText(f"Redo {self.document.redo_text()}".strip())
        self._update_title()

    def _show_messages(self, *extra: str) -> None:
        view = self.view
        errors = [*extra, *view.errors, *(p for p in self._problems if p not in view.errors)]
        self.messages.show_messages(errors, view.violations)

    def _update_overlay(self) -> None:
        view = self.view
        markers = [v.bbox_um for v in view.violations if v.bbox_um]
        self.canvas.show_overlay(self.document.highlight(view.selection), markers)
        try:
            declared = [(n, x, y) for n, (x, y) in self.document.declared_points().items()]
        except Exception:  # noqa: BLE001 - the messages panel shows why
            declared = []
        self.canvas.show_points("declared", declared, labels=True)
        selected = self.document.node_points(view.selection[0]) if len(view.selection) == 1 else []
        self.canvas.show_points("selected", [] if self.align_step else selected)
        self.canvas.show_points("pick", self._candidates if self.align_step else [])

    def _exists(self, path: NodePath) -> bool:
        try:
            self.document.node(path)
            return True
        except KeyError:
            return False

    def _update_title(self) -> None:
        project = self.document.project
        where = str(self.document.path) if self.document.path else "not saved"
        star = "*" if self.document.dirty else ""
        doing = "viewing" if self.document.read_only else "editing"
        self.setWindowTitle(
            f"{star}{project.name} — {doing} {self.document.active} — {where} — MEMS Sketch"
        )

    # -- selection ---------------------------------------------------------

    def _tree_selected(self, paths: list[NodePath]) -> None:
        self.selection = paths
        self.properties.show_node(paths[0] if len(paths) == 1 else None)
        self._update_overlay()

    def _cursor_moved(self, x: float, y: float) -> None:
        text = f"x {x:.3f} µm   y {y:.3f} µm"
        if self.align_step:
            nearest = self._nearest_candidate(x, y)
            if nearest is not None:
                text = f"{nearest}   {text}"
        self.coordinates.setText(text)

    def _view_clicked(self, view: ComponentView, x: float, y: float, additive: bool) -> None:
        if view is not self.area.current:
            self.area.set_current(view)  # clicking in the other pane switches to it
        self._canvas_clicked(x, y, additive)

    def _view_double_clicked(self, view: ComponentView, x: float, y: float) -> None:
        """Double-clicking a placed component opens it in a tab."""
        hit = self._hit(view, x, y)
        if hit is not None:
            self._open_reference(hit, view.component)

    def _tree_double_clicked(self, item, _column: int) -> None:
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path is not None:
            self._open_reference(path, self.document.active)

    def _open_reference(self, path: NodePath, component: str) -> None:
        target = self.document.reference_target(path, component)
        if target is not None:
            self.open_component(target)

    def _hit(self, view: ComponentView, x: float, y: float) -> NodePath | None:
        point = kdb.Point(to_dbu(x), to_dbu(y))
        probe = kdb.Region(kdb.Box(point.x - 1, point.y - 1, point.x + 1, point.y + 1))
        return next(
            (p for p, region in reversed(view.node_regions) if not (region & probe).is_empty()),
            None,
        )

    def _canvas_clicked(self, x: float, y: float, additive: bool) -> None:
        if self.align_step:
            self._pick(x, y)
            return
        hit = self._hit(self.view, x, y)
        if hit is None:
            paths = self.selection if additive else []
        elif additive:
            paths = (
                [p for p in self.selection if p != hit]
                if hit in self.selection
                else [*self.selection, hit]
            )
        else:
            paths = [hit]
        self.tree.select_paths(paths)

    def _zoom_to_bbox(self, bbox: tuple[float, float, float, float]) -> None:
        x0, y0, x1, y1 = bbox
        size = max(x1 - x0, y1 - y0, 5.0)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        self.canvas.zoom_to(QRectF(cx - size * 2, cy - size * 2, size * 4, size * 4))

    def _mode_changed(self) -> None:
        view = self.view
        view.view_mode = self.mode_box.currentData()
        view.refresh(self.layers.colors, self.layers.visible)
        self._show_messages()
        self._update_overlay()

    def _edit_top(self) -> None:
        self.open_component(self.document.project.top)

    # -- commands ----------------------------------------------------------

    def report_error(self, message: str) -> None:
        self.statusBar().showMessage(message, 8000)
        self._show_messages(message)

    def _run(self, action) -> tuple[bool, object]:
        """Run a command; report failures to the user. Returns (succeeded, result)."""
        try:
            return True, action()
        except Exception as exc:  # noqa: BLE001 - reported to the user
            self.report_error(str(exc))
            return False, None

    def _select_result(self, action) -> None:
        ok, path = self._run(action)
        if ok and path:
            self.tree.select_paths([path])

    def add_primitive(self, kind: str) -> None:
        self._select_result(lambda: self.document.add_primitive(kind))

    def add_component(self, name: str) -> None:
        self._select_result(lambda: self.document.add_component(name))

    def wrap(self, operation: str) -> None:
        self._select_result(lambda: self.document.wrap(self.selection, operation))

    def make_component(self) -> None:
        if not self.selection:
            self.report_error("select the shapes to turn into a component first")
            return
        name, ok = QInputDialog.getText(self, "Make component", "Name of the new component:")
        if ok and name.strip():
            self._select_result(lambda: self.document.make_component(self.selection, name.strip()))

    # -- the Align tool ----------------------------------------------------

    def start_align(self) -> None:
        """Pick a point of the selected shape, then the point to align it to."""
        if len(self.selection) != 1:
            self.report_error("select the one shape to align first")
            return
        path = self.selection[0]
        candidates = self.document.node_points(path)
        if not candidates:
            self.report_error("this shape has no points to align (does it have geometry?)")
            return
        self.align_step, self._align_path, self._candidates = "own", path, candidates
        name = self.document.node(path).name or "the shape"
        self._prompt(f"Align {name}: click the point of {name} to align (Esc cancels)")
        self._update_overlay()

    def _pick(self, x: float, y: float) -> None:
        picked = self._nearest_candidate(x, y)
        if picked is None:
            self.statusBar().showMessage("Click on one of the marked points (Esc cancels)", 5000)
            return
        if self.align_step == "own":
            targets = [
                (name, tx, ty) for name, _, tx, ty in self.document.align_targets(self._align_path)
            ]
            if not targets:
                self.cancel_align(quiet=True)
                self.report_error("there is no other named shape here to align to")
                return
            self.align_step, self._align_point, self._candidates = "target", picked, targets
            self._prompt(f"Now click the point to put {picked} on (Esc cancels)")
            self._update_overlay()
            return
        path, point = self._align_path, self._align_point
        self.cancel_align(quiet=True)
        ok, _ = self._run(lambda: self.document.set_align(path, Align(point=point, to=picked)))
        if ok:
            self.statusBar().showMessage(f"Aligned {point} to {picked}", 5000)

    def _nearest_candidate(self, x: float, y: float, pixels: float = 12) -> str | None:
        reach = pixels / self.canvas.pixels_per_um()
        best, best_distance = None, reach
        for name, px, py in self._candidates:
            distance = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
            if distance <= best_distance:
                best, best_distance = name, distance
        return best

    def cancel_align(self, quiet: bool = False) -> None:
        if self.align_step is None:
            return
        self.align_step, self._align_path, self._align_point = None, None, None
        self._candidates = []
        self.canvas.unsetCursor()
        if not quiet:
            self.statusBar().showMessage("Align cancelled", 3000)
        self._update_overlay()

    def _prompt(self, text: str) -> None:
        self.canvas.setCursor(Qt.CursorShape.CrossCursor)
        self.statusBar().showMessage(text)

    def remove_alignment(self) -> None:
        if len(self.selection) == 1:
            path = self.selection[0]
            self._run(lambda: self.document.set_align(path, None))

    def unpack(self) -> None:
        if len(self.selection) == 1:
            self._select_result(lambda: self.document.unpack(self.selection[0]))

    def unwrap(self) -> None:
        if len(self.selection) == 1:
            self._run(lambda: self.document.unwrap(self.selection[0]))

    def duplicate(self) -> None:
        if len(self.selection) == 1:
            self._select_result(lambda: self.document.duplicate(self.selection[0]))

    def delete(self) -> None:
        if self.selection:
            paths, self.selection = self.selection, []
            self._run(lambda: self.document.remove_nodes(paths))

    # -- files -------------------------------------------------------------

    def _confirm_discard(self) -> bool:
        if not self.document.dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            "Save changes to the current project first?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save_project()
        return answer == QMessageBox.StandardButton.Discard

    def new_project(self) -> None:
        if self._confirm_discard():
            self._save_tabs()
            self.area.close_all()
            self.document.new()

    def open_project(self, path: str | None = None) -> None:
        if not self._confirm_discard():
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, "Open project", self._last_dir(), OPEN_FILTER
            )
        if not path:
            return
        self._save_tabs()
        if self._run(lambda: self._open(path))[0]:
            self._remember_dir(path)
            if self.document.path is None:
                self.statusBar().showMessage(
                    "Imported a legacy design: use Save as… to store it as a project folder", 10000
                )

    def _open(self, path: str) -> None:
        self._restoring = True
        try:
            self.document.open(path)  # raises before anything changes if the file is bad
            self.area.close_all()
            self.refresh()
            self._restore_tabs()
        finally:
            self._restoring = False
        self._save_tabs()

    def save_project(self) -> bool:
        if self.document.path is None:
            return self.save_project_as()
        return self._run(self.document.save)[0]

    def save_project_as(self) -> bool:
        folder = QFileDialog.getExistingDirectory(
            self, "Save project in folder (create a new folder for a new project)", self._last_dir()
        )
        if not folder:
            return False
        target = Path(folder)
        if (target / "project.yaml").exists() and target != self.document.path:
            answer = QMessageBox.question(
                self, "Replace project", f"{target} already contains a project. Replace it?"
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        self._remember_dir(str(target / "project.yaml"))
        return self._run(lambda: self.document.save(target))[0]

    def export_file(self, mode: str) -> None:
        exporters = available_exporters()
        filters = ";;".join(
            f"{name.upper()} (*{cls.file_extension})" for name, cls in exporters.items()
        )
        path, chosen = QFileDialog.getSaveFileName(
            self, f"Export {VIEW_MODES[mode].lower()} geometry", self._last_dir(), filters
        )
        if not path:
            return
        extension = chosen[chosen.find("*") + 1 : chosen.find(")")]
        if not Path(path).suffix:
            path += extension
        if self._run(lambda: self.document.export(path, mode))[0]:
            self._remember_dir(path)
            self.statusBar().showMessage(f"Exported {path}", 5000)

    def _last_dir(self) -> str:
        return str(QSettings("mems-sketch", "mems-sketch").value("last_dir", str(Path.home())))

    def _remember_dir(self, path: str) -> None:
        QSettings("mems-sketch", "mems-sketch").setValue("last_dir", str(Path(path).parent))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._confirm_discard():
            self._save_tabs()
            event.accept()
        else:
            event.ignore()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("MEMS Sketch")
    window = MainWindow()
    if len(argv) > 1:
        window.open_project(argv[1])
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
