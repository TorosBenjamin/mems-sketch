"""Main window and application entry point (``mems-sketch`` or ``python -m mems_sketch.gui``).

The window is a frontend only: it shows and edits the project through
:class:`ProjectDocument`, which is the single way into the backend.
"""

from __future__ import annotations

import sys
from pathlib import Path

import klayout.db as kdb
from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QTabWidget,
    QToolBar,
    QToolButton,
    QWidget,
)

from mems_sketch.core.shapes import NodePath
from mems_sketch.export.base import available_exporters
from mems_sketch.gui import icons, theme
from mems_sketch.gui.canvas import LayoutCanvas
from mems_sketch.gui.document import VIEW_MODES, ProjectDocument
from mems_sketch.gui.editor_state import load_state, save_state
from mems_sketch.gui.find_action import FindActionDialog, menu_actions
from mems_sketch.gui.panels import (
    ComponentsPanel,
    ConstantsPanel,
    LayersPanel,
    MessagesPanel,
    ParametersPanel,
    PointsPanel,
    ShapeTree,
    swatch_icon,
)
from mems_sketch.gui.properties import PropertyEditor
from mems_sketch.gui.settings import PreferencesDialog, Settings
from mems_sketch.gui.tools import TOOLS, AlignTool, Tool, probe
from mems_sketch.gui.views import ComponentView, EditorArea

STATE_SAVE_DELAY_MS = 1000  # the editor state is written this long after the last change
DEFAULT_PATH_WIDTH = 2.0  # µm, for the Path tool until another width is chosen
TOOLS_DRAWING_FIRST = next(t.name for t in TOOLS if t.draws)  # the palette separates them
OPEN_FILTER = "MEMS projects (project.yaml);;Legacy designs (*.mems)"
PRIMITIVES = [
    ("rect", "Rectangle"),
    ("circle", "Circle"),
    ("arc", "Arc / ring"),
    ("polygon", "Polygon"),
    ("path", "Path"),
]
# Canvas options and the settings they come from (see gui/settings.py).
CANVAS_OPTIONS = {
    "fill_opacity": "canvas/fill_opacity",
    "outline_width": "canvas/outline_width",
    "show_grid": "canvas/show_grid",
    "grid_spacing_px": "canvas/grid_spacing_px",
    "show_axes": "canvas/show_axes",
    "show_axis_gizmo": "canvas/show_axis_gizmo",
    "show_scale_bar": "canvas/show_scale_bar",
    "gizmo_size_px": "canvas/gizmo_size_px",
    "zoom_step": "canvas/zoom_step",
}
OVERLAYS = [  # View › Overlays: setting, label, icon
    ("canvas/show_grid", "Grid", "grid"),
    ("canvas/show_axes", "Coloured axes", "axes"),
    ("canvas/show_axis_gizmo", "Axis indicator", "axes"),
    ("canvas/show_scale_bar", "Scale bar", "ruler"),
    ("canvas/show_gizmos", "Move and rotate gizmos", "move"),
    ("canvas/hover_highlight", "Highlight under cursor", "select"),
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
        self.settings = Settings(self)
        self.ui_theme = self._apply_ui_theme()
        self.draw_layer: str | None = None  # the layer the drawing tools draw on
        self.path_width = self.settings.get("editor/path_width")
        self._hovered: NodePath | None = None
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
        self.layers.layers.currentCellChanged.connect(self._layer_row_chosen)
        self.constants = ConstantsPanel(self.document)
        self.points = PointsPanel(self.document)
        self.messages = MessagesPanel()
        self._build_docks()

        self._build_actions()
        self._build_status_bar()

        self.document.changed.connect(self.refresh)
        self.document.active_changed.connect(self._active_changed)
        self.document.file_changed.connect(self._update_title)
        self.document.component_renamed.connect(self.area.rename)
        self.area.current_changed.connect(self._view_activated)
        self.area.tabs_changed.connect(self.state_changed)
        self.area.tab_menu_requested.connect(self._tab_menu)
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
        self.messages.counts_changed.connect(self._show_problem_count)
        self.settings.changed.connect(self._setting_changed)
        for panel in (
            self.properties,
            self.parameters,
            self.layers,
            self.constants,
            self.points,
            self.components,
        ):
            panel.error.connect(self.report_error)

        self.setWindowIcon(icons.icon("component"))
        self.resize(1500, 950)
        self.refresh()
        self._update_title()
        saved_tool = self.settings.value("tool", "select")
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
            canvas.key_pressed.connect(lambda key: self.tool.key(key))
            canvas.view_changed.connect(self.state_changed)
            canvas.view_changed.connect(self._show_zoom)
            canvas.set_theme(self.canvas_theme)
            canvas.configure(**self._canvas_options())
            self._caption(view)
            canvas.left_pans = self.tool.name == "hand"
            canvas.set_tool_cursor(self.tool.cursor)
            canvas.show_rulers(self.rulers.get(view.component, []))
        return view

    def _view_activated(self, view: ComponentView) -> None:
        """The user switched tabs (or panes): the panels follow the new current tab."""
        for tool in self.tools.values():
            tool.reset()  # what a tool was doing belongs to the previous tab
            tool._gizmo_drag = None
        for other in self.area.views():  # ... and so do its previews
            other.canvas.clear_drag_preview()
            other.canvas.show_sketch([], False)
            other.canvas.show_box(None)
            other.canvas.show_points("snap", [])
            other.canvas.show_hover(None)
        self._hovered = None
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

    # -- appearance and settings ------------------------------------------

    @property
    def canvas_theme(self) -> str:
        chosen = self.settings.get("appearance/canvas_theme")
        return self.ui_theme if chosen == "auto" else chosen

    def set_canvas_theme(self, theme: str) -> None:
        """Light or dark canvas background for every tab; remembered for next time."""
        self.settings.set("appearance/canvas_theme", theme)

    def _toggle_dark(self, checked: bool) -> None:
        self.set_canvas_theme("dark" if checked else "light")

    def _apply_ui_theme(self) -> str:
        app = QApplication.instance()
        return theme.apply(app, self.settings.get("appearance/ui_theme"))

    def _canvas_options(self) -> dict:
        return {option: self.settings.get(key) for option, key in CANVAS_OPTIONS.items()}

    def _setting_changed(self, key: str) -> None:
        """Apply a changed setting at once."""
        if key == "appearance/ui_theme":
            self.ui_theme = self._apply_ui_theme()
            self.refresh()  # panels and tabs pick up the new icon colours
        if key in ("appearance/ui_theme", "appearance/canvas_theme"):
            for view in self.area.views():
                view.canvas.set_theme(self.canvas_theme)
            self.dark_action.setChecked(self.canvas_theme == "dark")
            self.update_overlay()
        if key in CANVAS_OPTIONS.values():
            options = self._canvas_options()
            for view in self.area.views():
                view.canvas.configure(**options)
            self._show_zoom()
        if key == "appearance/palette_labels":
            self._palette_style()
        if key in ("canvas/show_gizmos", "canvas/hover_highlight"):
            self.canvas.show_hover(None)
            self._hovered = None
            self.update_overlay()
        if key == "snapping/angle_step":
            self.angle_box.blockSignals(True)
            self.angle_box.setValue(self.settings.get(key))
            self.angle_box.blockSignals(False)
        for setting_key, action in self.setting_actions.items():
            if setting_key == key:
                action.blockSignals(True)
                action.setChecked(self.settings.get(key))
                action.blockSignals(False)
        self.prompt(self.tool.hint())

    def show_settings(self, page: str | None = None) -> None:
        """The settings dialog (Ctrl+Alt+S); ``page`` opens a page, e.g. ``Keymap``."""
        shortcuts = [
            (action.text().replace("&", ""), action.shortcut().toString())
            for _path, action in menu_actions(self.menuBar())
            if not action.shortcut().isEmpty()
        ]
        dialog = PreferencesDialog(self.settings, shortcuts, self)
        if page is not None:
            names = [dialog.pages.item(i).text() for i in range(dialog.pages.count())]
            if page in names:
                dialog.pages.setCurrentRow(names.index(page))
        self._preferences = dialog
        dialog.show()

    def find_action(self) -> None:
        """Find Action (Ctrl+Shift+A): run a command by typing its name."""
        dialog = FindActionDialog(menu_actions(self.menuBar()), self)
        center = self.geometry().center()
        dialog.move(center.x() - dialog.width() // 2, self.geometry().top() + 90)
        self._find_dialog = dialog
        dialog.show()
        dialog.search.setFocus()

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
            "drawing": {"layer": self.draw_layer, "path_width": self.path_width},
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
        if self.document.path is None or not self.settings.get("editor/restore_state"):
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
        drawing = state.get("drawing", {})
        if drawing.get("layer") in layers:
            self.draw_layer = drawing["layer"]
        width = drawing.get("path_width")
        if isinstance(width, (int, float)) and width > 0:
            self.path_width = float(width)
            self.width_box.blockSignals(True)
            self.width_box.setValue(self.path_width)
            self.width_box.blockSignals(False)
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
            view.canvas.show_hover(None)
        self._hovered = None
        tool.activate()
        self.settings.set_value("tool", name)
        self._show_tool_options()
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

    def _dock(self, title: str, widget, area, header: bool = True) -> QDockWidget:
        """A tool window. Its header has the title and the panel's own buttons
        (``header_buttons``); ``header=False`` leaves the header to the tabs of
        docks tabbed together."""
        dock = QDockWidget(title, self)
        dock.setObjectName(title)
        dock.setWidget(widget)
        bar = _DockHeader(theme.HEADER_HEIGHT if header else 0)
        if header:
            bar.setObjectName("dock-title")
            bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            row = QHBoxLayout(bar)
            row.setContentsMargins(10, 0, 6, 0)
            row.setSpacing(1)
            label = QLabel(title)
            label.setObjectName("dock-title-label")
            row.addWidget(label, 1)
            for button in getattr(widget, "header_buttons", []):
                row.addWidget(button)
        dock.setTitleBarWidget(bar)
        self.addDockWidget(area, dock)
        return dock

    def _build_docks(self) -> None:
        left, right = Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea
        # The side panels run the full height; Messages sits under the editor only.
        self.setCorner(Qt.Corner.BottomLeftCorner, left)
        self.setCorner(Qt.Corner.BottomRightCorner, right)
        self.setTabPosition(Qt.DockWidgetArea.AllDockWidgetAreas, QTabWidget.TabPosition.North)
        self.setDockOptions(
            QMainWindow.DockOption.AnimatedDocks | QMainWindow.DockOption.AllowTabbedDocks
        )
        components = self._dock("Components", self.components, left)
        shapes = self._dock("Shapes", self.tree, left)
        layers = self._dock("Layers", self.layers, left)
        properties = self._dock("Properties", self.properties, right)
        parameters = self._dock("Parameters", self.parameters, right, header=False)
        points = self._dock("Points", self.points, right, header=False)
        constants = self._dock("Process constants", self.constants, right, header=False)
        self.tabifyDockWidget(parameters, points)
        self.tabifyDockWidget(parameters, constants)
        parameters.raise_()
        messages = self._dock("Messages", self.messages, Qt.DockWidgetArea.BottomDockWidgetArea)
        vertical, horizontal = Qt.Orientation.Vertical, Qt.Orientation.Horizontal
        self.resizeDocks([components, shapes, layers], [240, 330, 200], vertical)
        self.resizeDocks([properties, parameters], [560, 220], vertical)
        self.resizeDocks([shapes, properties], [320, 360], horizontal)
        self.resizeDocks([messages], [120], vertical)

    def _action(
        self, text: str, slot, shortcut=None, menu: QMenu | None = None, icon: str | None = None
    ) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(slot)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        if icon is not None:
            icons.bind(action, icon)
        if menu is not None:
            menu.addAction(action)
        keys = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
        action.setToolTip(f"{text.replace('…', '')} ({keys})" if keys else text.replace("…", ""))
        return action

    def _build_actions(self) -> None:
        bar = self.menuBar()
        file = bar.addMenu("&File")
        self._action("New project", self.new_project, QKeySequence.StandardKey.New, file, "new")
        self._action(
            "Open project…", self.open_project, QKeySequence.StandardKey.Open, file, "open"
        )
        self.save_action = self._action(
            "Save", self.save_project, QKeySequence.StandardKey.Save, file, "save"
        )
        self._action("Save as…", self.save_project_as, QKeySequence.StandardKey.SaveAs, file)
        file.addSeparator()
        export = file.addMenu("Export")
        icons.bind(export.menuAction(), "export")
        for mode, label in VIEW_MODES.items():
            self._action(
                f"{label} geometry…", lambda _=False, m=mode: self.export_file(m), menu=export
            )
        file.addSeparator()
        self._action("Settings…", self.show_settings, "Ctrl+Alt+S", file, "settings")
        file.addSeparator()
        self._action("Quit", self.close, QKeySequence.StandardKey.Quit, file)

        edit = bar.addMenu("&Edit")
        self.undo_action = self._action(
            "Undo", self.document.undo, QKeySequence.StandardKey.Undo, edit, "undo"
        )
        self.redo_action = self._action(
            "Redo", self.document.redo, QKeySequence.StandardKey.Redo, edit, "redo"
        )
        edit.addSeparator()
        self._action("Duplicate", self.duplicate, "Ctrl+D", edit, "duplicate")
        self._action("Delete", self.delete, QKeySequence.StandardKey.Delete, edit, "delete")
        self._action("Unwrap operation", self.unwrap, "Ctrl+Shift+U", edit)
        self.make_action = self._action(
            "Make component from selection…", self.make_component, "Ctrl+K", edit, "make_component"
        )
        self.unpack_action = self._action(
            "Unpack component", self.unpack, "Ctrl+Shift+K", edit, "unpack"
        )
        edit.addSeparator()
        self._action("Move by…", self.move_by, "Ctrl+Shift+M", edit, "move")
        self.rotate_left_action = self._action(
            "Rotate 90° left", lambda: self.rotate_selection(90), "Ctrl+R", edit, "rotate_left"
        )
        self.rotate_right_action = self._action(
            "Rotate 90° right",
            lambda: self.rotate_selection(-90),
            "Ctrl+Shift+R",
            edit,
            "rotate_right",
        )
        self.mirror_h_action = self._action(
            "Mirror left-right", lambda: self.mirror_selection(True), None, edit, "mirror_h"
        )
        self.mirror_v_action = self._action(
            "Mirror up-down", lambda: self.mirror_selection(False), None, edit, "mirror_v"
        )
        edit.addSeparator()
        self._action("Align…", self.start_align, "Ctrl+L", edit, "align")
        self._action("Remove alignment", self.remove_alignment, None, edit)
        self._action("Cancel", self.escape, "Esc", edit)

        tools_menu = bar.addMenu("&Tools")
        self.tool_actions: dict[str, QAction] = {}
        group = QActionGroup(self)
        group.setExclusive(True)
        for name, tool in self.tools.items():
            if name == TOOLS_DRAWING_FIRST:
                tools_menu.addSeparator()
            action = self._action(
                tool.label,
                lambda _=False, n=name: self.set_tool(n),
                tool.shortcut,
                tools_menu,
                tool.icon,
            )
            action.setCheckable(True)
            group.addAction(action)
            self.tool_actions[name] = action
        tools_menu.addSeparator()
        self._action("Clear rulers", self.clear_rulers, None, tools_menu, "clear")

        insert = bar.addMenu("&Insert")
        self.primitive_menu = insert.addMenu("Primitive")
        icons.bind(self.primitive_menu.menuAction(), "rect")
        for kind, label in PRIMITIVES:
            self._action(
                label,
                lambda _=False, k=kind: self.add_primitive(k),
                menu=self.primitive_menu,
                icon=kind,
            )
        self.component_menu = insert.addMenu("Component")
        icons.bind(self.component_menu.menuAction(), "place")
        self.component_menu.aboutToShow.connect(self._fill_component_menu)

        operations = bar.addMenu("&Operations")
        self.operation_actions: dict[str, QAction] = {}
        for op, label in OPERATIONS:
            self.operation_actions[op] = self._action(
                label, lambda _=False, o=op: self.wrap(o), menu=operations, icon=op
            )

        view = bar.addMenu("&View")
        self._action("Fit", lambda: self.canvas.fit(), "F", view, "fit")
        self._action("Zoom in", lambda: self.canvas.zoom_by(1.25), "Ctrl+=", view, "zoom_in")
        self._action("Zoom out", lambda: self.canvas.zoom_by(0.8), "Ctrl+-", view, "zoom_out")
        self._action("Recompile and check", self.refresh, "F5", view, "recompile")
        view.addSeparator()
        overlays = view.addMenu("Overlays")
        icons.bind(overlays.menuAction(), "eye")
        self.setting_actions: dict[str, QAction] = {}
        for key, label, icon_name in OVERLAYS:
            action = self._action(
                label, lambda checked, k=key: self.settings.set(k, checked), menu=overlays
            )
            icons.bind(action, icon_name)
            action.setCheckable(True)
            action.setChecked(self.settings.get(key))
            self.setting_actions[key] = action
        self.dark_action = self._action("Dark canvas", self._toggle_dark, None, view)
        self.dark_action.setCheckable(True)
        self.dark_action.setChecked(self.canvas_theme == "dark")
        view.addSeparator()
        self._action("Open top component", self._edit_top, "Ctrl+T", view, "top")
        self._action("Split view", self.split_view, "Ctrl+\\", view, "split")
        self._action("Merge split view", self.area.unsplit, None, view)
        self._action("Close tab", self.close_tab, QKeySequence.StandardKey.Close, view, "close")
        panels = view.addMenu("Panels")
        for dock in self.findChildren(QDockWidget):
            panels.addAction(dock.toggleViewAction())

        help_menu = bar.addMenu("&Help")
        self.find_action_action = self._action(
            "Find action…", self.find_action, "Ctrl+Shift+A", help_menu, "search"
        )
        self._action("Keyboard shortcuts", lambda: self.show_settings("Keymap"), None, help_menu)

        self._build_main_toolbar()
        self._build_palette()

    def _toolbar(self, title: str, name: str) -> QToolBar:
        toolbar = self.addToolBar(title)
        toolbar.setObjectName(name)
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(18, 18))
        return toolbar

    def _menu_button(self, toolbar: QToolBar, menu: QMenu, icon: str, tip: str) -> QToolButton:
        button = QToolButton()
        icons.bind(button, icon)
        button.setMenu(menu)
        button.setToolTip(tip)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        toolbar.addWidget(button)
        return button

    def _build_main_toolbar(self) -> None:
        tools = self._toolbar("Main", "main-toolbar")
        for action in self.findChildren(QAction):
            if action.text() in ("New project", "Open project…"):
                tools.addAction(action)
        tools.addAction(self.save_action)
        tools.addSeparator()
        tools.addAction(self.undo_action)
        tools.addAction(self.redo_action)
        tools.addSeparator()
        self._menu_button(tools, self.primitive_menu, "rect", "Insert a primitive")
        self._menu_button(tools, self.component_menu, "place", "Place a component")
        tools.addSeparator()
        for op in ("union", "subtract", "intersect", "xor", "offset", "fillet", "transform"):
            tools.addAction(self.operation_actions[op])
        tools.addSeparator()
        tools.addAction(self.make_action)
        tools.addAction(self.unpack_action)
        tools.addSeparator()
        self._build_tool_options(tools)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tools.addWidget(spacer)
        self._build_snapping(tools)
        tools.addSeparator()
        self.mode_box = QComboBox()
        self.mode_box.setToolTip("What the canvas shows: the drawn layout or a process view")
        for mode, label in VIEW_MODES.items():
            self.mode_box.addItem(label, mode)
        self.mode_box.currentIndexChanged.connect(self._mode_changed)
        self.mode_box.setMaximumWidth(160)
        tools.addWidget(self.mode_box)
        split = next(a for a in self.findChildren(QAction) if a.text() == "Split view")
        tools.addAction(split)
        tools.addSeparator()
        tools.addAction(self.find_action_action)
        settings = next(a for a in self.findChildren(QAction) if a.text() == "Settings…")
        tools.addAction(settings)

    def _build_palette(self) -> None:
        palette = QToolBar("Tools")
        palette.setObjectName("tools-toolbar")
        palette.setMovable(False)
        palette.setIconSize(QSize(20, 20))
        self.addToolBar(Qt.ToolBarArea.LeftToolBarArea, palette)  # a vertical tool palette
        for name, action in self.tool_actions.items():
            if name == TOOLS_DRAWING_FIRST:
                palette.addSeparator()
            palette.addAction(action)
        palette.addSeparator()
        for action in (
            self.rotate_left_action,
            self.rotate_right_action,
            self.mirror_h_action,
            self.mirror_v_action,
        ):
            palette.addAction(action)
        self.palette = palette
        self._palette_style()

    def _palette_style(self) -> None:
        labels = self.settings.get("appearance/palette_labels")
        self.palette.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextUnderIcon
            if labels
            else Qt.ToolButtonStyle.ToolButtonIconOnly
        )

    def _build_tool_options(self, bar: QToolBar) -> None:
        """The active tool and its own settings (as in Blender's tool header):
        the drawing layer and path width, or the rotation step."""
        self.tool_icon = QLabel()
        self.tool_name = QLabel()
        self.tool_name.setObjectName("heading")
        bar.addWidget(self.tool_icon)
        bar.addWidget(self.tool_name)
        self.layer_box = QComboBox()
        self.layer_box.setToolTip("The layer the drawing tools draw on")
        self.layer_box.setMinimumWidth(110)
        self.layer_box.currentIndexChanged.connect(self._draw_layer_chosen)
        self.width_box = QDoubleSpinBox()
        self.width_box.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.width_box.setRange(0.001, 1e6)
        self.width_box.setDecimals(3)
        self.width_box.setSuffix(" µm")
        self.width_box.setValue(self.path_width)
        self.width_box.setToolTip("The width of paths drawn with the Path tool")
        self.width_box.valueChanged.connect(self._path_width_chosen)
        self.angle_box = QDoubleSpinBox()
        self.angle_box.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.angle_box.setRange(1, 90)
        self.angle_box.setDecimals(1)
        self.angle_box.setSuffix(" °")
        self.angle_box.setValue(self.settings.get("snapping/angle_step"))
        self.angle_box.setToolTip("The Rotate tool snaps to multiples of this angle")
        self.angle_box.valueChanged.connect(lambda v: self.settings.set("snapping/angle_step", v))
        self.tool_widgets = {}
        for key, label, widget in (
            ("layer", "Layer", self.layer_box),
            ("width", "Width", self.width_box),
            ("angle", "Step", self.angle_box),
        ):
            caption = QLabel(f"  {label} ")
            caption.setObjectName("muted")
            self.tool_widgets[key] = (bar.addWidget(caption), bar.addWidget(widget))

    def _build_snapping(self, bar: QToolBar) -> None:
        """Snapping toggles (shape points, grid) and the gizmo toggle."""
        for key, label, icon_name in (
            ("snapping/points", "Snap to shape points", "snap_points"),
            ("snapping/grid", "Snap to the grid", "grid"),
        ):
            action = QAction(label, self)
            icons.bind(action, icon_name)
            action.setCheckable(True)
            action.setChecked(self.settings.get(key))
            action.toggled.connect(lambda checked, k=key: self.settings.set(k, checked))
            bar.addAction(action)
            self.setting_actions[key] = action
        bar.addAction(self.setting_actions["canvas/show_gizmos"])

    def _show_tool_options(self) -> None:
        tool = self.tool
        self.tool_icon.setPixmap(icons.pixmap(tool.icon, 16))
        self.tool_name.setText(f"{tool.label} ")
        shown = {
            "layer": tool.draws,
            "width": tool.name == "path",
            "angle": tool.name == "rotate",
        }
        for key, actions in self.tool_widgets.items():
            for action in actions:
                action.setVisible(shown[key])

    def _build_status_bar(self) -> None:
        status = self.statusBar()
        status.setSizeGripEnabled(False)
        self.problems_button = QToolButton()
        self.problems_button.setToolTip("Errors and rule violations (click to show)")
        self.problems_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.problems_button.clicked.connect(self._show_messages_panel)
        self.grid_label = QLabel()
        self.grid_label.setToolTip("Grid step at this zoom")
        self.zoom_label = QLabel()
        self.zoom_label.setToolTip("Zoom (screen pixels per µm)")
        self.coordinates = QLabel()
        self.coordinates.setMinimumWidth(230)
        for widget in (self.problems_button, self.grid_label, self.zoom_label, self.coordinates):
            status.addPermanentWidget(widget)

    def _show_zoom(self) -> None:
        canvas = self.canvas
        self.grid_label.setText(f"grid {canvas.grid_step():g} µm")
        self.zoom_label.setText(f"{canvas.pixels_per_um():.3g} px/µm")

    def _show_problem_count(self, errors: int, violations: int) -> None:
        if errors:
            icons.bind(self.problems_button, "error")
            text = f"{errors} error{'s' if errors > 1 else ''}"
        elif violations:
            icons.bind(self.problems_button, "warning")
            text = f"{violations} violation{'s' if violations > 1 else ''}"
        else:
            icons.bind(self.problems_button, "ok")
            text = "No problems"
        self.problems_button.setText(text)

    def _show_messages_panel(self) -> None:
        dock = next(d for d in self.findChildren(QDockWidget) if d.windowTitle() == "Messages")
        dock.show()
        dock.raise_()

    def _tab_menu(self, view: ComponentView, position: QPoint) -> None:
        menu = QMenu(self)
        self._action("Close", lambda: self.area.close_view(view), None, menu, "close")
        self._action("Close others", lambda: self.area.close_others(view), None, menu)
        self._action("Close all", self._close_all_tabs, None, menu)
        menu.addSeparator()
        self._action(
            "Open in the other pane" if self.area.split else "Split right",
            lambda: (self.area.set_current(view), self.split_view()),
            None,
            menu,
            "split",
        )
        if self.area.split:
            self._action("Merge panes", self.area.unsplit, None, menu)
        menu.exec(position)

    def _close_all_tabs(self) -> None:
        for view in self.area.views():
            self.area.close_view(view)  # the top component opens again when none is left

    # -- drawing -----------------------------------------------------------

    def _refresh_layer_box(self) -> None:
        layers = list(self.document.project.layers)
        if self.draw_layer not in layers:
            self.draw_layer = layers[0] if layers else None
        self.layer_box.blockSignals(True)
        self.layer_box.clear()
        for name in layers:
            self.layer_box.addItem(name)
            color = self.layers.colors.get(name)
            if color is not None:
                self.layer_box.setItemIcon(self.layer_box.count() - 1, swatch_icon(color))
        self.layer_box.setCurrentIndex(layers.index(self.draw_layer) if self.draw_layer else -1)
        self.layer_box.blockSignals(False)

    def set_draw_layer(self, name: str) -> None:
        """The layer the drawing tools draw on."""
        if name in self.document.project.layers and name != self.draw_layer:
            self.draw_layer = name
            self._refresh_layer_box()
            self.state_changed()

    def _draw_layer_chosen(self, index: int) -> None:
        if index >= 0:
            self.set_draw_layer(self.layer_box.itemText(index))

    def _layer_row_chosen(self, row: int, *_) -> None:
        """Clicking a layer in the Layers panel makes it the drawing layer."""
        names = list(self.document.project.layers)
        if 0 <= row < len(names):
            self.set_draw_layer(names[row])

    def _path_width_chosen(self, value: float) -> None:
        self.path_width = round(value, 6)
        self.prompt(self.tool.hint())
        self.state_changed()

    def add_drawn(self, shape) -> None:
        """Add a shape drawn with a drawing tool and select it."""
        self._select_result(lambda: self.document.add_shape(shape))

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
            self._caption(view)
        self.area.update_titles()
        self.draw_rulers()
        self._problems = self.document.problems()
        self._refresh_panels()

    def _refresh_panels(self) -> None:
        """Show the current tab in the panels, toolbar and title."""
        view = self.view
        self.parameters.refresh()
        self.points.refresh()
        self._refresh_layer_box()
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

    def _caption(self, view: ComponentView) -> None:
        """The canvas caption: component, view mode and whether it can be edited."""
        details = [VIEW_MODES.get(view.view_mode, view.view_mode)]
        if view.read_only:
            details.append("read-only")
        if view.component == self.document.project.top:
            details.append("top component")
        view.canvas.set_caption(view.component, " · ".join(details))

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
        self._place_gizmo()
        self._show_zoom()

    def _place_gizmo(self) -> None:
        """The active tool's gizmo on the selection's centre (only in the current tab)."""
        view = self.view
        kind = self.tool.gizmo
        shown = (
            kind is not None
            and view.selection
            and self.settings.get("canvas/show_gizmos")
            and not self.document.read_only
            and not self.tool.busy
        )
        center = self.document.selection_center(view.selection) if shown else None
        for other in self.area.views():
            other.canvas.set_gizmo(kind if other is view and center else None, center)

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
        text = f"x {x:.3f}  y {y:.3f} µm"
        label = self.tool.hover_label(x, y)
        if label is not None:
            text = f"{label}   {text}"
        self.coordinates.setText(text)
        self._hover(x, y)

    def _hover(self, x: float, y: float) -> None:
        """Outline the shape under the cursor (what a click would select)."""
        tool = self.tool
        hovered = None
        if (
            tool.hovers
            and not tool.busy
            and self.settings.get("canvas/hover_highlight")
            and self.canvas.gizmo_hit(x, y) is None
        ):
            hovered = self.hit(x, y)
            if hovered in self.selection:
                hovered = None
        if hovered != self._hovered:
            self._hovered = hovered
            region = dict(self.view.node_regions).get(hovered) if hovered else None
            self.canvas.show_hover(region)

    def _view_double_clicked(self, view: ComponentView, x: float, y: float) -> None:
        """Double-clicking a placed component opens it in a tab (unless a tool uses it)."""
        if view is self.area.current and self.tool.double_click(x, y):
            return
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
        self._caption(view)
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
        return str(self.settings.value("last_dir", str(Path.home())))

    def _remember_dir(self, path: str) -> None:
        self.settings.set_value("last_dir", str(Path(path).parent))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._confirm_discard():
            self.save_editor_state()
            event.accept()
        else:
            event.ignore()


class _DockHeader(QWidget):
    """A tool window header of a fixed height (the dock lays out by the size hint)."""

    def __init__(self, height: int) -> None:
        super().__init__()
        self._height = height
        self.setFixedHeight(height)

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), self._height)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self._height)


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
