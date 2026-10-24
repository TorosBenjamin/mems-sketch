"""Every command of the main window as a ``QAction``, and the menus built from them.

A command is defined once here and can then be put in any menu, the toolbar
or the editor's right-click menu. The actions call the window's methods;
this module holds no editing logic of its own.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QCursor, QKeySequence
from PySide6.QtWidgets import QMenu

from mems_sketch.core.shapes import RefShape
from mems_sketch.gui import icons, theme

if TYPE_CHECKING:
    from mems_sketch.gui.app import MainWindow

PRIMITIVES = [
    ("rect", "Rectangle"),
    ("circle", "Circle"),
    ("arc", "Arc / ring"),
    ("polygon", "Polygon"),
    ("path", "Path"),
    ("route", "Route"),
    ("guide", "Guide"),
]
OVERLAYS = [  # View › Overlays: setting, label, icon
    ("canvas/show_grid", "Grid", "grid"),
    ("canvas/show_axes", "Coloured axes", "axes"),
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
# The ☰ menu's entries' icons: File, Edit, Tools, Insert, Operations, View, Help.
ROOT_ICONS = ("folder", "edit", "select", "add", "subtract", "eye", "help")


def make_action(
    parent, text: str, slot, shortcut=None, menu: QMenu | None = None, icon: str | None = None
) -> QAction:
    """An action calling ``slot``, with its shortcut in the tooltip, added to ``menu``."""
    action = QAction(text, parent)
    # only a checkable action passes its state on: Qt's ``checked`` would land in
    # a slot's optional first argument (``search(scope)``) otherwise
    action.triggered.connect(lambda checked: slot(checked) if action.isCheckable() else slot())
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
        act("Close project", w.close_project, None, file)
        file.addSeparator()
        act("Import…", lambda: w.components.import_gds(), "Ctrl+I", file, "import")
        act("Export…", w.export_file, "Ctrl+E", file, "export")
        file.addSeparator()
        self.settings = act("Settings…", w.show_settings, "Ctrl+Alt+S", file, "settings")
        file.addSeparator()
        act("Quit", w.close, QKeySequence.StandardKey.Quit, file)

        edit = self.edit = QMenu("&Edit", w)
        self.undo = act("Undo", w.undo, QKeySequence.StandardKey.Undo, edit, "undo")
        self.redo = act("Redo", w.redo, QKeySequence.StandardKey.Redo, edit, "redo")
        edit.addSeparator()
        self.cut = act("Cut", w.cut, QKeySequence.StandardKey.Cut, edit, "cut")
        self.copy = act("Copy", w.copy, QKeySequence.StandardKey.Copy, edit, "copy")
        self.paste = act("Paste", w.paste, QKeySequence.StandardKey.Paste, edit, "paste")
        self.duplicate = act("Duplicate", w.duplicate, "Ctrl+D", edit, "duplicate")
        self.delete = act("Delete", w.delete, QKeySequence.StandardKey.Delete, edit, "delete")
        self.select_all = act(
            "Select all", w.select_all, QKeySequence.StandardKey.SelectAll, edit, "select"
        )
        self.rename = act("Rename…", w.rename_selection, "F2", edit, "edit")
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
        # Shown greyed out where they cannot apply; their keys work again once it
        # closes, and say why (#253).
        edit.aboutToShow.connect(self.show_what_applies)
        edit.aboutToHide.connect(lambda: self.show_what_applies(everything=True))
        act("Cancel", w.escape, "Esc", edit)

        tools_menu = self.tools_menu = QMenu("&Tools", w)
        self.tools: dict[str, QAction] = {}
        group = QActionGroup(w)
        group.setExclusive(True)
        first_drawing = next(name for name, tool in w.tools.items() if tool.draws)
        for name, tool in w.tools.items():
            if name == first_drawing:
                tools_menu.addSeparator()
            # Measure starts measuring distances; N (below) measures angles.
            slot = (
                (lambda _=False: w.measure("distance"))
                if name == "measure"
                else (lambda _=False, n=name: w.set_tool(n))
            )
            action = act(
                tool.label,
                slot,
                tool.shortcut or None,
                tools_menu if tool.listed else None,
                tool.icon,
            )
            action.setCheckable(True)
            group.addAction(action)
            self.tools[name] = action
        # The Measure tool in its angle mode, listed right after Measure.
        self.measure_angle = act("Measure angle", lambda: w.measure("angle"), "N", None, "angle")
        entries = tools_menu.actions()
        after = entries.index(self.tools["measure"]) + 1
        tools_menu.insertAction(
            entries[after] if after < len(entries) else None, self.measure_angle
        )
        tools_menu.addSeparator()
        act("Clear rulers", w.clear_rulers, None, tools_menu, "clear")

        insert = self.insert = QMenu("&Insert", w)
        self.add = insert.addMenu("Add")
        icons.bind(self.add.menuAction(), "rect")
        for kind, label in PRIMITIVES:
            # Drawn shapes start their drawing tool, for one shape; an arc is added
            # as a default one.
            slot = (
                (lambda _=False, k=kind: w.draw_once(k))
                if kind in w.tools
                else (lambda _=False, k=kind: w.add_primitive(k))
            )
            act(label, slot, menu=self.add, icon=kind)
        # A popup that searches the components (see mems_sketch.gui.component_picker).
        self.place = act("Place component…", lambda: w.place_popup(), "Shift+A", insert, "place")

        operations = self.operations_menu = QMenu("&Operations", w)
        combine = self.combine = operations.addMenu("Combine")
        icons.bind(combine.menuAction(), "subtract")
        self.operations: dict[str, QAction] = {}
        for op, label in OPERATIONS:
            menu = combine if op in BOOLEANS else operations
            self.operations[op] = act(label, lambda _=False, o=op: w.wrap(o), menu=menu, icon=op)
        self.connect = act("Connect", lambda: w.connect(), menu=operations, icon="connection")
        self.connect.setToolTip("Join the selected shapes' ends (their ports) with a connection")
        operations.addSeparator()
        operations.addAction(self.make)
        operations.addAction(self.unpack)

        view = self.view = QMenu("&View", w)
        self.fit = act("Fit", lambda: w.canvas.fit(animated=True), "F", view, "fit")
        self.zoom_selection = act(
            "Zoom to selection", w.zoom_to_selection, "Shift+F", view, "zoom_in"
        )
        act("Zoom in", lambda: w.canvas.zoom_by(1.25, animated=True), "Ctrl+=", view, "zoom_in")
        act("Zoom out", lambda: w.canvas.zoom_by(0.8, animated=True), "Ctrl+-", view, "zoom_out")
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
        self.dark.setChecked(theme.get(w.canvas_theme).dark)
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
        self.split = act("Split right", w.split_view, "Ctrl+\\", view, "split")
        act("Merge panes", w.area.unsplit, None, view)
        act("Close tab", w.close_tab, QKeySequence.StandardKey.Close, view, "close")
        self.panels = view.addMenu("Panels")

        help_menu = self.help = QMenu("&Help", w)
        self.search = act("Search everywhere…", w.search, None, help_menu, "search")
        self.search.setToolTip("Search everywhere (press Shift twice)")
        self.find = act("Find action…", w.find_action, "Ctrl+Shift+A", help_menu, "search")
        act("Keyboard shortcuts", lambda: w.show_settings("Keymap"), None, help_menu)
        help_menu.addSeparator()
        act("User guide", w.open_user_guide, None, help_menu, "help")
        act("About MEMS Sketch", w.show_about, None, help_menu)

        self.root_menus = [file, edit, tools_menu, insert, operations, view, help_menu]
        self.root = QMenu(w)  # the ☰ button's menu
        for menu, icon_name in zip(self.root_menus, ROOT_ICONS, strict=True):
            self.root.addMenu(menu)
            icons.bind(menu.menuAction(), icon_name)

    def show_what_applies(self, everything: bool = False) -> None:
        """Enable the Edit menu's commands that apply to the selection, and grey out
        the others (``everything``: all of them, for their keys)."""
        w = self.window
        paths = list(w.selection)
        node = w.document.node(paths[0]) if len(paths) == 1 and w._exists(paths[0]) else None
        applies = {
            self.cut: bool(paths),
            self.copy: bool(paths),
            self.duplicate: bool(paths),
            self.delete: bool(paths),
            self.move_by: bool(paths),
            self.rotate_left: bool(paths),
            self.rotate_right: bool(paths),
            self.mirror_h: bool(paths),
            self.mirror_v: bool(paths),
            self.make: bool(paths),
            self.rename: node is not None,
            self.unwrap: node is not None and any(node.child_lists()),
            self.unpack: isinstance(node, RefShape),
            self.remove_alignment: node is not None and node.align is not None,
        }
        for action, on in applies.items():
            action.setEnabled(everything or on)

    def shape_actions(self) -> list[QAction]:
        """The commands on the selected shapes: their shortcuts work in the editor
        and the Shapes panel only, so a key pressed in another panel (Delete in
        the Components panel) never changes the shapes."""
        return [
            self.cut,
            self.copy,
            self.paste,
            self.duplicate,
            self.delete,
            self.select_all,
            self.rename,
            self.unwrap,
            self.make,
            self.unpack,
            self.move_by,
            self.rotate_left,
            self.rotate_right,
        ]

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

    def context_menu(self, x: float, y: float) -> QMenu:
        """The editor's right-click menu at ``(x, y)`` µm, for what is selected.

        On empty space (nothing selected) it offers what can be done there: add
        a shape or place a component at that point, paste, select all, fit. With
        a selection it offers what can be done to it: the operations, rotating
        and mirroring, zooming to it, renaming one shape, and the edit commands.
        Its entries are copies of the commands (their shortcuts shown but not
        active), enabled only when they apply.
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

        if not selection:
            add = menu.addMenu(self.add.menuAction().icon(), "Add")
            for source in self.add.actions():
                kind = next(k for k, label in PRIMITIVES if label == source.text())
                slot = (
                    (lambda _=False, k=kind: w.start_drawing(k, x, y))
                    if kind in w.tools
                    else (lambda _=False, k=kind: w.add_primitive(k, at=(x, y)))
                )
                item(add, source, slot)
            # The Place popup, by the cursor; the component lands where the click was.
            item(menu, self.place, lambda: w.place_popup(at=(x, y), near=QCursor.pos()))
            add.setEnabled(editable)
            menu.addSeparator()
            item(menu, self.paste, enabled=w.can_paste())
            item(menu, self.select_all).setEnabled(True)  # selecting changes nothing
            item(menu, self.fit).setEnabled(True)
            return menu

        self._selection_items(item, menu, selection)
        return menu

    def _selection_items(self, item, menu: QMenu, selection: list) -> None:
        """What can be done to the selected shapes, for a right-click menu (the
        canvas's and the Shapes panel's)."""
        w = self.window
        editable = not w.document.read_only
        one, several = len(selection) == 1, len(selection) > 1
        node = w.document.node(selection[0]) if one and w._exists(selection[0]) else None
        reference = one and w.document.reference_target(selection[0]) is not None
        combine = menu.addMenu(self.combine.menuAction().icon(), "Combine")
        for op in BOOLEANS:
            item(combine, self.operations[op], enabled=several)
        combine.setEnabled(several and editable)
        for op in ("offset", "fillet", "transform", "layer_map"):
            item(menu, self.operations[op])
        item(menu, self.connect, enabled=several)
        item(menu, self.make)
        item(menu, self.unpack, enabled=reference)
        item(menu, self.unwrap, enabled=node is not None and any(node.child_lists()))
        orient = menu.addMenu(self.rotate_left.icon(), "Rotate && mirror")
        for source in (self.rotate_left, self.rotate_right, self.mirror_h, self.mirror_v):
            item(orient, source)
        orient.setEnabled(editable)
        item(menu, self.remove_alignment, enabled=node is not None and node.align is not None)
        menu.addSeparator()
        item(menu, self.zoom_selection).setEnabled(True)
        if one:
            item(menu, self.rename)
        menu.addSeparator()
        self._edit_items(item, menu, True, one)

    def _edit_items(self, item, menu: QMenu, some: bool, one: bool) -> None:
        """Cut, copy, paste, duplicate and delete, for a right-click menu."""
        item(menu, self.cut, enabled=some)
        copy = item(menu, self.copy, enabled=some)
        copy.setEnabled(some)  # copying changes nothing: also in a read-only component
        item(menu, self.paste, enabled=self.window.can_paste())
        item(menu, self.duplicate, enabled=some)  # several: one step (#253)
        item(menu, self.delete, enabled=some)

    def shapes_menu(self) -> QMenu:
        """The Shapes tree's right-click menu, for the current selection."""
        w = self.window
        editable = not w.document.read_only
        menu = QMenu(w)

        def item(target: QMenu, source: QAction, slot=None, enabled: bool = True) -> QAction:
            action = make_action(menu, source.text(), slot or source.trigger, menu=target)
            action.setIcon(source.icon())
            action.setShortcut(source.shortcut())
            action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)  # shown, not active
            action.setEnabled(enabled and editable)
            return action

        selection = list(w.selection)
        if selection:  # as on the canvas: what can be done to them
            self._selection_items(item, menu, selection)
        else:
            self._edit_items(item, menu, False, False)
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
