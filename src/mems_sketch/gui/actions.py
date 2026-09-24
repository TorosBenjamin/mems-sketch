"""Every command of the main window as a ``QAction``, and the menus built from them.

A command is defined once here and can then be put in any menu, the toolbar
or the editor's right-click menu. The actions call the window's methods;
this module holds no editing logic of its own.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QMenu

from mems_sketch.gui import icons
from mems_sketch.gui.views import VIEW_MODES

if TYPE_CHECKING:
    from mems_sketch.gui.app import MainWindow

PRIMITIVES = [
    ("rect", "Rectangle"),
    ("circle", "Circle"),
    ("arc", "Arc / ring"),
    ("polygon", "Polygon"),
    ("path", "Path"),
    ("guide", "Guide"),
]
OVERLAYS = [  # View › Overlays: setting, label, icon
    ("canvas/show_grid", "Grid", "grid"),
    ("canvas/show_axes", "Coloured axes", "axes"),
    ("canvas/show_axis_gizmo", "Axis indicator", "axes"),
    ("canvas/show_scale_bar", "Scale bar", "ruler"),
    ("canvas/show_gizmos", "Move and rotate gizmos", "move"),
    ("canvas/hover_highlight", "Highlight under cursor", "select"),
    ("canvas/always_show_points", "Always show points", "point"),
]
OPERATIONS = [
    ("subtract", "Subtract"),
    ("intersect", "Intersect"),
    ("xor", "XOR"),
    ("offset", "Offset"),
    ("fillet", "Fillet"),
    ("transform", "Transform"),
    ("layer_map", "Layer map"),
]
BOOLEANS = ("subtract", "intersect", "xor")  # under Operations › Combine


def _label(window, path) -> str:
    try:
        node = window.document.node(path)
    except KeyError:
        return "?"
    return node.name or node.kind


def make_action(
    parent, text: str, slot, shortcut=None, menu: QMenu | None = None, icon: str | None = None
) -> QAction:
    """An action calling ``slot``, with its shortcut in the tooltip, added to ``menu``."""
    action = QAction(text, parent)
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


class Actions:
    """The window's commands and menus. ``root_menus`` is the menu bar's content."""

    def __init__(self, window: MainWindow) -> None:
        w = self.window = window
        act = functools.partial(make_action, w)

        file = self.file = QMenu("&File", w)
        act("New project", lambda: w.new_project(), QKeySequence.StandardKey.New, file, "new")
        act("New library", w.new_library, None, file, "library")
        act("Open project…", w.open_project, QKeySequence.StandardKey.Open, file, "open")
        self.save = act("Save", w.save_project, QKeySequence.StandardKey.Save, file, "save")
        act("Save as…", w.save_project_as, QKeySequence.StandardKey.SaveAs, file)
        file.addSeparator()
        export = file.addMenu("Export")
        icons.bind(export.menuAction(), "export")
        for mode, label in VIEW_MODES.items():
            act(f"{label} geometry…", lambda _=False, m=mode: w.export_file(m), menu=export)
        file.addSeparator()
        self.settings = act("Settings…", w.show_settings, "Ctrl+Alt+S", file, "settings")
        file.addSeparator()
        act("Quit", w.close, QKeySequence.StandardKey.Quit, file)

        edit = self.edit = QMenu("&Edit", w)
        document = w.document
        self.undo = act("Undo", document.undo, QKeySequence.StandardKey.Undo, edit, "undo")
        self.redo = act("Redo", document.redo, QKeySequence.StandardKey.Redo, edit, "redo")
        edit.addSeparator()
        self.duplicate = act("Duplicate", w.duplicate, "Ctrl+D", edit, "duplicate")
        self.delete = act("Delete", w.delete, QKeySequence.StandardKey.Delete, edit, "delete")
        self.unwrap = act("Unwrap operation", w.unwrap, "Ctrl+Shift+U", edit)
        self.make = act(
            "Make component from selection…", w.make_component, "Ctrl+K", edit, "make_component"
        )
        self.unpack = act("Unpack component", w.unpack, "Ctrl+Shift+K", edit, "unpack")
        edit.addSeparator()
        self.move_by = act("Move by…", w.move_by, "Ctrl+Shift+M", edit, "move")
        self.rotate_left = act(
            "Rotate 90° left", lambda: w.rotate_selection(90), "Ctrl+R", edit, "rotate_left"
        )
        self.rotate_right = act(
            "Rotate 90° right",
            lambda: w.rotate_selection(-90),
            "Ctrl+Shift+R",
            edit,
            "rotate_right",
        )
        self.mirror_h = act(
            "Mirror left-right", lambda: w.mirror_selection(True), None, edit, "mirror_h"
        )
        self.mirror_v = act(
            "Mirror up-down", lambda: w.mirror_selection(False), None, edit, "mirror_v"
        )
        edit.addSeparator()
        act("Align…", w.start_align, "Ctrl+L", edit, "align")
        self.remove_alignment = act("Remove alignment", w.remove_alignment, None, edit)
        act("Cancel", w.escape, "Esc", edit)

        tools_menu = self.tools_menu = QMenu("&Tools", w)
        self.tools: dict[str, QAction] = {}
        group = QActionGroup(w)
        group.setExclusive(True)
        first_drawing = next(name for name, tool in w.tools.items() if tool.draws)
        for name, tool in w.tools.items():
            if name == first_drawing:
                tools_menu.addSeparator()
            action = act(
                tool.label,
                lambda _=False, n=name: w.set_tool(n),
                tool.shortcut,
                tools_menu,
                tool.icon,
            )
            action.setCheckable(True)
            group.addAction(action)
            self.tools[name] = action
        tools_menu.addSeparator()
        act("Clear rulers", w.clear_rulers, None, tools_menu, "clear")

        insert = self.insert = QMenu("&Insert", w)
        self.add = insert.addMenu("Add")
        icons.bind(self.add.menuAction(), "rect")
        for kind, label in PRIMITIVES:
            # Drawn shapes start their drawing tool; an arc is added as a default one.
            slot = (
                (lambda _=False, k=kind: w.set_tool(k))
                if kind in w.tools
                else (lambda _=False, k=kind: w.add_primitive(k))
            )
            act(label, slot, menu=self.add, icon=kind)
        self.place = insert.addMenu("Place component")
        icons.bind(self.place.menuAction(), "place")
        self.place.aboutToShow.connect(self._fill_place_menu)

        operations = self.operations_menu = QMenu("&Operations", w)
        combine = self.combine = operations.addMenu("Combine")
        icons.bind(combine.menuAction(), "subtract")
        self.operations: dict[str, QAction] = {}
        for op, label in OPERATIONS:
            menu = combine if op in BOOLEANS else operations
            self.operations[op] = act(label, lambda _=False, o=op: w.wrap(o), menu=menu, icon=op)
        operations.addSeparator()
        operations.addAction(self.make)
        operations.addAction(self.unpack)

        view = self.view = QMenu("&View", w)
        act("Fit", lambda: w.canvas.fit(), "F", view, "fit")
        act("Zoom in", lambda: w.canvas.zoom_by(1.25), "Ctrl+=", view, "zoom_in")
        act("Zoom out", lambda: w.canvas.zoom_by(0.8), "Ctrl+-", view, "zoom_out")
        act("Recompile and check", w.refresh, "F5", view, "recompile")
        view.addSeparator()
        overlays = view.addMenu("Overlays")
        icons.bind(overlays.menuAction(), "eye")
        self.settings_toggles: dict[str, QAction] = {}
        for key, label, icon_name in OVERLAYS:
            action = act(label, lambda checked, k=key: w.settings.set(k, checked), menu=overlays)
            icons.bind(action, icon_name)
            action.setCheckable(True)
            action.setChecked(w.settings.get(key))
            self.settings_toggles[key] = action
        self.dark = act("Dark canvas", w._toggle_dark, None, view)
        self.dark.setCheckable(True)
        self.dark.setChecked(w.canvas_theme == "dark")
        implementation = act(
            "Show implementation of read-only components",
            lambda checked: w.settings.set("editor/show_implementation", checked),
            None,
            view,
            "shapes",
        )
        implementation.setCheckable(True)
        implementation.setChecked(w.settings.get("editor/show_implementation"))
        self.settings_toggles["editor/show_implementation"] = implementation
        view.addSeparator()
        act("Open top component", w._edit_top, "Ctrl+T", view, "top")
        act("Process", w.open_process, None, view, "layers")
        self.split = act("Split view", w.split_view, "Ctrl+\\", view, "split")
        act("Merge split view", w.area.unsplit, None, view)
        act("Close tab", w.close_tab, QKeySequence.StandardKey.Close, view, "close")
        self.panels = view.addMenu("Panels")

        help_menu = self.help = QMenu("&Help", w)
        self.find = act("Find action…", w.find_action, "Ctrl+Shift+A", help_menu, "search")
        act("Keyboard shortcuts", lambda: w.show_settings("Keymap"), None, help_menu)

        self.root_menus = [file, edit, tools_menu, insert, operations, view, help_menu]
        self.root = QMenu(w)  # the ☰ button's menu
        for menu in self.root_menus:
            self.root.addMenu(menu)

    def all_actions(self) -> list[QAction]:
        """Every command in the menus, once each (to register their shortcuts on the window)."""
        found: dict[int, QAction] = {}

        def visit(menu: QMenu) -> None:
            for action in menu.actions():
                if action.menu() is not None:
                    visit(action.menu())
                elif not action.isSeparator():
                    found.setdefault(id(action), action)

        visit(self.root)
        return list(found.values())

    def _fill_place_menu(self) -> None:
        w = self.window
        self.place.clear()
        for name in w.document.component_names():
            if name != w.document.active:
                make_action(w, name, lambda _=False, n=name: w.add_component(n), menu=self.place)

    def context_menu(self, x: float, y: float) -> QMenu:
        """The editor's right-click menu at ``(x, y)`` µm, for the current selection.

        Its entries are copies of the commands (their shortcuts shown but not
        active), enabled only when they apply, so the menu keeps its shape.
        """
        w = self.window
        selection = list(w.selection)
        editable = not w.document.read_only
        menu = QMenu(w)

        def item(target: QMenu, source: QAction, slot=None, enabled: bool = True) -> QAction:
            action = make_action(menu, source.text(), slot or source.trigger, menu=target)
            action.setIcon(source.icon())
            action.setShortcut(source.shortcut())
            action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)  # shown, not active
            action.setEnabled(enabled and editable)
            return action

        menu.addSection("At the cursor")
        add = menu.addMenu(self.add.menuAction().icon(), "Add")
        for source in self.add.actions():
            kind = next(k for k, label in PRIMITIVES if label == source.text())
            slot = (lambda _=False, k=kind: w.start_drawing(k, x, y)) if kind in w.tools else None
            item(add, source, slot)
        place = menu.addMenu(self.place.menuAction().icon(), "Place component")
        for name in w.document.component_names():
            if name != w.document.active:
                action = make_action(menu, name, lambda _=False, n=name: w.add_component(n))
                place.addAction(action)
                action.setEnabled(editable)
        add.setEnabled(editable)
        place.setEnabled(editable)

        menu.addSection(", ".join(_label(w, p) for p in selection[:3]) or "No selection")
        one, some, several = len(selection) == 1, bool(selection), len(selection) > 1
        reference = one and w.document.reference_target(selection[0]) is not None
        combine = menu.addMenu(self.combine.menuAction().icon(), "Combine")
        for op in BOOLEANS:
            item(combine, self.operations[op], enabled=several)
        combine.setEnabled(several and editable)
        for op in ("offset", "fillet", "transform", "layer_map"):
            item(menu, self.operations[op], enabled=some)
        item(menu, self.make, enabled=some)
        item(menu, self.unpack, enabled=reference)
        orient = menu.addMenu(self.rotate_left.icon(), "Rotate && mirror")
        for source in (self.rotate_left, self.rotate_right, self.mirror_h, self.mirror_v):
            item(orient, source, enabled=some)
        orient.setEnabled(some and editable)
        menu.addSeparator()
        item(menu, self.duplicate, enabled=one)
        item(menu, self.delete, enabled=some)
        return menu

    def tab_menu(self, view) -> QMenu:
        """The right-click menu of an editor tab."""
        w = self.window
        area = w.area
        menu = QMenu(w)
        make_action(w, "Close", lambda: area.close_view(view), None, menu, "close")
        make_action(w, "Close others", lambda: area.close_others(view), None, menu)
        make_action(w, "Close all", w._close_all_tabs, None, menu)
        menu.addSeparator()
        make_action(
            w,
            "Open in the other pane" if area.split else "Split right",
            lambda: (area.set_current(view), w.split_view()),
            None,
            menu,
            "split",
        )
        if area.split:
            make_action(w, "Merge panes", area.unsplit, None, menu)
        return menu
