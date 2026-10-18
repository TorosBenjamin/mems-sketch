"""Main window and application entry point (``mems-sketch`` or ``python -m mems_sketch.gui``).

The window is a frontend only: it shows and edits the project through
an :class:`~mems_sketch.editing.EditSession`, the backend's way to edit a project.
"""

from __future__ import annotations

import contextlib
import json
import math
import multiprocessing
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QColor, QCursor, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QToolButton,
)

from mems_sketch.core.shapes import CornersModifier, NodePath, paths
from mems_sketch.editing import EditSession
from mems_sketch.editing.clipboard import is_shapes
from mems_sketch.export.base import available_exporters, options_of, title_of
from mems_sketch.gui import component_picker, icons, motion, theme
from mems_sketch.gui.actions import Actions, make_action
from mems_sketch.gui.canvas import LayoutCanvas
from mems_sketch.gui.component_picker import PlacePopup
from mems_sketch.gui.editor_state import load_state, save_state
from mems_sketch.gui.export_dialog import ExportOptionsDialog, remember, remembered
from mems_sketch.gui.history_panel import HistoryPanel
from mems_sketch.gui.new_project import NewProjectDialog
from mems_sketch.gui.panels import (
    INSIDE_ROLE,
    ComponentsPanel,
    LayersPanel,
    MessagesPanel,
    ParametersPanel,
    ShapeTree,
    component_icon,
    detail,
    shape_icon,
    swatch_icon,
)
from mems_sketch.gui.points_panel import PointsPanel
from mems_sketch.gui.process_view import ProcessView
from mems_sketch.gui.properties import PropertyEditor
from mems_sketch.gui.search import (
    ALL,
    DoubleShift,
    Result,
    SearchDialog,
    action_results,
    menu_actions,
)
from mems_sketch.gui.settings import PreferencesDialog, Settings, open_folder
from mems_sketch.gui.statusbar import (
    GRID_SAMPLE,
    POSITION_SAMPLE,
    TARGET_MAX_WIDTH,
    ZOOM_SAMPLE,
    StatusBar,
    ToolStatus,
    fixed_label,
)
from mems_sketch.gui.title_bar import TitleBar
from mems_sketch.gui.toast import Toast, Toasts
from mems_sketch.gui.toolbar import build_toolbar
from mems_sketch.gui.tools import TOOLS, AlignTool, Tool
from mems_sketch.gui.toolwindows import ToolWindows
from mems_sketch.gui.views import ComponentView, EditorArea, builds
from mems_sketch.gui.widgets import MenuTidier

PANEL_HELP = {  # the "?" in each tool window's header
    "components": "Every component, each listed once: the project's and each library's, "
    "the bundled ones (mems) included, in their categories. A component can be *shared* "
    "(at the top of its group) or *private* to another one, listed under it and placed "
    "only inside it.\n\n"
    "Double-click to open one in a tab (library ones read-only); drag it "
    "onto the canvas, or use Place, to put it in the component you are editing. "
    "Right-click for everything else.",
    "shapes": "The shape tree of the component you are editing, evaluated again "
    "whenever a value changes. Operations (subtract, offset, …) hold the shapes they "
    "act on, so nothing is lost: change a shape inside and the result follows.\n\n"
    "Click a shape's eye to hide it; a placed component opens to show what it is made "
    "of (read-only).",
    "layers": "Click a layer's eye to show or hide it; click a layer to draw on it. What a "
    "layer is (its GDS number and rules) is part of the process: View › Process.",
    "messages": "Why the component does not build, and the rule checks: shapes "
    "narrower or closer together than their layer allows. Click a message to see "
    "where it is.",
    "properties": "The selected shape's fields. Numbers can be dragged left and right "
    "(*Shift* finer, *Ctrl* round steps) and the canvas follows as you drag; a click "
    "edits the text. A field can hold an expression over the parameters, such as "
    "*pitch * 2*.",
    "parameters": "The component's parameters: the values whoever places it can set. "
    "*Internal* ones (the lock) are only for inside it.\n\n*Trial* tries a value "
    "without changing the design, on library components too.",
    "history": "What changed, from the project's commits in git. *Uncommitted "
    "changes* are those since the last commit, saved or not; choose a commit to see "
    "what it changed. The canvas shows the material added (tinted) and removed "
    "(hatched) in the component you are editing, with default parameters. Click a "
    "change to go to it.\n\n"
    "*Commit* saves the project and commits its folder, nothing else. Right-click a "
    "commit to compare it with the design now, or to restore the project (or the "
    "component you are editing) as it was: an ordinary edit, which Undo takes back. "
    "Branches, merging and remotes stay with your git tools.",
    "points": "Every point of the component. Its own points are for whoever places "
    "it (to align to); *Default* are the points every component has, from the box "
    "around it; then the points of each named shape.\n\nHover a point to find it "
    "on the canvas, click it to go there and edit it. *Re-export* makes a shape's "
    "point one of this component's.",
}
HOVER_LABEL_DELAY_MS = 600  # pointing at a shape this long says what it is
STATE_SAVE_DELAY_MS = 1000  # the editor state is written this long after the last change
GUIDE_REACH_PX = 6  # a click this close to a guide line selects it
BOX_EPS_UM = 0.0005  # half a database unit: a box edge drawn on a shape's edge contains it
HIT_REACH_PX = 4  # a click this close to a shape still selects it (thin fingers, small parts)
DEFAULT_PATH_WIDTH = 2.0  # µm, for the Path tool until another width is chosen
OPEN_FILTER = (
    "MEMS projects (project.yaml);;One-file projects (*.json *.xml *.mat *.yaml *.yml);;"
    "Legacy designs (*.mems)"
)
TOOL_WINDOWS_KEY = "layout/tool_windows"  # app setting: open tool windows and panel sizes
DEFAULT_TOOL_WINDOWS = ("components", "shapes", "properties", "messages")
CANVAS_MODES = (  # on the canvas
    "select",
    "hand",
    "move",
    "rotate",
    "align",
    "corners",
    "measure",
)
# Canvas options and the settings they come from (see gui/settings.py).
CANVAS_OPTIONS = {
    "fill_opacity": "canvas/fill_opacity",
    "outline_width": "canvas/outline_width",
    "show_grid": "canvas/show_grid",
    "grid_spacing_px": "canvas/grid_spacing_px",
    "show_axes": "canvas/show_axes",
    "show_scale_bar": "canvas/show_scale_bar",
    "gizmo_size_px": "canvas/gizmo_size_px",
    "zoom_step": "canvas/zoom_step",
    "scrolling": "canvas/scrolling",
    "draft_quality": "canvas/draft_quality",
    "max_fps": "canvas/max_fps",
}


# A design that has taken longer than this (s) to build is built in the background
# after a change: the editor goes on, showing the last result until the new one is
# there (requirement QS-8). A quicker one is built at once.
BACKGROUND_BUILD_S = 0.15
# A background build says so (and greys out what shows built geometry) only when it
# takes longer than this (ms): a quick one does not make the window flicker.
UPDATING_SHOWN_MS = 200


CORNER_ERROR_MARK_PX = 8  # half the box marking a corner whose radius does not fit
RESTORED_TOOLS = ("select", "hand", "move")  # the tools a new window starts in again


class MainWindow(QMainWindow):
    _build_done = Signal(object)  # a background build's Future (from the worker thread)

    def __init__(self, document: EditSession | None = None) -> None:
        super().__init__()
        self.document = document or EditSession()
        self._problems: list[str] = []
        self._corner_errors: list[tuple[float, float]] = []  # corners whose radius does not fit
        # The longest each component has taken to build since the project was opened:
        # one that can take long is built in the background, even when a change
        # happens to be quick to build (that one is soon there).
        self.build_seconds: dict[str, float] = {}
        self._build_done.connect(self._take_build)
        self._restoring = False  # while opening a project, the editor state is not saved
        # Per component: distances (x0, y0, x1, y1) and angles (vertex, arm, arm).
        self.rulers: dict[str, list[tuple[float, ...]]] = {}
        self.settings = Settings(self)
        ComponentView.show_implementation = self.settings.get("editor/show_implementation")
        self.ui_theme = self._apply_ui_theme()
        self.draw_layer: str | None = None  # the layer the drawing tools draw on
        self.path_width = self.settings.get("editor/path_width")
        self._hovered: NodePath | None = None
        self._tip_for: NodePath | None = None  # the shape a hover label is (or will be) about
        self._tip_timer = QTimer(self)
        self._tip_timer.setSingleShot(True)
        self._tip_timer.setInterval(HOVER_LABEL_DELAY_MS)
        self._tip_timer.timeout.connect(self._show_tip)
        self.tools: dict[str, Tool] = {cls.name: cls(self) for cls in TOOLS}
        self.tool: Tool = self.tools["select"]
        self.tool.reset()
        self._state_timer = QTimer(self)
        self._state_timer.setSingleShot(True)
        self._state_timer.setInterval(STATE_SAVE_DELAY_MS)
        self._state_timer.timeout.connect(self.save_editor_state)

        self.area = EditorArea(self.document)
        self.toasts = Toasts(self, self.area)  # before anything can report an error
        self._quiet_selection = False
        self.components = ComponentsPanel(self.document)
        self.tree = ShapeTree(self.document)
        self.properties = PropertyEditor(self.document)
        self.parameters = ParametersPanel(self.document)
        self.layers = LayersPanel(self.document)
        self.layers.layers.currentCellChanged.connect(self._layer_row_chosen)
        self.points = PointsPanel(self.document)
        self.points.hovered.connect(self._point_hovered)
        self.points.focused.connect(self._point_focused)
        self._hovered_point = None
        self.messages = MessagesPanel()
        self.history = HistoryPanel(self.document)
        self.history.comparison_changed.connect(self.update_overlay)
        self.history.navigate.connect(self._go_to_change)
        self._build_tool_windows()

        self._build_actions()
        self._build_status_bar()
        motion.set_enabled(not self.settings.get("appearance/reduce_motion"))

        self.document.changed.connect(self.refresh)
        self.document.active_changed.connect(self._active_changed)
        self.document.file_changed.connect(self._update_title)
        self.document.component_renamed.connect(self.area.rename)
        self.area.current_changed.connect(self._view_activated)
        self.area.tabs_changed.connect(self.state_changed)
        self.area.tab_menu_requested.connect(self._tab_menu)
        self.tree.selection_changed_paths.connect(self._tree_selected)
        self.tree.enabled_toggled.connect(
            lambda p, e: self._run(lambda: self.document.nodes.set_enabled(p, e))
        )
        self.tree.itemDoubleClicked.connect(self._tree_double_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(
            lambda at: self.show_menu(
                self.actions_.shapes_menu(), self.tree.viewport().mapToGlobal(at)
            )
        )
        self.components.place_requested.connect(self.add_component)
        self.components.open_requested.connect(self.open_component)
        self.components.process_requested.connect(self.open_process)
        self.area.process_factory = self._make_process_view
        self.components.open_aside_requested.connect(self.open_aside)
        self.layers.visibility_changed.connect(self._set_layer_visible)
        self.tree.collapse_changed.connect(self.state_changed)
        self.components.collapse_changed.connect(self.state_changed)
        self.document.changed.connect(self.state_changed)  # e.g. trial values
        self.messages.zoom_requested.connect(self._zoom_to_bbox)
        self.messages.counts_changed.connect(self._show_problem_count)
        self.messages.waive_requested.connect(self.waive)
        self.messages.unwaive_requested.connect(self.unwaive)
        self.settings.changed.connect(self._setting_changed)
        for panel in (
            self.properties,
            self.parameters,
            self.layers,
            self.points,
            self.components,
            self.history,
        ):
            panel.error.connect(self.report_error)
        self.properties.previewed.connect(self._preview_node)
        self.properties.pick_corners.connect(lambda: self.set_tool("corners"))
        self.properties.pick_alignment.connect(self._pick_alignment)
        self.properties.open_component.connect(self.open_component)
        self._double_shift = DoubleShift(self)
        self._double_shift.pressed.connect(self.search)
        self.winId()  # the native window, which every key typed in this window reaches first
        self.windowHandle().installEventFilter(self._double_shift)
        app = QApplication.instance()
        if not hasattr(app, "_menu_tidier"):  # once for every window and menu
            app._menu_tidier = MenuTidier(app)
            app.installEventFilter(app._menu_tidier)

        self.setWindowIcon(icons.icon("component"))
        self.resize(1500, 950)
        self.refresh()
        self._update_title()
        # Only a way of working comes back: a tool for one job (align, measure, a
        # drawing tool) would greet you with a prompt you did not ask for.
        saved_tool = self.settings.value("tool", "select")
        self.set_tool(saved_tool if saved_tool in RESTORED_TOOLS else "select")

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

    def open_component(self, name: str) -> None:
        """Open a component in a tab (or go to its tab), like opening a file."""
        if not self.document.exists(name):
            self.report_error(f"unknown component '{name}'")
            return
        self._render(self.area.open(name))

    def open_process(self) -> None:
        """The Process tab: the process's constants and layer definitions."""
        self.area.open_process()
        self.state_changed()

    def _make_process_view(self) -> ProcessView:
        view = ProcessView(self.document)
        view.error.connect(self.report_error)
        return view

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
            canvas.context_requested.connect(
                lambda x, y, at, v=view: self._context_menu(v, x, y, at)
            )
            canvas.set_mode_actions([self.tool_actions[name] for name in CANVAS_MODES])
            canvas.component_dropped.connect(
                lambda name, x, y, v=view: self._component_dropped(v, name, x, y)
            )
            canvas.set_theme(self.canvas_theme)
            canvas.corner_color = self._frame_color()
            canvas.configure(**self._canvas_options())
            self._caption(view)
            canvas.left_pans = self.tool.name == "hand"
            canvas.set_tool_cursor(self.tool.cursor)
            canvas.show_rulers(self.rulers.get(view.component, []))
            view.checked.connect(lambda v=view: self._checked(v))
        return view

    def _checked(self, view: ComponentView) -> None:
        """A background rule check finished: show its violations."""
        if view is self.view:
            self._show_messages()
            self.update_overlay()

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
        """The canvas colours of a theme for every tab; remembered for next time."""
        self.settings.set("appearance/canvas_theme", theme)

    def _frame_color(self) -> QColor:
        """The window's frame colour: what shows around the islands."""
        return QColor(theme.get(self.ui_theme).ui["frame"])

    def _toggle_dark(self, checked: bool) -> None:
        self.set_canvas_theme("dark" if checked else "light")

    def _apply_ui_theme(self) -> str:
        app = QApplication.instance()
        return theme.apply(app, self.settings.get("appearance/ui_theme"))

    def _canvas_options(self) -> dict:
        options = {option: self.settings.get(key) for option, key in CANVAS_OPTIONS.items()}
        rate = options["max_fps"]  # "display", or a number of frames per second (0: none)
        options["max_fps"] = round(self.screen().refreshRate()) if rate == "display" else int(rate)
        return options

    def _setting_changed(self, key: str) -> None:
        """Apply a changed setting at once."""
        if key == "appearance/reduce_motion":
            motion.set_enabled(not self.settings.get(key))
        if key == "appearance/custom_title_bar":
            self.title_bar.set_enabled(self.settings.get(key))
        if key == "appearance/ui_theme":
            self.ui_theme = self._apply_ui_theme()
            self.refresh()  # panels and tabs pick up the new icon colours
        if key in ("appearance/ui_theme", "appearance/canvas_theme"):
            for view in self.area.views():
                view.canvas.set_theme(self.canvas_theme)
                view.canvas.corner_color = self._frame_color()
            self.dark_action.setChecked(theme.get(self.canvas_theme).dark)
            self.update_overlay()
        if key in CANVAS_OPTIONS.values():
            options = self._canvas_options()
            for view in self.area.views():
                view.canvas.configure(**options)
            self._show_zoom()
        if key == "editor/show_implementation":
            ComponentView.show_implementation = self.settings.get(key)
            self.tree.select_paths([])
            self.refresh()
        if key == "canvas/always_show_points":
            self.update_overlay()
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
        native = QKeySequence.SequenceFormat.NativeText
        shortcuts = [
            (action.text().replace("&", "").rstrip("…"), action.shortcut().toString(native), path)
            for path, action in menu_actions(self.actions_.root)
            if not action.shortcut().isEmpty()
        ]
        dialog = PreferencesDialog(self.settings, shortcuts, self)
        if page is not None:
            names = [dialog.pages.item(i).text() for i in range(dialog.pages.count())]
            if page in names:
                dialog.pages.setCurrentRow(names.index(page))
        self._preferences = dialog
        dialog.show()

    def search(self, scope: str = ALL) -> None:
        """Search Everywhere (Shift twice): components, shapes, parameters, commands."""
        dialog = SearchDialog(
            self.search_results(),
            scope,
            self,
            describe=lambda entry: component_picker.component_info(
                self.document, entry, self.layers.colors
            ),
        )
        center = self.geometry().center()
        dialog.move(center.x() - dialog.width() // 2, self.geometry().top() + 90)
        self._find_dialog = dialog
        dialog.show()
        dialog.search.setFocus()

    def find_action(self) -> None:
        """Find Action (Ctrl+Shift+A): Search Everywhere, on commands."""
        self.search("Actions")

    def search_results(self) -> list[Result]:
        """Everything Search Everywhere can find, in the order it lists them."""
        project = self.document.project
        placeable = {e.name: e for e in component_picker.entries(self.document)}
        editable = not self.document.read_only
        results = []
        for name in [
            *project.components,
            *(
                f"{lib}.{c}"
                for lib, library in project.libraries.items()
                for c in library.components
            ),
        ]:
            entry = placeable.get(name) or component_picker.Entry(  # e.g. the one edited
                name, component_picker.source_of(project, name), icon=component_icon(project, name)
            )
            can_place = editable and name in placeable
            results.append(
                Result(
                    name,
                    "Components",
                    lambda n=name: self.open_component(n),
                    detail=_component_detail(project, name),
                    icon=icons.icon(component_icon(project, name)),
                    entry=entry,
                    place=(lambda n=name: self.start_placing(n)) if can_place else None,
                )
            )
        results += [
            Result(
                shape.name or f"({shape.kind})",
                "Shapes",
                lambda p=path: self.tree.select_paths([p]),
                detail=f"{detail(shape)} · in {self.document.active}",
                icon=shape_icon(shape),
            )
            for path, shape in paths(self.document.shapes)
        ]
        results += [
            Result(
                parameter.name,
                "Parameters",
                lambda n=parameter.name: self.show_parameter(n),
                detail=f"= {parameter.default} · of {self.document.active}",
                icon=icons.icon("parameters"),
            )
            for parameter in self.document.active_definition.parameters
        ]
        return results + action_results(self.actions_.root)

    def show_parameter(self, name: str) -> None:
        """Open the Parameters panel on the parameter ``name``."""
        self.tool_windows.open("parameters")
        self.parameters.select(name)

    def open_aside(self, name: str) -> None:
        """Open a component in the other pane (splitting the editor if needed)."""
        if not self.document.exists(name):
            self.report_error(f"unknown component '{name}'")
            return
        self._render(self.area.open(name, self.area.other_pane()))

    def _component_dropped(self, view: ComponentView, name: str, x: float, y: float) -> None:
        """A component dragged from the explorer onto a canvas: place it there."""
        if view is not self.area.current:
            self.area.set_current(view)
        self.place_component_at(name, x, y)

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
                if not isinstance(view, ComponentView):
                    tabs.append({"process": True})
                    continue
                zoom, x, y = view.canvas.view_state()
                tabs.append(
                    {
                        "component": view.component,
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
                "explorer": sorted(self.components.expanded),
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
                self.notify(f"Could not save the editor state: {exc}", "warning")

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
            c: [tuple(float(v) for v in r) for r in rs if len(r) in (4, 6)]
            for c, rs in state.get("rulers", {}).items()
            if exists(c)
        }
        collapsed = state.get("collapsed", {})
        self.components.collapsed = set(collapsed.get("components", []))
        self.components.expanded = set(collapsed.get("explorer", []))
        self.tree.collapsed = {
            c: {_path(p) for p in paths} for c, paths in collapsed.get("shapes", {}).items()
        }
        panes = [
            {
                **pane,
                "tabs": [t for t in pane["tabs"] if t.get("process") or exists(t["component"])],
            }
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
                    if tab.get("process"):
                        self.area.open_process(target)
                        continue
                    view = self.area.open(tab["component"], target)
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
        if tool.draws:
            tool.once = False  # chosen as a tool: it stays on (see draw_once)
        self.tool_actions[name].setChecked(True)
        for view in self.area.views():
            view.canvas.set_tool_cursor(tool.cursor)
            view.canvas.show_hover(None)
            view.canvas.set_cursor_label(None)
            view.canvas.set_hover_label(None)
            view.canvas.set_over_selection(False)
        self._hovered = self._tip_for = None
        tool.activate()
        self.settings.set_value("tool", name)
        self._show_tool_options()
        self.update_overlay()

    def measure(self, mode: str) -> None:
        """The Measure tool, measuring distances or angles (D, N)."""
        self.set_tool("measure")
        self.tools["measure"].set_mode(mode)

    def set_left_pans(self, pans: bool) -> None:
        for view in self.area.views():
            view.canvas.left_pans = pans
            view.canvas.set_tool_cursor(self.tool.cursor)

    def prompt(self, text: str) -> None:
        """The hint for what to do next, in the status bar (a drag's label by the
        cursor goes: the drag is over)."""
        self.statusBar().showMessage(text)
        if self.area.current is not None:  # not while the window is being made
            self.area.current.canvas.set_cursor_label(None)

    def live(self, text: str, label: str | None = None) -> None:
        """The live value of a drag or of a shape being drawn: in the status bar, and
        ``label`` (by default the text before its parenthesised help) by the cursor."""
        self.statusBar().showMessage(text)
        self.canvas.set_cursor_label(label or text.split("   (")[0].strip())
        self.canvas.set_hover_label(None)

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
        once = getattr(self.tool, "once", False)  # Add › a shape: Esc ends it too
        if (not self.tool.cancel() or once) and self.tool.name != "select":
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

    def add_ruler(self, ruler: tuple[float, ...]) -> None:
        self.rulers.setdefault(self.document.active, []).append(ruler)
        self.draw_rulers()
        self.state_changed()

    def clear_rulers(self) -> None:
        self.rulers.pop(self.document.active, None)
        self.draw_rulers()
        self.state_changed()

    def draw_rulers(self, extra: tuple[float, ...] | None = None) -> None:
        """Show the rulers in every tab of the current component (plus one being drawn)."""
        for view in self.area.views():
            rulers = list(self.rulers.get(view.component, []))
            if extra is not None and view is self.area.current:
                rulers.append(extra)
            view.canvas.show_rulers(rulers)

    def _build_tool_windows(self) -> None:
        """The panels around the editor, opened from the stripes on the window edges."""
        self.tool_windows = ToolWindows(self.area)
        self.setCentralWidget(self.tool_windows)
        for name, title, icon, widget, anchor in (
            ("components", "Components", "component", self.components, "left-top"),
            ("shapes", "Shapes", "shapes", self.tree, "left-bottom"),
            ("layers", "Layers", "layers", self.layers, "left-bottom"),
            ("messages", "Messages", "messages", self.messages, "bottom"),
            ("properties", "Properties", "properties", self.properties, "right"),
            ("parameters", "Parameters", "parameters", self.parameters, "right"),
            ("points", "Points", "point", self.points, "right"),
            ("history", "History", "history", self.history, "right"),
        ):
            self.tool_windows.add(name, title, icon, widget, anchor, PANEL_HELP[name])
        self._restore_tool_windows()
        self.tool_windows.changed.connect(self._save_tool_windows)
        self.tool_windows.resized.connect(self._save_tool_windows)
        self.tool_windows.changed.connect(self.update_overlay)  # points follow the panel

    def _restore_tool_windows(self) -> None:
        """The tool windows as they were left, or the ones a first start opens."""
        try:
            state = json.loads(self.settings.value(TOOL_WINDOWS_KEY, "") or "{}")
        except (TypeError, ValueError):
            state = {}
        if not isinstance(state, dict) or not state.get("open"):
            state = {"open": list(DEFAULT_TOOL_WINDOWS)}
        self.tool_windows.restore(state)

    def _save_tool_windows(self) -> None:
        self.settings.set_value(TOOL_WINDOWS_KEY, json.dumps(self.tool_windows.state()))

    def _build_actions(self) -> None:
        self.actions_ = actions = Actions(self)
        self.addActions(actions.all_actions())  # shortcuts work without a menu bar
        for name in self.tool_windows.names():
            action = make_action(
                self,
                self.tool_windows.title(name),
                lambda _=False, n=name: self.tool_windows.toggle(n),
            )
            action.setCheckable(True)
            action.setChecked(self.tool_windows.is_open(name))
            self.tool_windows.button(name).toggled.connect(action.setChecked)
            actions.panels.addAction(action)
        # The names the rest of the window (and its tests) use.
        self.save_action, self.undo_action, self.redo_action = (
            actions.save,
            actions.undo,
            actions.redo,
        )
        self.make_action, self.unpack_action = actions.make, actions.unpack
        self.rotate_left_action, self.rotate_right_action = (
            actions.rotate_left,
            actions.rotate_right,
        )
        self.mirror_h_action, self.mirror_v_action = actions.mirror_h, actions.mirror_v
        self.find_action_action, self.dark_action = actions.find, actions.dark
        self.tool_actions, self.operation_actions = actions.tools, actions.operations
        self.setting_actions = actions.settings_toggles
        self.primitive_menu = actions.add
        bar = build_toolbar(self)
        self.addToolBar(bar)
        self.title_bar = TitleBar(self, bar)
        self.title_bar.set_enabled(self.settings.get("appearance/custom_title_bar"))

    def _show_tool_options(self) -> None:
        self.tool_status.show_for(self.tool)

    def _build_status_bar(self) -> None:
        status = StatusBar()
        self.setStatusBar(status)
        tool_status = self.tool_status = ToolStatus()
        # The names the tools and tests use.
        self.tool_icon, self.tool_name = tool_status.tool_icon, tool_status.tool_name
        self.layer_box, self.width_box = tool_status.layer_box, tool_status.width_box
        self.angle_box, self.tool_widgets = tool_status.angle_box, tool_status.tool_widgets
        self.layer_box.currentIndexChanged.connect(self._draw_layer_chosen)
        for mode, button in tool_status.mode_buttons.items():
            button.clicked.connect(lambda _=False, m=mode: self.measure(m))
        self.width_box.setValue(self.path_width)
        self.width_box.valueChanged.connect(self._path_width_chosen)
        self.angle_box.setValue(self.settings.get("snapping/angle_step"))
        self.angle_box.valueChanged.connect(lambda v: self.settings.set("snapping/angle_step", v))
        for key, label, icon_name in (
            ("snapping/points", "Snap to shape points", "snap_points"),
            ("snapping/grid", "Snap to the grid", "grid"),
        ):
            action = make_action(self, label, lambda checked, k=key: self.settings.set(k, checked))
            icons.bind(action, icon_name)
            action.setCheckable(True)
            action.setChecked(self.settings.get(key))
            tool_status.add_toggle(action)
            self.setting_actions[key] = action
        tool_status.add_toggle(self.setting_actions["canvas/show_gizmos"])

        self.problems_button = QToolButton()
        self.problems_button.setToolTip("Errors and rule violations (click to show)")
        self.problems_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.problems_button.clicked.connect(self._show_messages_panel)
        self.grid_label = fixed_label(GRID_SAMPLE, "Grid step at this zoom")
        self.zoom_label = fixed_label(ZOOM_SAMPLE, "Zoom (screen pixels per µm)")
        self.snap_target = QLabel()  # the point the cursor would snap to
        self.snap_target.setObjectName("status-target")
        self.snap_target.setMaximumWidth(TARGET_MAX_WIDTH)
        self.coordinates = fixed_label(POSITION_SAMPLE, "The cursor's position, in µm")
        status.arrange(
            tool_status,
            self.snap_target,
            self.problems_button,
            [tool_status.snapping, self.grid_label, self.zoom_label, self.coordinates],
        )

    def _show_zoom(self) -> None:
        canvas = self.canvas
        self.grid_label.setText(f"grid {canvas.grid_step():g} µm")
        self.zoom_label.setText(f"{canvas.pixels_per_um():.3g} px/µm")

    def waive(self, violation) -> None:
        """Accept a rule violation of the shown component, asking why."""
        reason, ok = QInputDialog.getText(
            self, "Waive a violation", f"Why is this {violation.rule} violation accepted?"
        )
        if ok:
            self._run(lambda: self.document.process.waive(violation, reason, self.view.component))

    def unwaive(self, violation) -> None:
        self._run(
            lambda: self.document.process.unwaive(
                violation.rule, violation.bbox_um, self.view.component
            )
        )

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
        self.tool_windows.open("messages")

    def _tab_menu(self, view: ComponentView, position: QPoint) -> None:
        self.show_menu(self.actions_.tab_menu(view), position)

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
        self._select_result(lambda: self.document.nodes.add(shape))

    # -- refresh -----------------------------------------------------------

    def refresh(self) -> None:
        """Recompile every open tab and update the panels (after any change). A big
        design is built in the background: the tabs show the last result meanwhile."""
        self.tool.design_changed()  # what it was doing was based on the previous state
        self._hovered = None  # it may be gone (deleted): the next move finds what is under
        self.canvas.show_hover(None)
        self._tip_for = None
        self._tip_timer.stop()
        self.canvas.set_hover_label(None)
        for view in self.area.views():
            if not self.document.exists(view.component):
                self.area.close_view(view)  # its component was deleted (or undone)
        if self.area.current is None or self.area.current.component != self.document.active:
            # e.g. undo went back to another component: go to its tab, wherever it is
            self._render(self.area.open(self.document.active, anywhere=True))
        self.layers.refresh()
        if self.area.process_view is not None:
            self.area.process_view.refresh()
        if self._slow():
            self._build_in_background()
        else:
            self._refresh_built()

    def _refresh_built(self) -> None:
        """Show what the components build to, in the tabs and the panels."""
        for view in self.area.views():
            view.rendered = True
            view.updating = False
            view.refresh(self.layers.colors, self.layers.visible)
            self._caption(view)
        self.properties.setEnabled(True)
        self.points.setEnabled(True)
        self.area.update_titles()
        self.draw_rulers()
        self._problems = self.document.results.problems()
        self._corner_errors = self._failing_corners()
        self._took(self.document.results.build_seconds)
        self._refresh_panels()

    def _build_in_background(self) -> None:
        """Build the open tabs on another thread; show the result when it is there.
        What needs it waits meanwhile: the tabs keep the last picture, and the panels
        that show built geometry are greyed out."""
        views = self.area.views()
        for view in views:
            view.updating = True
        version = self.document.results.version
        QTimer.singleShot(UPDATING_SHOWN_MS, lambda: self._show_updating(version))
        self.area.update_titles()
        self.history.refresh()
        self.undo_action.setEnabled(self.document.can_undo())
        self.redo_action.setEnabled(self.document.can_redo())
        self._update_title()
        future = self.document.results.build_in_background([v.component for v in views], builds())
        future.add_done_callback(self._report_build)

    def _show_updating(self, version: int) -> None:
        """A background build is taking a while: say so where it will show."""
        if self.document.results.version != version or self.document.results.ready():
            return
        for view in self.area.views():
            self._caption(view)
        self.properties.setEnabled(False)
        self.points.setEnabled(False)

    def _report_build(self, future) -> None:
        """In the worker thread: hand the finished build to the window's thread."""
        try:
            self._build_done.emit(future)
        except RuntimeError:  # the window was closed meanwhile
            pass

    def _take_build(self, future) -> None:
        if future.cancelled() or future.exception() is not None or future.result():
            self._refresh_built()  # with what it built (or, if it failed, saying why)
        # else: the design changed meanwhile, and the newer build shows when it is done

    def _took(self, seconds: dict[str, float]) -> None:
        """How long components took to build: whether the next change builds the open
        tabs in the background, and whether an edit waits to see that it builds."""
        for component, took in seconds.items():
            self.build_seconds[component] = max(took, self.build_seconds.get(component, 0.0))
        self.document.check_builds = not self._slow()

    def _slow(self) -> bool:
        """Whether a component of an open tab has taken long to build."""
        times = self.build_seconds
        return any(times.get(v.component, 0.0) > BACKGROUND_BUILD_S for v in self.area.views())

    def implementation_hidden(self, view: ComponentView | None = None) -> bool:
        """A read-only component shows its interface only (unless the user asked to see
        how it is built): public parameters, points and geometry, no shapes."""
        view = view or self.view
        return view.implementation_hidden

    def _refresh_panels(self) -> None:
        """Show the current tab in the panels, toolbar and title."""
        view = self.view
        hidden = self.implementation_hidden(view)
        self.tree.hide_implementation = hidden
        self.parameters.hide_implementation = hidden
        shown = self.settings.get("editor/show_implementation")
        self.components.hide_implementation = not shown
        self.tree.show_implementation = shown
        self.properties.hide_implementation = hidden
        self.parameters.refresh()
        self.points.refresh()
        self.history.refresh()
        self._refresh_layer_box()
        self.components.refresh()
        self.tree.rebuild([p for p in view.selection if self._exists(p)])
        self._show_messages()
        self.undo_action.setEnabled(self.document.can_undo())
        self.redo_action.setEnabled(self.document.can_redo())
        self.undo_action.setText(f"Undo {self.document.undo_text()}".strip())
        self.redo_action.setText(f"Redo {self.document.redo_text()}".strip())
        self._update_title()

    def _caption(self, view: ComponentView) -> None:
        """The canvas caption: the component and whether it can be edited."""
        details = ["updating…"] if view.updating else []
        if view.read_only:
            details.append("read-only")
        if view.component == self.document.project.top:
            details.append("top component")
        view.canvas.set_caption(view.component, " · ".join(details))

    def _show_messages(self, *extra: str) -> None:
        view = self.view
        errors = [*extra, *view.errors, *(p for p in self._problems if p not in view.errors)]
        self.messages.show_messages(errors, view.violations, checking=view.checking)

    def _failing_corners(self) -> list[tuple[float, float]]:
        """Rounded corners of the open component whose radius does not fit (when the
        build says a corner is wrong): marked on the canvas like rule violations."""
        texts = [*self._problems, *self.view.errors]
        if not any("corner" in text for text in texts):
            return []
        found = []
        for path, shape in paths(self.document.shapes):
            if any(isinstance(m, CornersModifier) for m in shape.modifiers):
                with contextlib.suppress(Exception):  # it does not build at all: no mark
                    found += self.document.corners.failing(path)
        return found

    def update_overlay(self) -> None:
        view = self.view
        markers = [v.bbox_um for v in view.violations if v.bbox_um and not v.waived]
        reach = CORNER_ERROR_MARK_PX / self.canvas.pixels_per_um()
        markers += [(x - reach, y - reach, x + reach, y + reach) for x, y in self._corner_errors]
        results = self.document.results
        single = view.selection[0] if len(view.selection) == 1 else None
        shown = self.points_shown()
        box = results.node_box(single) if shown and single is not None else None
        self.canvas.show_overlay(results.highlights(view.selection), markers, box)
        self.canvas.show_alignment(*self.alignment_link(single))
        self.canvas.show_guides(view.guides, set(view.selection))
        changes = (
            self.history.geometry(view.component) if self.tool_windows.is_open("history") else None
        )
        self.canvas.show_changes(*(changes or (None, None)))
        open_ = self.tool_windows.is_open("points")
        focus = (
            [m for m in (self.points.focused_marker(), self._hovered_point) if m] if open_ else []
        )
        declared = []
        if shown:
            try:
                declared = [(n, x, y) for n, (x, y) in results.declared_points().items()]
            except Exception:  # noqa: BLE001 - the messages panel shows why
                declared = []
        highlighted = {m[0] for m in focus}  # drawn once, highlighted
        self.canvas.show_points("declared", [d for d in declared if d[0] not in highlighted], True)
        tool_markers = self.tool.markers()
        selected = results.node_points(single) if shown and single is not None else []
        self.canvas.show_points("selected", [] if "pick" in tool_markers else selected)
        self.canvas.show_points("focus", focus, labels=True)
        for style in ("pick", "anchor"):
            self.canvas.show_points(style, tool_markers.get(style, []))
        self._place_gizmo()
        self._show_zoom()

    def points_shown(self) -> bool:
        """Points are drawn while you work with them: the Points panel is open (or
        the setting says always); the align tool shows its own candidates."""
        return self.settings.get("canvas/always_show_points") or self.tool_windows.is_open("points")

    def _go_to_change(self, change) -> None:
        """A change clicked in the History panel: open its component and select the
        shape (or what held a removed one), panning to it."""
        if change.component is None or not self.document.exists(change.component):
            return
        self.open_component(change.component)
        if change.path is None or not self._exists(change.path):
            return
        self.tree.select_paths([change.path])
        box = self.document.results.node_box(change.path)
        if box:
            xs, ys = [x for x, _ in box], [y for _, y in box]
            zoom = self.canvas.pixels_per_um()
            self.canvas.set_view_state(zoom, (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)

    def _point_hovered(self, marker) -> None:
        self._hovered_point = marker
        self.update_overlay()

    def _point_focused(self, marker) -> None:
        """A point chosen in the Points panel: pan to it (the zoom stays)."""
        if marker is not None:
            zoom = self.canvas.pixels_per_um()
            self.canvas.set_view_state(zoom, marker[1], marker[2])
        self.update_overlay()

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
        center = self.document.results.selection_center(view.selection) if shown else None
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
        self.project_label.setText(f"{star}{project.name}")
        self.project_label.setToolTip(where)

    # -- selection ---------------------------------------------------------

    def _tree_selected(self, paths: list[NodePath]) -> None:
        picked = [p for p in paths if p not in self.selection]
        self.selection = paths
        if picked and not self._quiet_selection:  # chosen in a panel, search or history
            regions = dict(getattr(self.view, "node_regions", ()))
            self.canvas.pulse(*(regions[p] for p in picked if p in regions))
        self.properties.show_node(paths[0] if len(paths) == 1 else None)
        self.update_overlay()
        self.state_changed()

    def _pick_alignment(self, field: str, point: str, to: str) -> None:
        """A pick button of the alignment in Properties: the Align tool picks that end
        on the canvas, then it is back to Select."""
        if len(self.selection) != 1:
            return
        path = self.selection[0]
        self.set_tool("align")
        self.tool.begin(path, only=field, point=point, to=to)

    def alignment_link(self, path: NodePath | None) -> tuple:
        """How the shape at ``path`` is aligned, for the canvas: its point and the point
        it is kept on, each (name, x, y); (None, None) when it is not aligned, while a
        tool is busy, or when either point cannot be found."""
        if path is None or self.tool.busy:
            return None, None
        try:
            align = self.document.node(path).align
            if align is None:
                return None, None
            results = self.document.results
            point = next((p for p in results.node_points(path) if p[0] == align.point), None)
            target = next(
                ((n, x, y) for n, _, x, y in results.align_targets(path) if n == align.to), None
            )
        except Exception:  # noqa: BLE001 - not built: nothing to show
            return None, None
        return point, target

    def select_all(self) -> None:
        """Select every shape shown in the component (Edit › Select all)."""
        self._select_quietly([p for p, _ in getattr(self.view, "node_regions", ())])

    def zoom_to_selection(self) -> None:
        """Glide to show the selected shapes as large as they fit (View › Zoom to selection)."""
        regions = dict(getattr(self.view, "node_regions", ()))
        self.canvas.frame(*(regions.get(p) for p in self.selection))

    def rename_selection(self) -> None:
        """Start renaming the selected shape: its name in Properties, ready to type over."""
        if len(self.selection) != 1:
            return
        self.tool_windows.open("properties")
        name = self.properties.findChild(QLineEdit, "title-edit")
        if name is not None and not name.isReadOnly():
            name.setFocus(Qt.FocusReason.ShortcutFocusReason)
            name.selectAll()

    def _select_quietly(self, paths: list[NodePath]) -> None:
        """Select what was clicked on the canvas: it is where the user looks, so it
        does not glow as a shape picked in a panel does."""
        self._quiet_selection = True
        try:
            self.tree.select_paths(paths)
        finally:
            self._quiet_selection = False

    def _cursor_moved(self, x: float, y: float) -> None:
        self.coordinates.setText(f"x {x:.3f}  y {y:.3f}")
        self.snap_target.setText(self.tool.hover_label(x, y) or "")
        self._hover(x, y)

    def _hover(self, x: float, y: float) -> None:
        """Outline the shape under the cursor (what a click would select); after a
        pause, say what it is; over the selection, show that a drag moves it."""
        tool = self.tool
        free = tool.hovers and not tool.busy and self.canvas.gizmo_hit(x, y) is None
        under = self.hit(x, y) if free else None
        hovered = under
        if hovered in self.selection or not self.settings.get("canvas/hover_highlight"):
            hovered = None
        if hovered != self._hovered:
            self._hovered = hovered
            geometry = dict(self.view.node_regions).get(hovered) if hovered else None
            self.canvas.show_hover(geometry)
        if under != self._tip_for:
            self._tip_for = under
            self.canvas.set_hover_label(None)
            if under is not None:
                self._tip_timer.start()
            else:
                self._tip_timer.stop()
        self.canvas.set_over_selection(
            free and tool.name == "select" and under is not None and under in self.selection
        )

    def _show_tip(self) -> None:
        if self._tip_for is None or self.canvas.cursor_label or self.tool.busy:
            return
        try:
            self.canvas.set_hover_label(self.describe_node(self._tip_for))
        except KeyError:  # gone meanwhile
            self._tip_for = None

    def describe_node(self, path: NodePath) -> str:
        """A shape in a few words: its name, what it is and its layer (``bar · rect ·
        device``), or the component it places (``comb_top · comb_drive``)."""
        node = self.document.node(path)
        parts = [node.name or f"unnamed {node.kind}"]
        component = getattr(node, "component", None)
        if component:
            parts.append(component)
        else:
            parts.append(node.kind)
            layer = getattr(node, "layer", None)
            if layer:
                parts.append(layer)
        return " · ".join(parts)

    def _view_double_clicked(self, view: ComponentView, x: float, y: float) -> None:
        """Double-clicking a placed component opens it in a tab (unless a tool uses it)."""
        if view is self.area.current and self.tool.double_click(x, y):
            return
        hit = self._hit(view, x, y)
        if hit is not None:
            self._open_reference(hit, view.component)

    def _tree_double_clicked(self, item, _column: int) -> None:
        inside = item.data(0, INSIDE_ROLE)
        if inside is not None:  # part of a placed component: edit it in its own tab
            owner, path = inside
            self.open_component(owner)
            if self.document.active == owner:
                self.tree.select_paths([path])
            return
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path is not None:
            self._open_reference(path, self.document.active)

    def _open_reference(self, path: NodePath, component: str) -> None:
        target = self.document.reference_target(path, component)
        if target is not None:
            self.open_component(target)

    def _preview_node(self, node, path=None) -> None:
        """Draw the current tab as it would be with a node changed (the selected one
        by default: a value being dragged in Properties); applying it redraws
        everything as usual."""
        view, path = self.view, path or self.properties.path
        if path is None:
            return
        try:
            geometry, outline, shapes = self.document.results.preview_outlined(path, node)
        except Exception:  # noqa: BLE001 - not valid: keep the last picture
            return
        if view.implementation_hidden:
            shapes = []
        view.canvas.show_geometry(geometry, self.layers.colors, self.layers.visible, shapes)
        # The selection stays outlined, as it is now (the other selected shapes too).
        others = self.document.results.highlights([p for p in view.selection if p != path])
        view.canvas.show_overlay([*others, *([outline] if outline else [])], [])

    def _hit(self, view: ComponentView, x: float, y: float) -> NodePath | None:
        if self.implementation_hidden(view):
            return None  # its shapes are not shown
        hit = self._region_hit(view, x, y) or self._guide_hit(view, x, y)
        if hit is None:  # nothing right under it: the nearest within a few pixels
            reach = HIT_REACH_PX / view.canvas.pixels_per_um()
            hit = self._region_hit(view, x, y, reach)
        return hit

    @staticmethod
    def _region_hit(view: ComponentView, x: float, y: float, reach: float = 0.0) -> NodePath | None:
        """The topmost shape within ``reach`` µm of ``(x, y)``."""
        return next(
            (p for p, geometry in reversed(view.node_regions) if geometry.touches(x, y, reach)),
            None,
        )

    def _guide_hit(self, view: ComponentView, x: float, y: float) -> NodePath | None:
        """The top-level shape holding a guide line near ``(x, y)`` (a few pixels)."""
        reach = GUIDE_REACH_PX / view.canvas.pixels_per_um()
        for path, _name, start, end in reversed(view.guides):
            if _distance_to_segment((x, y), start, end) <= reach:
                return path[:1]
        return None

    def hit(self, x: float, y: float) -> NodePath | None:
        """The top-level shape under ``(x, y)`` in the current tab.

        Between the parts of the outlined shape (a comb's fingers) it is still that
        shape, while inside its box: the outline does not flicker on and off, and a
        click selects what is outlined.
        """
        found = self._hit(self.view, x, y)
        if found is None and self._hovered is not None:
            geometry = dict(self.view.node_regions).get(self._hovered)
            box = geometry.bbox() if geometry is not None else None
            if box is not None and _box_contains(box, x, y):
                return self._hovered
        return found

    def on_selection(self, x: float, y: float) -> bool:
        """Whether ``(x, y)`` lies on one of the selected shapes."""
        for path in self.selection:
            geometry = self.document.results.highlight([path])
            if geometry is not None and geometry.touches(x, y):
                return True
        return False

    def box_touches(self, x0: float, x1: float) -> bool:
        """Whether a box dragged from ``x0`` to ``x1`` selects what it touches (else
        what is entirely inside it), as the ``editor/box_selection`` setting has it."""
        mode = self.settings.get("editor/box_selection")
        return mode == "touching" or (mode == "direction" and x1 < x0)

    def select_box(self, x0: float, y0: float, x1: float, y1: float, additive: bool) -> None:
        """Select the top-level shapes in a box dragged from ``(x0, y0)`` to ``(x1, y1)``:
        those it touches, or those entirely inside it (see :meth:`box_touches`)."""
        if self.implementation_hidden():
            return
        box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        if self.box_touches(x0, x1):
            inside = [p for p, geometry in self.view.node_regions if geometry.overlaps_box(*box)]
            inside += [  # guides draw nothing: a guide is touched where its line is
                path[:1]
                for path, _name, start, end in self.view.guides
                if _segment_meets_box(start, end, box)
            ]
        else:
            inside = [
                p
                for p, geometry in self.view.node_regions
                if (b := geometry.bbox()) is not None
                and _box_contains(box, b[0], b[1])
                and _box_contains(box, b[2], b[3])
            ]
            inside += [  # guides draw nothing: they are inside when both ends are
                path[:1]
                for path, _name, start, end in self.view.guides
                if _box_contains(box, *start) and _box_contains(box, *end)
            ]
        paths = list(dict.fromkeys([*self.selection, *inside])) if additive else inside
        self._select_quietly(paths)

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
        self._select_quietly(paths)

    def _zoom_to_bbox(self, bbox: tuple[float, float, float, float]) -> None:
        x0, y0, x1, y1 = bbox
        size = max(x1 - x0, y1 - y0, 5.0)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        self.canvas.zoom_to(QRectF(cx - size * 2, cy - size * 2, size * 4, size * 4), animated=True)

    def _context_menu(self, view: ComponentView, x: float, y: float, at: QPoint) -> None:
        """The editor's right-click menu; a shape under the cursor is selected first,
        and empty space clears the selection (its menu is for adding there)."""
        if view is not self.area.current:
            self.area.set_current(view)
        hit = self._hit(view, x, y)
        if hit is None and self.selection:
            self._select_quietly([])
        elif hit is not None and hit not in self.selection:
            self._select_quietly([hit])
        self.show_menu(self.actions_.context_menu(x, y), at)

    def show_menu(self, menu: QMenu, at: QPoint) -> None:
        """Pop up a menu at a global position; it deletes itself when closed."""
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.popup(at)

    def draw_once(self, kind: str) -> None:
        """Add › a shape: its drawing tool for one shape, then back to Select.

        Chosen from the toolbar or by its shortcut, a drawing tool stays active
        for the next shape instead.
        """
        self.set_tool(kind)
        self.tool.once = True

    def start_drawing(self, kind: str, x: float, y: float) -> None:
        """Right-click › Add: draw one shape, with its first point at ``(x, y)``."""
        self.draw_once(kind)
        self.tool.start_at(x, y)

    def _edit_top(self) -> None:
        if self.document.project.top is None:
            self.report_error("this project is a library: it has no top component")
            return
        self.open_component(self.document.project.top)

    # -- commands ----------------------------------------------------------

    def notify(
        self,
        text: str,
        kind: str = "info",
        action: tuple[str, Callable] | None = None,
        seconds: float | None = None,
        key: str = "",
    ) -> Toast:
        """A note over the editor about what just happened (see :mod:`toast`):
        ``kind`` is info, success, warning or error (which stays until closed)."""
        return self.toasts.show_toast(text, kind, action, seconds, key)

    def report_error(self, message: str) -> None:
        self.notify(message, "error", ("Show messages", self._show_messages_panel))
        self._show_messages(message)

    def undo(self) -> None:
        """Undo, saying what was undone."""
        if self.document.can_undo():
            self._step_history(self.document.undo_text(), self.document.undo, "Undid")

    def redo(self) -> None:
        if self.document.can_redo():
            self._step_history(self.document.redo_text(), self.document.redo, "Redid")

    def _step_history(self, text: str, step: Callable[[], None], done: str) -> None:
        before = self.document.active
        step()
        where = f" in {self.document.active}" if self.document.active != before else ""
        self.notify(f"{done} {text}{where}".strip(), "info", seconds=2, key="history-step")

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
            self._select_quietly([path])  # just made where the user is looking

    def add_primitive(self, kind: str) -> None:
        self._select_result(lambda: self.document.nodes.add_primitive(kind))

    def add_component(self, name: str) -> None:
        self._select_result(lambda: self.document.nodes.add_component(name))

    # -- placing components (see mems_sketch.gui.component_picker) ------------

    def place_popup(
        self, at: tuple[float, float] | None = None, near: QPoint | None = None
    ) -> PlacePopup | None:
        """The Place popup: by ``near`` (global), else by the mouse on the canvas, else
        under the Place button. A component chosen follows the cursor until a click
        puts it down, or, with ``at`` (µm, a right-click), lands there."""
        if self.document.read_only:
            self.report_error("this component cannot be edited: copy it into the project first")
            return None
        popup = PlacePopup(component_picker.entries(self.document), self)
        if at is None:
            popup.chosen.connect(self.start_placing)
        else:
            popup.chosen.connect(lambda name: self.place_component_at(name, *at))
        popup.browse.connect(lambda: self.search("Components"))
        if near is None:
            cursor = QCursor.pos()
            viewport = self.canvas.viewport()
            if viewport.rect().contains(viewport.mapFromGlobal(cursor)):
                near = cursor
            else:
                button = self.place_button
                near = button.mapToGlobal(button.rect().bottomLeft())
        popup.popup(near)
        self._place_popup = popup
        return popup

    def start_placing(self, name: str) -> None:
        """Put ``name`` under the cursor; a click places it (see ``PlaceTool``)."""
        self.set_tool("place")
        if not self.tools["place"].begin(name):
            self.set_tool("select")

    def place_component_at(self, name: str, x: float, y: float) -> None:
        """Place ``name`` with its origin at ``(x, y)`` µm and remember it as recent."""
        if name == self.document.active:
            self.report_error("a component cannot be placed inside itself")
            return
        component_picker.remember(name)
        self._select_result(lambda: self.document.nodes.add_component(name, x, y))

    def wrap(self, operation: str) -> None:
        self._select_result(lambda: self.document.nodes.wrap(self.selection, operation))

    def make_component(self) -> None:
        if not self.selection:
            self.report_error("select the shapes to turn into a component first")
            return
        name, ok = QInputDialog.getText(self, "Make component", "Name of the new component:")
        if ok and name.strip():
            self._select_result(lambda: self.document.components.make(self.selection, name.strip()))

    # -- moving, rotating, mirroring ---------------------------------------

    def nudge(self, steps_x: int, steps_y: int, fine: bool = False) -> None:
        """Move the selection by grid steps (a tenth of one when ``fine``)."""
        if not self.selection or self.tool.busy:
            return
        step = self.canvas.grid_step() / (10 if fine else 1)
        paths = list(self.selection)
        self._run(lambda: self.document.moves.move(paths, steps_x * step, steps_y * step))

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
            self.document.moves.move(paths, *parts)

        self._run(move)

    def rotate_selection(self, angle: float) -> None:
        """Rotate the selection about the centre of its bounding box."""
        center = self.document.results.selection_center(self.selection) if self.selection else None
        if center is None:
            self.report_error("select the shapes to rotate first")
            return
        paths = list(self.selection)
        self._run(lambda: self.document.moves.rotate(paths, angle, center))

    def mirror_selection(self, left_right: bool) -> None:
        center = self.document.results.selection_center(self.selection) if self.selection else None
        if center is None:
            self.report_error("select the shapes to mirror first")
            return
        paths = list(self.selection)
        self._run(lambda: self.document.moves.mirror(paths, left_right, center))

    def remove_alignment(self) -> None:
        if len(self.selection) == 1:
            path = self.selection[0]
            self._run(lambda: self.document.nodes.set_align(path, None))

    def unpack(self) -> None:
        if len(self.selection) == 1:
            self._select_result(lambda: self.document.components.unpack(self.selection[0]))

    def unwrap(self) -> None:
        if len(self.selection) == 1:
            self._run(lambda: self.document.nodes.unwrap(self.selection[0]))

    def duplicate(self) -> None:
        if len(self.selection) == 1:
            self._select_result(lambda: self.document.nodes.duplicate(self.selection[0]))

    def delete(self) -> None:
        if self.selection:
            paths, self.selection = self.selection, []
            self._run(lambda: self.document.nodes.remove(paths))

    def copy(self) -> None:
        """The selected shapes onto the clipboard, as text (other windows can paste them)."""
        if self.selection:
            ok, text = self._run(lambda: self.document.clipboard.copy(list(self.selection)))
            if ok:
                QApplication.clipboard().setText(text)
                count = len(self.selection)
                self.notify(f"Copied {count} shape{'s' if count != 1 else ''}", seconds=2)

    def cut(self) -> None:
        if self.selection:
            paths, self.selection = list(self.selection), []
            ok, text = self._run(lambda: self.document.clipboard.cut(paths))
            if ok:
                QApplication.clipboard().setText(text)

    def can_paste(self) -> bool:
        return is_shapes(QApplication.clipboard().text())

    def paste(self) -> None:
        """The shapes on the clipboard into the component in the current tab, selected."""
        text = QApplication.clipboard().text()
        if not is_shapes(text):
            self.notify("The clipboard holds no shapes to paste", seconds=3)
            return
        ok, pasted = self._run(lambda: self.document.clipboard.paste(text))
        if ok and pasted:
            self._select_quietly(list(pasted))
            for note in pasted.notes:
                self.notify(note, "warning")

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

    def new_project(self, library: bool = False) -> None:
        """The New Project wizard: name, folder, process and libraries; saved at once.

        Unsaved changes are asked about once the wizard is finished, not before.
        """
        last = Path(self._last_dir())  # the last project's folder: offer the one it is in
        location = last.parent if (last / "project.yaml").exists() else last
        dialog = NewProjectDialog(str(location), library, self)
        if self.show_dialog(dialog) == QDialog.DialogCode.Accepted and self._confirm_discard():
            self.create_project(**dialog.values())

    def show_dialog(self, dialog: QDialog) -> int:
        """Run a dialog (tests fill it in instead)."""
        return dialog.exec()

    def create_project(self, folder: Path, name: str, **options) -> bool:
        """Start a new project or library in ``folder`` (see ``EditSession.create``)."""

        def create() -> None:
            self.document.create(folder, name, **options)  # raises before anything changes
            self.area.close_all()
            self.build_seconds = {}
            self.rulers = {}
            self.layers.visible = {}
            self.refresh()

        self.save_editor_state()
        if not self._run(create)[0]:
            return False
        self._remember_dir(str(Path(folder) / "project.yaml"))
        return True

    def new_library(self) -> None:
        """A new project without a top component: a set of components to place elsewhere."""
        self.new_project(library=True)

    def open_project(self, path: str | None = None) -> None:
        """Open a project; without ``path``, the one chosen in a file dialog.

        The file is chosen first. Then, if this window holds a project, the
        user chooses to open it here or in a new window; only opening it here
        asks about unsaved changes.
        """
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, "Open project", self._last_dir(), OPEN_FILTER
            )
            if not path:
                return
            if self.document.path is not None or self.document.dirty:
                where = self.ask_where_to_open(path)
                if where is None:
                    return
                if where == "new":
                    self._remember_dir(path)
                    self.open_in_new_window(path)
                    return
        if not self._confirm_discard():
            return
        self.save_editor_state()
        if self._run(lambda: self._open(path))[0]:
            self._remember_dir(path)
            if self.document.project.load_notes:
                self.notify("; ".join(self.document.project.load_notes), "warning", seconds=15)
            elif self.document.path is None:
                self.notify(
                    f"Opened {Path(path).name} as a copy: use Save as… to store it as a "
                    "project folder",
                    "info",
                    ("Save as…", self.save_project_as),
                    seconds=10,
                )

    def ask_where_to_open(self, path: str) -> str | None:
        """``"here"``, ``"new"`` (another window) or None (cancelled)."""
        box = QMessageBox(self)
        box.setWindowTitle("Open project")
        chosen = Path(path)
        name = chosen.parent.name if chosen.name == "project.yaml" else chosen.name
        box.setText(f"Open {name} in this window or in a new one?")
        here = box.addButton("This window", QMessageBox.ButtonRole.AcceptRole)
        new = box.addButton("New window", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(here)
        box.exec()
        return {id(here): "here", id(new): "new"}.get(id(box.clickedButton()))

    def open_in_new_window(self, path: str) -> None:
        """Open a project in another copy of the program, a window of its own."""
        if getattr(sys, "frozen", False):  # the bundled app: the program is the executable
            command = [sys.executable, path]
        else:
            command = [sys.executable, "-m", "mems_sketch.gui", path]
        subprocess.Popen(command, start_new_session=sys.platform != "win32")

    def _open(self, path: str) -> None:
        self._restoring = True
        try:
            self.document.open(path)  # raises before anything changes if the file is bad
            self.area.close_all()
            self.build_seconds = {}
            self.rulers = {}
            self.layers.visible = {}
            self.components.collapsed = set()
            self.components.expanded = set()
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

    def export_file(self) -> None:
        exporters = available_exporters()
        filters = {
            f"{title_of(cls)} (*{cls.file_extension})": name for name, cls in exporters.items()
        }
        path, chosen = QFileDialog.getSaveFileName(
            self, "Export", self._last_dir(), ";;".join(filters)
        )
        if not path:
            return
        # A known extension decides the format; otherwise the chosen filter's
        # is added.
        by_extension = {c.file_extension: n for n, c in exporters.items()}
        format_name = by_extension.get(Path(path).suffix.lower())
        if format_name is None:
            format_name = filters.get(chosen) or next(iter(exporters))
            path += exporters[format_name].file_extension
        cls = exporters[format_name]
        options = options_of(cls)
        values: dict = {}
        if options:
            process = self.document.project.process
            start = {"grid_um": process.grid_um, "chord_um": process.chord_um}
            dialog = ExportOptionsDialog(
                title_of(cls), options, remembered(self.settings, format_name, options, start), self
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            values = dialog.values()
            remember(self.settings, format_name, options, values)
        ok, result = self._run(lambda: self.document.export_with_report(path, format_name, values))
        if ok:
            self._remember_dir(path)
            written = result[1]
            changes = written.describe() if hasattr(written, "describe") else []
            if changes:  # snapping to the file's grid changed the shape (requirement OUT-3)
                self.show_export_changes(path, changes)
            self.notify(
                f"Exported {Path(path).name}",
                "success",
                ("Show in folder", lambda: open_folder(Path(path).parent)),
            )

    def show_export_changes(self, path: str, changes: list[str]) -> None:
        """Say what snapping to the export's grid changed in the shape of the design."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Export")
        box.setText(
            f"Exported {Path(path).name}, but snapping to its grid changed the shape of the design:"
        )
        box.setInformativeText("\n".join(changes[:20]) + ("\n…" if len(changes) > 20 else ""))
        box.setDetailedText("\n".join(changes))
        box.exec()

    def _last_dir(self) -> str:
        return str(self.settings.value("last_dir", str(Path.home())))

    def _remember_dir(self, path: str) -> None:
        self.settings.set_value("last_dir", str(Path(path).parent))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._confirm_discard():
            self.save_editor_state()
            self._save_tool_windows()
            event.accept()
        else:
            event.ignore()


def _component_detail(project, name: str) -> str:
    """What Search Everywhere shows by a component: where it is, and its tags."""
    if name in project.imports:
        return "imported"
    where = "library" if "." in name else "project"
    found = project.definition(name)
    tags = found[0].tags if found is not None else []
    return f"{where} · {', '.join(tags)}" if tags else where


def _segment_meets_box(start, end, box: tuple[float, float, float, float]) -> bool:
    """Whether the segment from ``start`` to ``end`` has a point in the box."""
    (ax, ay), (bx, by) = start, end
    left, bottom, right, top = box
    if max(ax, bx) < left or min(ax, bx) > right or max(ay, by) < bottom or min(ay, by) > top:
        return False
    sides = [
        (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        for cx, cy in ((left, bottom), (right, bottom), (right, top), (left, top))
    ]
    return min(sides) <= 0 <= max(sides)


def _box_contains(box: tuple[float, float, float, float], x: float, y: float) -> bool:
    """``(x, y)`` lies in ``(left, bottom, right, top)``, to within half a database unit."""
    x0, y0, x1, y1 = box
    eps = BOX_EPS_UM
    return x0 - eps <= x <= x1 + eps and y0 - eps <= y <= y1 + eps


def _distance_to_segment(p, a, b) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _path(steps) -> NodePath:
    return tuple((int(slot), int(index)) for slot, index in steps)


def main(argv: list[str] | None = None) -> int:
    # First: in a bundled app the rule-check process starts as another copy of
    # the program, which must run its task, not open a window.
    multiprocessing.freeze_support()
    argv = sys.argv if argv is None else argv
    if len(argv) > 1 and argv[1] in ("--version", "--self-test"):
        from mems_sketch import __version__

        if argv[1] == "--version":
            print(f"mems-sketch {__version__}")
            return 0
        from mems_sketch.gui.selftest import run, say

        try:
            run()
        except Exception:  # noqa: BLE001 - reported, and the exit code says so
            import traceback

            say(traceback.format_exc())
            say("self-test FAILED")
            return 1
        return 0
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("MEMS Sketch")
    window = MainWindow()
    if len(argv) > 1:
        window.open_project(argv[1])
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
