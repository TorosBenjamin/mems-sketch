"""Main window and application entry point (``mems-sketch`` or ``python -m mems_sketch.gui``)."""

from __future__ import annotations

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
    QLabel,
    QMainWindow,
    QMessageBox,
    QToolButton,
    QMenu,
)

from mems_sketch.core.component import Geometry, to_dbu
from mems_sketch.core.shapes import Evaluator, NodePath, placement_of
from mems_sketch.export.base import available_exporters
from mems_sketch.gui.canvas import LayoutCanvas
from mems_sketch.gui.document import VIEW_MODES, DesignDocument
from mems_sketch.gui.panels import LayersPanel, MessagesPanel, ShapeTree, VariablesPanel
from mems_sketch.gui.properties import PropertyEditor

FILE_FILTER = "MEMS designs (*.mems)"
PRIMITIVES = [
    ("rect", "Rectangle"),
    ("circle", "Circle"),
    ("arc", "Arc / ring"),
    ("polygon", "Polygon"),
    ("path", "Path"),
]
OPERATIONS = [
    ("group", "Group"),
    ("union", "Union"),
    ("subtract", "Subtract"),
    ("intersect", "Intersect"),
    ("xor", "XOR"),
    ("offset", "Offset"),
    ("fillet", "Fillet"),
    ("layer_map", "Layer map"),
]


class MainWindow(QMainWindow):
    def __init__(self, document: DesignDocument | None = None) -> None:
        super().__init__()
        self.document = document or DesignDocument()
        self.view_mode = "drawn"
        self.selection: list[NodePath] = []
        self._shape_regions: list[tuple[NodePath, kdb.Region]] = []
        self._violations = []
        self._errors: list[str] = []

        self.canvas = LayoutCanvas()
        self.setCentralWidget(self.canvas)
        self.tree = ShapeTree(self.document)
        self.properties = PropertyEditor(self.document)
        self.variables = VariablesPanel(self.document)
        self.layers = LayersPanel(self.document)
        self.messages = MessagesPanel()
        left = Qt.DockWidgetArea.LeftDockWidgetArea
        right = Qt.DockWidgetArea.RightDockWidgetArea
        shapes_dock = self._dock("Shapes", self.tree, left)
        layers_dock = self._dock("Layers", self.layers, left)
        properties_dock = self._dock("Properties", self.properties, right)
        variables_dock = self._dock("Variables", self.variables, right)
        messages_dock = self._dock(
            "Messages", self.messages, Qt.DockWidgetArea.BottomDockWidgetArea
        )
        vertical, horizontal = Qt.Orientation.Vertical, Qt.Orientation.Horizontal
        self.resizeDocks([shapes_dock, layers_dock], [520, 220], vertical)
        self.resizeDocks([properties_dock, variables_dock], [560, 200], vertical)
        self.resizeDocks([shapes_dock, properties_dock], [300, 340], horizontal)
        self.resizeDocks([messages_dock], [110], vertical)

        self.coordinates = QLabel()
        self.statusBar().addPermanentWidget(self.coordinates)
        self._build_actions()

        self.document.changed.connect(self.refresh)
        self.document.file_changed.connect(self._update_title)
        self.tree.selection_changed_paths.connect(self._tree_selected)
        self.tree.enabled_toggled.connect(
            lambda p, e: self._run(lambda: self.document.set_enabled(p, e))
        )
        self.canvas.clicked.connect(self._canvas_clicked)
        self.canvas.cursor_moved.connect(
            lambda x, y: self.coordinates.setText(f"x {x:.3f} µm   y {y:.3f} µm")
        )
        self.layers.visibility_changed.connect(self.canvas.set_layer_visible)
        self.messages.zoom_requested.connect(self._zoom_to_bbox)
        for panel in (self.properties, self.variables, self.layers):
            panel.error.connect(self.report_error)

        self.resize(1400, 900)
        self.refresh()
        self._update_title()

    # -- construction ------------------------------------------------------

    def _dock(self, title: str, widget, area) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(title)
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
        return dock

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
        self._action("New", self.new_file, QKeySequence.StandardKey.New, file)
        self._action("Open…", self.open_file, QKeySequence.StandardKey.Open, file)
        self._action("Save", self.save_file, QKeySequence.StandardKey.Save, file)
        self._action("Save as…", self.save_file_as, QKeySequence.StandardKey.SaveAs, file)
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
        self._action("Fit", self.canvas.fit, "F", view)
        self._action("Run rule check", self.refresh, "F5", view)

        tools = self.addToolBar("Main")
        tools.setObjectName("main-toolbar")
        for text, slot in (
            ("New", self.new_file),
            ("Open", self.open_file),
            ("Save", self.save_file),
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
        for op in ("group", "union", "subtract", "intersect", "offset", "fillet"):
            tools.addAction(dict(OPERATIONS)[op], lambda o=op: self.wrap(o))
        tools.addSeparator()
        tools.addWidget(QLabel(" View: "))
        self.mode_box = QComboBox()
        for mode, label in VIEW_MODES.items():
            self.mode_box.addItem(label, mode)
        self.mode_box.currentIndexChanged.connect(self._mode_changed)
        tools.addWidget(self.mode_box)
        tools.addAction("Fit", self.canvas.fit)

    def _fill_component_menu(self) -> None:
        self.component_menu.clear()
        for name in self.document.component_names():
            self._action(
                name, lambda _=False, n=name: self.add_component(n), menu=self.component_menu
            )

    # -- refresh -----------------------------------------------------------

    def refresh(self) -> None:
        """Re-render the design and update every view."""
        self._errors = []
        try:
            geometry = self.document.geometry(self.view_mode)
        except Exception as exc:  # noqa: BLE001 - shown in the messages panel
            geometry = Geometry()
            self._errors.append(str(exc))
        try:
            self._violations = self.document.check()
        except Exception:  # noqa: BLE001 - render error already reported
            self._violations = []
        self._shape_regions = self._top_level_regions()
        self.layers.refresh()
        self.variables.refresh()
        self.canvas.show_geometry(geometry, self.layers.colors, self.layers.visible)
        self.tree.rebuild([p for p in self.selection if self._exists(p)])
        self.messages.show_messages(self._errors, self._violations)
        self.undo_action.setEnabled(self.document.can_undo())
        self.redo_action.setEnabled(self.document.can_redo())
        self.undo_action.setText(f"Undo {self.document.undo_text()}".strip())
        self.redo_action.setText(f"Redo {self.document.redo_text()}".strip())

    def _top_level_regions(self) -> list[tuple[NodePath, kdb.Region]]:
        """Merged geometry of each top-level shape, for click selection."""
        design = self.document.design
        try:
            variables = design.resolved_variables()
        except Exception:  # noqa: BLE001
            return []
        result = []
        for index, shape in enumerate(design.shapes):
            if not shape.enabled:
                continue
            try:
                geometry = design.render_shape(shape, variables)
            except Exception:  # noqa: BLE001
                continue
            region = kdb.Region()
            for layer, r in geometry.layers.items():
                if self.layers.visible.get(layer, True):
                    region.insert(r)
            result.append((((0, index),), region))
        return result

    def _highlight(self) -> Geometry | None:
        design = self.document.design
        highlight = Geometry()
        try:
            variables = design.resolved_variables()
            for path in self.selection:
                node = self.document.node(path)
                geometry = Evaluator(design.component).render_shape(
                    node, {**variables, "i": 0.0, "j": 0.0}
                )
                highlight.merge(geometry, placement_of(design.shapes, path, variables))
        except Exception:  # noqa: BLE001 - nothing to highlight
            return None
        return highlight.merged()

    def _update_overlay(self) -> None:
        markers = [v.bbox_um for v in self._violations if v.bbox_um]
        self.canvas.show_overlay(self._highlight(), markers)

    def _exists(self, path: NodePath) -> bool:
        try:
            self.document.node(path)
            return True
        except KeyError:
            return False

    def _update_title(self) -> None:
        name = self.document.path.name if self.document.path else "Untitled"
        self.setWindowTitle(f"{'*' if self.document.dirty else ''}{name} — MEMS Sketch")

    # -- selection ---------------------------------------------------------

    def _tree_selected(self, paths: list[NodePath]) -> None:
        self.selection = paths
        self.properties.show_node(paths[0] if len(paths) == 1 else None)
        self._update_overlay()

    def _canvas_clicked(self, x: float, y: float, additive: bool) -> None:
        point = kdb.Point(to_dbu(x), to_dbu(y))
        probe = kdb.Region(kdb.Box(point.x - 1, point.y - 1, point.x + 1, point.y + 1))
        hit = next(
            (p for p, region in reversed(self._shape_regions) if not (region & probe).is_empty()),
            None,
        )
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
        self.view_mode = self.mode_box.currentData()
        self.refresh()

    # -- commands ----------------------------------------------------------

    def report_error(self, message: str) -> None:
        self.statusBar().showMessage(message, 8000)
        self.messages.show_messages([message, *self._errors], self._violations)

    def _run(self, action) -> tuple[bool, object]:
        """Run a command; report failures to the user. Returns (succeeded, result)."""
        try:
            return True, action()
        except Exception as exc:  # noqa: BLE001 - reported to the user
            self.report_error(str(exc))
            return False, None

    def add_primitive(self, kind: str) -> None:
        ok, path = self._run(lambda: self.document.add_primitive(kind))
        if ok:
            self.tree.select_paths([path])

    def add_component(self, name: str) -> None:
        ok, path = self._run(lambda: self.document.add_component(name))
        if ok:
            self.tree.select_paths([path])

    def wrap(self, operation: str) -> None:
        ok, path = self._run(lambda: self.document.wrap(self.selection, operation))
        if ok:
            self.tree.select_paths([path])

    def unwrap(self) -> None:
        if len(self.selection) == 1:
            self._run(lambda: self.document.unwrap(self.selection[0]))

    def duplicate(self) -> None:
        if len(self.selection) == 1:
            ok, path = self._run(lambda: self.document.duplicate(self.selection[0]))
            if ok:
                self.tree.select_paths([path])

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
            "Save changes to the current design first?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save_file()
        return answer == QMessageBox.StandardButton.Discard

    def new_file(self) -> None:
        if self._confirm_discard():
            self.selection = []
            self.document.new()

    def open_file(self, path: str | None = None) -> None:
        if not self._confirm_discard():
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, "Open design", self._last_dir(), FILE_FILTER
            )
        if path:
            self.selection = []
            if self._run(lambda: self.document.open(path))[0]:
                self._remember_dir(path)
                self.canvas.fit()

    def save_file(self) -> bool:
        if self.document.path is None:
            return self.save_file_as()
        return self._run(self.document.save)[0]

    def save_file_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, "Save design", self._last_dir(), FILE_FILTER)
        if not path:
            return False
        if not path.endswith(".mems"):
            path += ".mems"
        self._remember_dir(path)
        return self._run(lambda: self.document.save(path))[0]

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
            event.accept()
        else:
            event.ignore()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("MEMS Sketch")
    window = MainWindow()
    if len(argv) > 1:
        window.open_file(argv[1])
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
