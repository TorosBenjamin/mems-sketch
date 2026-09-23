"""Main window and application entry point (``mems-sketch`` or ``python -m mems_sketch.gui``).

The window is a frontend only: it shows and edits the project through
:class:`ProjectDocument`, which is the single way into the backend.
"""

from __future__ import annotations

import sys
from pathlib import Path

import klayout.db as kdb
from PySide6.QtCore import QRectF, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
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

from mems_sketch.core.shapes import NodePath
from mems_sketch.export.base import available_exporters
from mems_sketch.gui.canvas import DEFAULT_THEME, THEMES, LayoutCanvas
from mems_sketch.gui.document import VIEW_MODES, ProjectDocument
from mems_sketch.gui.editor_state import load_state, save_state
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
from mems_sketch.gui.tools import TOOLS, AlignTool, Tool, probe
from mems_sketch.gui.views import ComponentView, EditorArea

STATE_SAVE_DELAY_MS = 1000  # the editor state is written this long after the last change
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
        self._problems: list[str] = []
        self._restoring = False  # while opening a project, the editor state is not saved
        self.rulers: dict[str, list[tuple[float, float, float, float]]] = {}  # per component
        settings = QSettings("mems-sketch", "mems-sketch")
        theme = settings.value("canvas/theme", DEFAULT_THEME)
        self.canvas_theme = theme if theme in THEMES else DEFAULT_THEME
        self.tools: dict[str, Tool] = {cls.name: cls(self) for cls in TOOLS}
        self.tool: Tool = self.tools["select"]
        self.tool.reset()
        self._state_timer = QTimer(self)
        self._state_timer.setSingleShot(True)
        self._state_timer.setInterval(STATE_SAVE_DELAY_MS)
        self._state_timer.timeout.connect(self.save_editor_state)

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
        self.area.tabs_changed.connect(self.state_changed)
        self.tree.selection_changed_paths.connect(self._tree_selected)
        self.tree.enabled_toggled.connect(
            lambda p, e: self._run(lambda: self.document.set_enabled(p, e))
        )
        self.tree.itemDoubleClicked.connect(self._tree_double_clicked)
        self.components.place_requested.connect(self.add_component)
        self.components.open_requested.connect(self.open_component)
        self.layers.visibility_changed.connect(self._set_layer_visible)
        self.tree.collapse_changed.connect(self.state_changed)
        self.components.collapse_changed.connect(self.state_changed)
        self.document.changed.connect(self.state_changed)  # e.g. trial values
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
        saved_tool = settings.value("tool", "select")
        self.set_tool(saved_tool if saved_tool in self.tools else "select")

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
            canvas = view.canvas
            canvas.pressed.connect(lambda x, y, m, v=view: self._pressed(v, x, y, m))
            canvas.moved.connect(lambda x, y, m, left, v=view: self._moved(v, x, y, m, left))
            canvas.released.connect(lambda x, y, m, v=view: self._released(v, x, y, m))
            canvas.double_clicked.connect(lambda x, y, v=view: self._view_double_clicked(v, x, y))
            canvas.cursor_moved.connect(self._cursor_moved)
            canvas.nudged.connect(self.nudge)
            canvas.view_changed.connect(self.state_changed)
            canvas.set_theme(self.canvas_theme)
            canvas.left_pans = self.tool.name == "hand"
            canvas.set_tool_cursor(self.tool.cursor)
            canvas.show_rulers(self.rulers.get(view.component, []))
        return view

    def _view_activated(self, view: ComponentView) -> None:
        """The user switched tabs (or panes): the panels follow the new current tab."""
        for tool in self.tools.values():
            tool.reset()  # what a tool was doing belongs to the previous tab
        self._render(view)
        self.state_changed()
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

    def set_canvas_theme(self, theme: str) -> None:
        """Light or dark canvas background for every tab; remembered for next time."""
        self.canvas_theme = theme
        for view in self.area.views():
            view.canvas.set_theme(theme)
        self.dark_action.setChecked(theme == "dark")
        QSettings("mems-sketch", "mems-sketch").setValue("canvas/theme", theme)
        self._update_overlay()

    def _toggle_dark(self, checked: bool) -> None:
        self.set_canvas_theme("dark" if checked else "light")

    def split_view(self) -> None:
        view = self.area.split_view()
        if view is not None:
            self._render(view)

    def close_tab(self) -> None:
        if self.area.current is not None:
            self.area.close_view(self.area.current)

    # -- editor state (see mems_sketch.gui.editor_state) -------------------

    def state_changed(self) -> None:
        """Something worth remembering changed: save the editor state a moment later."""
        if self.document.path is not None and not self._restoring:
            self._state_timer.start()

    def editor_state(self) -> dict:
        """How the project is being looked at: tabs, views, rulers, tree and layer state."""
        panes = []
        for pane in self.area.panes:
            tabs = []
            for i in range(pane.count()):
                view = pane.widget(i)
                zoom, x, y = view.canvas.view_state()
                tabs.append(
                    {
                        "component": view.component,
                        "mode": view.view_mode,
                        "zoom": zoom,
                        "center": [x, y],
                        "selection": [[list(step) for step in path] for path in view.selection],
                    }
                )
            panes.append({"tabs": tabs, "current": pane.currentIndex()})
        current = self.area.current
        return {
            "panes": panes,
            "current_pane": self.area.panes.index(self.area.pane_of(current)) if current else 0,
            "rulers": {c: [list(r) for r in rs] for c, rs in self.rulers.items() if rs},
            "hidden_layers": sorted(n for n, shown in self.layers.visible.items() if not shown),
            "collapsed": {
                "components": sorted(self.components.collapsed),
                "shapes": {
                    c: [[list(step) for step in p] for p in sorted(paths)]
                    for c, paths in self.tree.collapsed.items()
                    if paths
                },
            },
            "trials": {c: dict(v) for c, v in self.document.trials.items() if v},
        }

    def save_editor_state(self) -> None:
        self._state_timer.stop()
        if self.document.path is not None and not self._restoring:
            try:
                save_state(self.document.path, self.editor_state())
            except OSError as exc:  # e.g. a read-only folder: views are simply not remembered
                self.statusBar().showMessage(f"Could not save the editor state: {exc}", 5000)

    def restore_editor_state(self) -> None:
        """Bring back how the project was being looked at the last time it was open."""
        if self.document.path is None:
            return
        state = load_state(self.document.path)
        if not state:
            return
        try:
            self._apply_state(state)
        except (KeyError, TypeError, ValueError, IndexError):
            pass  # an unexpected state file only costs the views, never the project

    def _apply_state(self, state: dict) -> None:
        exists = self.document.exists
        layers = self.document.project.layers
        self.layers.visible = {n: False for n in state.get("hidden_layers", []) if n in layers}
        self.document.restore_trials(state.get("trials", {}))
        self.rulers = {
            c: [tuple(float(v) for v in r) for r in rs]
            for c, rs in state.get("rulers", {}).items()
            if exists(c)
        }
        collapsed = state.get("collapsed", {})
        self.components.collapsed = set(collapsed.get("components", []))
        self.tree.collapsed = {
            c: {_path(p) for p in paths} for c, paths in collapsed.get("shapes", {}).items()
        }
        panes = [
            {**pane, "tabs": [t for t in pane["tabs"] if exists(t["component"])]}
            for pane in state.get("panes", [])[:2]
        ]
        panes = [pane for pane in panes if pane["tabs"]]
        if panes:
            self.area.close_all()
            for index, pane in enumerate(panes):
                if index:
                    self.area.split_view()
                    target = self.area.panes[1]
                    while target.count():  # split_view copied the current tab
                        target.removeTab(0)
                target = self.area.panes[index]
                for tab in pane["tabs"]:
                    view = self.area.open(tab["component"], target)
                    view.view_mode = tab.get("mode", "drawn")
                    if view.view_mode not in VIEW_MODES:
                        view.view_mode = "drawn"
                    view.selection = [_path(p) for p in tab.get("selection", [])]
                    view._fitted = True
                    self._render(view)
                    view.canvas.set_view_state(tab["zoom"], *tab["center"])
                current = pane.get("current", 0)
                if 0 <= current < target.count():
                    target.setCurrentIndex(current)
            chosen = self.area.panes[min(state.get("current_pane", 0), len(self.area.panes) - 1)]
            if chosen.currentWidget() is not None:
                self.area.set_current(chosen.currentWidget())
        self.refresh()

    # -- tools -------------------------------------------------------------

    def set_tool(self, name: str) -> None:
        """Make a canvas tool active (see :mod:`mems_sketch.gui.tools`)."""
        tool = self.tools[name]
        if tool is not self.tool:
            self.tool.deactivate()
            self.tool = tool
        self.tool_actions[name].setChecked(True)
        for view in self.area.views():
            view.canvas.set_tool_cursor(tool.cursor)
        tool.activate()
        QSettings("mems-sketch", "mems-sketch").setValue("tool", name)
        self.update_overlay()

    def set_left_pans(self, pans: bool) -> None:
        for view in self.area.views():
            view.canvas.left_pans = pans
            view.canvas.set_tool_cursor(self.tool.cursor)

    def prompt(self, text: str) -> None:
        self.statusBar().showMessage(text)

    def run(self, action) -> tuple[bool, object]:
        return self._run(action)

    def _pressed(self, view: ComponentView, x: float, y: float, modifiers) -> None:
        if view is not self.area.current:
            self.area.set_current(view)  # clicking in the other pane switches to it
        self.tool.press(x, y, modifiers)

    def _moved(self, view: ComponentView, x: float, y: float, modifiers, left: bool) -> None:
        if view is self.area.current:
            self.tool.move(x, y, modifiers, left)

    def _released(self, view: ComponentView, x: float, y: float, modifiers) -> None:
        if view is self.area.current:
            self.tool.release(x, y, modifiers)

    def _canvas_clicked(self, x: float, y: float, additive: bool) -> None:
        """A click at ``(x, y)`` with the active tool (Ctrl when ``additive``)."""
        modifiers = Qt.KeyboardModifier.ControlModifier if additive else Qt.KeyboardModifier(0)
        self.tool.press(x, y, modifiers)
        self.tool.release(x, y, modifiers)

    def escape(self) -> None:
        """Esc: stop what the tool is doing; if it was idle, go back to Select."""
        if not self.tool.cancel() and self.tool.name != "select":
            self.set_tool("select")
        else:
            self.prompt(self.tool.hint())
        self.update_overlay()

    cancel_align = escape  # the earlier name

    def start_align(self) -> None:
        """The Align tool; with one shape selected, it starts with that shape."""
        if len(self.selection) != 1:
            self.report_error("select the one shape to align first")
            return
        self.set_tool("align")

    @property
    def align_step(self) -> str | None:
        tool = self.tools["align"]
        return tool.step if self.tool is tool else None

    @property
    def _candidates(self) -> list:
        tool = self.tools["align"]
        return tool.candidates if isinstance(self.tool, AlignTool) else []

    # -- rulers ------------------------------------------------------------

    def add_ruler(self, ruler: tuple[float, float, float, float]) -> None:
        self.rulers.setdefault(self.document.active, []).append(ruler)
        self.draw_rulers()
        self.state_changed()

    def clear_rulers(self) -> None:
        self.rulers.pop(self.document.active, None)
        self.draw_rulers()
        self.state_changed()

    def draw_rulers(self, extra: tuple[float, float, float, float] | None = None) -> None:
        """Show the rulers in every tab of the current component (plus one being drawn)."""
        for view in self.area.views():
            rulers = list(self.rulers.get(view.component, []))
            if extra is not None and view is self.area.current:
                rulers.append(extra)
            view.canvas.show_rulers(rulers)

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
        self._action("Move by…", self.move_by, "Ctrl+Shift+M", edit)
        self._action("Rotate 90° left", lambda: self.rotate_selection(90), "Ctrl+R", edit)
        self._action("Rotate 90° right", lambda: self.rotate_selection(-90), "Ctrl+Shift+R", edit)
        self._action("Mirror left-right", lambda: self.mirror_selection(True), None, edit)
        self._action("Mirror up-down", lambda: self.mirror_selection(False), None, edit)
        edit.addSeparator()
        self._action("Align…", self.start_align, "Ctrl+L", edit)
        self._action("Remove alignment", self.remove_alignment, None, edit)
        self._action("Cancel", self.escape, "Esc", edit)

        tools_menu = bar.addMenu("&Tools")
        self.tool_actions: dict[str, QAction] = {}
        group = QActionGroup(self)
        group.setExclusive(True)
        for name, tool in self.tools.items():
            action = self._action(
                tool.label, lambda _=False, n=name: self.set_tool(n), tool.shortcut, tools_menu
            )
            action.setCheckable(True)
            action.setToolTip(f"{tool.label} ({tool.shortcut})")
            group.addAction(action)
            self.tool_actions[name] = action
        tools_menu.addSeparator()
        self._action("Clear rulers", self.clear_rulers, None, tools_menu)

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
        self.dark_action = self._action("Dark canvas", self._toggle_dark, None, view)
        self.dark_action.setCheckable(True)
        self.dark_action.setChecked(self.canvas_theme == "dark")
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

        palette = self.addToolBar("Tools")
        palette.setObjectName("tools-toolbar")
        self.addToolBar(Qt.ToolBarArea.LeftToolBarArea, palette)  # a vertical tool palette
        for action in self.tool_actions.values():
            palette.addAction(action)
        palette.addSeparator()
        palette.addAction("⟲ 90°", lambda: self.rotate_selection(90))
        palette.addAction("⟳ 90°", lambda: self.rotate_selection(-90))
        palette.addAction("⇆", lambda: self.mirror_selection(True)).setToolTip("Mirror left-right")
        palette.addAction("⇅", lambda: self.mirror_selection(False)).setToolTip("Mirror up-down")

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
        self.tool.cancel()  # what it was doing was based on the previous state
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
        self.draw_rulers()
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

    def update_overlay(self) -> None:
        view = self.view
        markers = [v.bbox_um for v in view.violations if v.bbox_um]
        self.canvas.show_overlay(self.document.highlight(view.selection), markers)
        try:
            declared = [(n, x, y) for n, (x, y) in self.document.declared_points().items()]
        except Exception:  # noqa: BLE001 - the messages panel shows why
            declared = []
        self.canvas.show_points("declared", declared, labels=True)
        tool_markers = self.tool.markers()
        selected = self.document.node_points(view.selection[0]) if len(view.selection) == 1 else []
        self.canvas.show_points("selected", [] if "pick" in tool_markers else selected)
        for style in ("pick", "anchor"):
            self.canvas.show_points(style, tool_markers.get(style, []))

    _update_overlay = update_overlay

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
        self.update_overlay()
        self.state_changed()

    def _cursor_moved(self, x: float, y: float) -> None:
        text = f"x {x:.3f} µm   y {y:.3f} µm"
        label = self.tool.hover_label(x, y)
        if label is not None:
            text = f"{label}   {text}"
        self.coordinates.setText(text)

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
        at = probe(x, y)
        return next(
            (p for p, region in reversed(view.node_regions) if not (region & at).is_empty()),
            None,
        )

    def hit(self, x: float, y: float) -> NodePath | None:
        """The top-level shape under ``(x, y)`` in the current tab."""
        return self._hit(self.view, x, y)

    def on_selection(self, x: float, y: float) -> bool:
        """Whether ``(x, y)`` lies on one of the selected shapes."""
        at = probe(x, y)
        for path in self.selection:
            geometry = self.document.highlight([path])
            if geometry and any(not (r & at).is_empty() for r in geometry.layers.values()):
                return True
        return False

    def select_box(self, x0: float, y0: float, x1: float, y1: float, additive: bool) -> None:
        """Select the top-level shapes lying entirely inside a box."""
        box = kdb.Box(
            *(round(v * 1000) for v in (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
        )
        inside = [
            p
            for p, region in self.view.node_regions
            if not region.is_empty()
            and box.contains(region.bbox().p1)
            and box.contains(region.bbox().p2)
        ]
        paths = list(dict.fromkeys([*self.selection, *inside])) if additive else inside
        self.tree.select_paths(paths)

    def select_click(self, hit: NodePath | None, additive: bool) -> None:
        """A click on ``hit`` (or on nothing): select it, or toggle it when ``additive``."""
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

    # -- moving, rotating, mirroring ---------------------------------------

    def nudge(self, steps_x: int, steps_y: int, fine: bool = False) -> None:
        """Move the selection by grid steps (a tenth of one when ``fine``)."""
        if not self.selection or self.tool.busy:
            return
        step = self.canvas.grid_step() / (10 if fine else 1)
        paths = list(self.selection)
        self._run(lambda: self.document.move(paths, steps_x * step, steps_y * step))

    def move_by(self) -> None:
        """Move the selection by an exact amount, typed as ``dx, dy``."""
        if not self.selection:
            self.report_error("select the shapes to move first")
            return
        text, ok = QInputDialog.getText(self, "Move by", "dx, dy in µm (e.g. 10, -2.5):")
        if not ok or not text.strip():
            return
        paths = list(self.selection)

        def move() -> None:
            parts = [float(v) for v in text.replace(";", ",").split(",")]
            if len(parts) != 2:
                raise ValueError("type two numbers: dx, dy")
            self.document.move(paths, *parts)

        self._run(move)

    def rotate_selection(self, angle: float) -> None:
        """Rotate the selection about the centre of its bounding box."""
        center = self.document.selection_center(self.selection) if self.selection else None
        if center is None:
            self.report_error("select the shapes to rotate first")
            return
        paths = list(self.selection)
        self._run(lambda: self.document.rotate(paths, angle, center))

    def mirror_selection(self, left_right: bool) -> None:
        center = self.document.selection_center(self.selection) if self.selection else None
        if center is None:
            self.report_error("select the shapes to mirror first")
            return
        paths = list(self.selection)
        self._run(lambda: self.document.mirror(paths, left_right, center))

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
            self.save_editor_state()
            self.area.close_all()
            self.rulers = {}
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
        self.save_editor_state()
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
            self.rulers = {}
            self.layers.visible = {}
            self.components.collapsed = set()
            self.tree.collapsed = {}
            self.refresh()
            self.restore_editor_state()
        finally:
            self._restoring = False

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

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._confirm_discard():
            self.save_editor_state()
            event.accept()
        else:
            event.ignore()


def _path(steps) -> NodePath:
    return tuple((int(slot), int(index)) for slot, index in steps)


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
