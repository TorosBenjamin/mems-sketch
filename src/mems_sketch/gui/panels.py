"""Dockable panels: components, shape tree, parameters, points, process and messages."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QMimeData, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.core.component import component_types
from mems_sketch.core.expressions import ExpressionError, resolve_variables
from mems_sketch.core.process import Layer
from mems_sketch.core.shapes import NodePath, RefShape, Shape, child_lists
from mems_sketch.editing import EditSession
from mems_sketch.gui import icons
from mems_sketch.gui.canvas import COMPONENT_MIME

PATH_ROLE = Qt.ItemDataRole.UserRole
SLOT_LABELS = {"boolean": ("A", "B")}


def describe(shape: Shape) -> str:
    """A node's summary with its alignment, if it has one (the shape list's tooltip)."""
    summary = shape.summary()
    if shape.align is not None:
        summary += f" · {alignment(shape)}"
    return summary


def alignment(shape: Shape) -> str:
    return f"{shape.align.point} at {shape.align.to}"


def detail(shape: Shape) -> str:
    """The muted text after a node's name: what it is, briefly."""
    return shape.detail()


def modifier_stack(shape: Shape) -> str:
    """The modifier stack in words, first to last (switched-off ones marked)."""
    return " → ".join(m.summary() + ("" if m.enabled else " (off)") for m in shape.modifiers)


def parse_value(text: str) -> float | str:
    """A number if the text is one, otherwise the text as an expression."""
    text = text.strip()
    try:
        return float(text)
    except ValueError:
        if not text:
            raise ValueError("a value is required") from None
        return text


def _format(value) -> str:
    if value is None:
        return ""
    return f"{value:g}" if isinstance(value, float | int) else str(value)


def _action_bar(title: QLabel | None, *actions: tuple[str, str, object]) -> QHBoxLayout:
    """A tool window's header row: an optional title, then small icon buttons.

    ``actions`` are ``(icon, tooltip, slot)``; the buttons are also returned in
    the layout's ``buttons`` attribute (by tooltip) for tests and shortcuts.
    """
    row = QHBoxLayout()
    row.setContentsMargins(6, 2, 4, 2)
    row.setSpacing(1)
    if title is not None:
        title.setObjectName("muted")
        row.addWidget(title, 1)
    else:
        row.addStretch(1)
    row.buttons = {}
    for name, tip, slot in actions:
        button = QToolButton()
        icons.bind(button, name)
        button.setIconSize(QSize(16, 16))
        button.setToolTip(tip)
        button.setAutoRaise(True)
        button.clicked.connect(slot)
        row.addWidget(button)
        row.buttons[tip] = button
    return row


def swatch_icon(color: QColor) -> QIcon:
    """A small rounded square in a layer's colour."""
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(color.darker(130))
    fill = QColor(color)
    fill.setAlpha(200)
    painter.setBrush(fill)
    painter.drawRoundedRect(3, 3, 18, 18, 4, 4)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def shape_icon(shape: Shape) -> QIcon:
    return icons.icon(shape.icon_name())


def _table(columns: Sequence[str]) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(list(columns))
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setHighlightSections(False)
    table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(26)
    table.setShowGrid(False)
    table.setFrameShape(QTableWidget.Shape.NoFrame)
    return table


def _blank_icon() -> QIcon:
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.GlobalColor.transparent)
    return QIcon(pixmap)


def _readonly(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    item.setForeground(QBrush(QColor("#8c8f99")))
    return item


class _Panel(QWidget):
    error = Signal(str)

    def _guard(self, action) -> bool:
        try:
            action()
            return True
        except Exception as exc:  # noqa: BLE001 - reported to the user
            self.error.emit(str(exc))
            return False


# -- components --------------------------------------------------------------


class ComponentsPanel(_Panel):
    """An explorer of the components: the project's, each library's and the built-ins.

    These are definitions: every component expands to the components it
    uses, and those expand in turn. The placements themselves (each with its
    own name) are in the Shapes list. Double-click
    opens a component in a tab (library and built-in ones read-only); drag one
    onto the canvas, or use Place, to put it into the component being edited.
    Right-click for everything else.
    """

    place_requested = Signal(str)
    open_requested = Signal(str)
    open_aside_requested = Signal(str)  # open in the other pane
    process_requested = Signal()
    collapse_changed = Signal()
    NAME_ROLE = Qt.ItemDataRole.UserRole
    GROUP_ROLE = Qt.ItemDataRole.UserRole + 1  # "project", "library:<name>", "builtin"
    PROCESS_ROLE = Qt.ItemDataRole.UserRole + 2  # the Process item
    PENDING = "…"  # placeholder child: filled in when the item is expanded

    def __init__(self, document: EditSession) -> None:
        super().__init__()
        self.document = document
        self.tree = _ComponentTree()
        self.tree.setHeaderHidden(True)
        self.tree.setItemDelegate(DetailDelegate(self.tree))
        self.tree.itemDoubleClicked.connect(self._activated)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.collapsed: set[str] = set()  # collapsed groups, e.g. "Built-in"
        self.expanded: set[str] = set()  # expanded components, by their path in the tree
        self.tree.itemCollapsed.connect(lambda item: self._set_expanded(item, False))
        self.tree.itemExpanded.connect(lambda item: self._set_expanded(item, True))
        self._refreshing = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.actions = _action_bar(
            None,
            ("add", "New component or library", self._add_menu),
            ("place", "Place the selected component in the edited one", self._place),
            ("collapse", "Collapse all", self._collapse_all),
        )
        self.header_buttons = list(self.actions.buttons.values())  # shown in the dock header
        layout.addWidget(self.tree)

    # -- building ------------------------------------------------------------

    def refresh(self) -> None:
        self._refreshing = True
        try:
            self._fill()
        finally:
            self._refreshing = False

    def _fill(self) -> None:
        project = self.document.project
        scroll = self.tree.verticalScrollBar().value()
        self.tree.clear()
        local = self._group(project.name, "project", "folder", "Project components")
        process = QTreeWidgetItem(local, ["Process"])
        process.setData(0, self.PROCESS_ROLE, True)
        process.setIcon(0, icons.icon("layers"))
        process.setToolTip(0, "The process's layers and constants (opens in a tab)")
        names = sorted(project.components, key=lambda n: n != project.top)  # top first
        for name in names:
            self._component(local, name, name)
        for library in project.libraries.values():
            where = f" — {library.path}" if library.path else ""
            group = self._group(
                library.name, f"library:{library.name}", "library", f"Library{where}"
            )
            for name in library.components:
                self._component(group, f"{library.name}.{name}", name)
        builtins = self._group("Built-in", "builtin", "builtin", "Built-in components")
        for name in component_types():
            self._component(builtins, name, name)
        self.tree.verticalScrollBar().setValue(scroll)

    def _group(self, title: str, key: str, icon: str, tip: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(self.tree, [title])
        item.setIcon(0, icons.icon(icon))
        item.setData(0, self.GROUP_ROLE, key)
        item.setToolTip(0, tip)
        font = QFont()
        font.setBold(True)
        item.setFont(0, font)
        item.setExpanded(self._state_key(item) not in self.collapsed)
        return item

    def _component(self, parent: QTreeWidgetItem, name: str, label: str):
        project = self.document.project
        item = QTreeWidgetItem(parent, [label])
        item.setData(0, self.NAME_ROLE, name)
        item.setIcon(0, icons.icon(component_icon(project, name)))
        if name == project.top and name != "top":  # the star shows it too
            item.setData(0, DETAIL_ROLE, "top")
        item.setToolTip(0, self._tooltip(name))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsDragEnabled)
        if parent.parent() is None and name == self.document.active:  # the tab being edited
            font = QFont()
            font.setBold(True)
            item.setFont(0, font)
            item.setIcon(0, icons.icon("eye" if self.document.read_only else "edit", "blue"))
        if self.document.components.placed(name):
            QTreeWidgetItem(item, [self.PENDING])  # filled in when expanded
            if self._path_key(item) in self.expanded or (
                parent.parent() is None and name == project.top and not self.expanded
            ):
                item.setExpanded(True)
        return item

    def _tooltip(self, name: str) -> str:
        project = self.document.project
        if "." in name:
            kind = "library component, read-only: copy it into the project to edit it"
        elif name in project.components:
            kind = "top component" if name == project.top else "project component"
        else:
            kind = "built-in component, read-only"
        users = self.document.components.users(name)
        placed = f"\nplaced in {', '.join(users)}" if users else ""
        return f"{name} — {kind}{placed}"

    def _fill_children(self, item: QTreeWidgetItem) -> None:
        if item.childCount() == 1 and item.child(0).text(0) == self.PENDING:
            item.takeChild(0)
            for child, _count in self.document.components.placed(item.data(0, self.NAME_ROLE)):
                self._component(item, child, child)

    # -- expanded state --------------------------------------------------------

    def _state_key(self, item: QTreeWidgetItem) -> str:
        """Groups keep the keys the editor state has always used."""
        key = item.data(0, self.GROUP_ROLE)
        if key == "project":
            return "project"
        if key == "builtin":
            return "Built-in"
        return f"Library: {key.partition(':')[2]}"

    def _path_key(self, item: QTreeWidgetItem) -> str:
        names = []
        while item is not None and item.data(0, self.GROUP_ROLE) is None:
            names.append(item.data(0, self.NAME_ROLE))
            item = item.parent()
        group = item.data(0, self.GROUP_ROLE) if item is not None else ""
        return "/".join([group, *reversed(names)])

    def _set_expanded(self, item: QTreeWidgetItem, expanded: bool) -> None:
        if expanded:
            self._fill_children(item)
        if self._refreshing:
            return
        if item.data(0, self.GROUP_ROLE) is not None:
            key = self._state_key(item)
            (self.collapsed.discard if expanded else self.collapsed.add)(key)
        else:
            key = self._path_key(item)
            (self.expanded.add if expanded else self.expanded.discard)(key)
        self.collapse_changed.emit()

    def _collapse_all(self) -> None:
        self.expanded.clear()
        self.tree.collapseAll()
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setExpanded(True)
        self.collapse_changed.emit()

    # -- actions -----------------------------------------------------------------

    def selected(self) -> str | None:
        items = self.tree.selectedItems()
        return items[0].data(0, self.NAME_ROLE) if items else None

    def _is_local(self, name: str | None) -> bool:
        return name is not None and name in self.document.project.components

    def _activated(self, item: QTreeWidgetItem) -> None:
        if item.data(0, self.PROCESS_ROLE):
            self.process_requested.emit()
            return
        name = item.data(0, self.NAME_ROLE)
        if name is not None:
            self.open_requested.emit(name)

    def _add_menu(self) -> None:
        menu = QMenu(self)
        _menu_action(menu, "New component…", self._new, "add")
        _menu_action(menu, "Add library…", self.add_library, "library")
        button = self.actions.buttons["New component or library"]
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _context_menu(self, position) -> None:
        menu = self.menu_for(self.tree.itemAt(position))
        if not menu.isEmpty():
            menu.exec(self.tree.viewport().mapToGlobal(position))

    def menu_for(self, item: QTreeWidgetItem | None) -> QMenu:
        """The right-click menu for an item of the explorer (or its empty space)."""
        menu = QMenu(self)
        if item is None:
            self._project_actions(menu)
        elif item.data(0, self.PROCESS_ROLE):
            _menu_action(menu, "Open the process", self.process_requested.emit, "layers")
        elif item.data(0, self.GROUP_ROLE) is not None:
            group = item.data(0, self.GROUP_ROLE)
            if group == "project":
                self._project_actions(menu)
            elif group.startswith("library:"):
                library = group.partition(":")[2]
                _menu_action(
                    menu,
                    f"Remove library {library}",
                    lambda: self._guard(lambda: self.document.components.remove_library(library)),
                    "remove",
                )
        else:
            self.tree.setCurrentItem(item)
            self._component_actions(menu, item.data(0, self.NAME_ROLE))
        return menu

    def _project_actions(self, menu) -> None:
        _menu_action(menu, "New component…", self._new, "add")
        _menu_action(menu, "Add library…", self.add_library, "library")
        if self.document.project.top is not None:
            menu.addSeparator()
            _menu_action(menu, "Make the project a library (no top component)", self._no_top)

    def _component_actions(self, menu, name: str) -> None:
        project = self.document.project
        active = self.document.active
        _menu_action(menu, "Open", lambda: self.open_requested.emit(name), "edit")
        _menu_action(
            menu, "Open in the other pane", lambda: self.open_aside_requested.emit(name), "split"
        )
        place = _menu_action(
            menu, f"Place in {active}", lambda: self.place_requested.emit(name), "place"
        )
        place.setEnabled(active in project.components and name != active)
        menu.addSeparator()
        if name in project.components:
            _menu_action(menu, "Rename…", lambda: self._rename(name), "edit")
            _menu_action(
                menu,
                "Duplicate",
                lambda: self._guard(lambda: self.document.components.copy(name)),
                "duplicate",
            )
            _menu_action(menu, "Delete", lambda: self._delete(name), "delete")
            menu.addSeparator()
            if name == project.top:
                _menu_action(menu, "Make the project a library (no top component)", self._no_top)
            else:
                _menu_action(
                    menu,
                    "Set as top component",
                    lambda: self._guard(lambda: self.document.components.set_top(name)),
                    "top",
                )
        elif "." in name:
            _menu_action(
                menu,
                "Copy into the project",
                lambda: self._guard(lambda: self.document.components.copy(name)),
                "duplicate",
            )

    def _new(self) -> None:
        name, ok = QInputDialog.getText(self, "New component", "Component name:")
        if ok and name.strip():
            self._guard(lambda: self.document.components.new(name.strip()))

    def add_library(self, folder: str | None = None) -> None:
        """Load a folder of components (a library or another project) as a library."""
        if folder is None:
            folder = QFileDialog.getExistingDirectory(self, "Add library: choose its folder")
        if folder:
            self._guard(lambda: self.document.components.add_library(folder))

    def _no_top(self) -> None:
        self._guard(lambda: self.document.components.set_top(None))

    def _rename(self, old: str | None = None) -> None:
        old = old or self.selected()
        if not self._is_local(old):
            self.error.emit("select a project component to rename")
            return
        new, ok = QInputDialog.getText(self, "Rename component", "New name:", text=old)
        if ok and new.strip() and new.strip() != old:
            self._guard(lambda: self.document.components.rename(old, new.strip()))

    def _delete(self, name: str | None = None) -> None:
        name = name or self.selected()
        if self._is_local(name):
            self._guard(lambda: self.document.components.delete(name))

    def _set_top(self) -> None:
        name = self.selected()
        if self._is_local(name):
            self._guard(lambda: self.document.components.set_top(name))

    def _place(self) -> None:
        name = self.selected()
        if name is not None:
            self.place_requested.emit(name)


class _ComponentTree(QTreeWidget):
    """The explorer's tree: components can be dragged onto a canvas to place them."""

    def __init__(self) -> None:
        super().__init__()
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)

    def mimeTypes(self) -> list[str]:
        return [COMPONENT_MIME]

    def mimeData(self, items) -> QMimeData:
        data = QMimeData()
        names = [i.data(0, ComponentsPanel.NAME_ROLE) for i in items]
        names = [n for n in names if n]
        if names:
            data.setData(COMPONENT_MIME, names[0].encode())
            data.setText(names[0])
        return data


def component_icon(project, name: str) -> str:
    """Project, library and built-in components have their own icons."""
    if "." in name:
        return "component_library"
    if name in project.components:
        return "top" if name == project.top else "component"
    return "component_builtin"


def _menu_action(menu, text: str, slot, icon: str | None = None):
    action = menu.addAction(text)
    action.triggered.connect(slot)
    if icon is not None:
        action.setIcon(icons.icon(icon))
    return action


DETAIL_ROLE = Qt.ItemDataRole.UserRole + 5  # muted text drawn after an item's name
PLACES_ROLE = Qt.ItemDataRole.UserRole + 6  # a shape row placing a component: its name
INSIDE_ROLE = Qt.ItemDataRole.UserRole + 7  # a row inside a placed component: (owner, path)


class DetailDelegate(QStyledItemDelegate):
    """Draws an item's name, then its detail (``DETAIL_ROLE``) in a muted colour."""

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        detail = index.data(DETAIL_ROLE)
        if not detail:
            return
        style_option = QStyleOptionViewItem(option)
        self.initStyleOption(style_option, index)
        style = style_option.widget.style() if style_option.widget else QApplication.style()
        text_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText, style_option, style_option.widget
        )
        metrics = style_option.fontMetrics
        used = metrics.horizontalAdvance(style_option.text) + 8
        rect = text_rect.adjusted(used, 0, 0, 0)
        if rect.width() <= 10:
            return
        painter.save()
        font = QFont(style_option.font)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(option.palette.placeholderText().color())
        text = QFontMetrics(font).elidedText(detail, Qt.TextElideMode.ElideRight, rect.width())
        painter.drawText(
            rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text
        )
        painter.restore()


# -- shape tree ----------------------------------------------------------------


class ShapeTree(QTreeWidget):
    """The active component's shapes, as an outliner. Emits the selected node paths.

    The component's own shapes can be selected and edited. A placed component
    expands to show what is inside it: greyed and read-only, because it
    belongs to that component's definition and changing it changes every
    copy. Double-clicking such a row opens the component that owns it, with
    that shape selected.
    """

    selection_changed_paths = Signal(list)
    enabled_toggled = Signal(tuple, bool)
    collapse_changed = Signal()

    STATUS = 1  # the narrow column with the alignment icon
    MODIFIERS = 2  # ... and the one with the modifier icon

    def __init__(self, document: EditSession) -> None:
        super().__init__()
        self.document = document
        self.setColumnCount(3)
        self.setHeaderHidden(True)
        self.setItemDelegateForColumn(0, DetailDelegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        header = self.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (self.STATUS, self.MODIFIERS):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.setColumnWidth(column, 24)
        self.itemSelectionChanged.connect(self._emit_selection)
        self.itemChanged.connect(self._item_changed)
        self._rebuilding = False
        # Per component: collapsed operations (others are open) and opened
        # placed components (others are closed).
        self.collapsed: dict[str, set[NodePath]] = {}
        self.opened: dict[str, set[NodePath]] = {}
        self.itemCollapsed.connect(lambda item: self._set_collapsed(item, True))
        self.itemExpanded.connect(self._expanded)

    def _expanded(self, item: QTreeWidgetItem) -> None:
        self._fill_placed(item)
        self._set_collapsed(item, False)

    def _set_collapsed(self, item: QTreeWidgetItem, collapsed: bool) -> None:
        path = item.data(0, PATH_ROLE)
        if self._rebuilding or path is None:
            return
        if item.data(0, PLACES_ROLE) is not None:
            paths = self.opened.setdefault(self.document.active, set())
            (paths.discard if collapsed else paths.add)(path)
        else:
            paths = self.collapsed.setdefault(self.document.active, set())
            (paths.add if collapsed else paths.discard)(path)
        self.collapse_changed.emit()

    def rebuild(self, keep: list[NodePath] | None = None) -> None:
        keep = self.selected_paths() if keep is None else keep
        self._rebuilding = True
        self.clear()
        for index, shape in enumerate(self.document.shapes):
            self._add(self.invisibleRootItem(), shape, ((0, index),))
        collapsed = self.collapsed.get(self.document.active, set())
        opened = self.opened.get(self.document.active, set())
        pending = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        while pending:  # the component's own nodes only: placed contents load when opened
            item = pending.pop()
            path = item.data(0, PATH_ROLE)
            if item.data(0, PLACES_ROLE) is not None:
                item.setExpanded(path in opened)
            elif item.childCount():
                item.setExpanded(path not in collapsed)
            if item.data(0, INSIDE_ROLE) is None:
                pending.extend(item.child(i) for i in range(item.childCount()))
        self._rebuilding = False
        self.select_paths(keep)

    def _add(self, parent: QTreeWidgetItem, shape: Shape, path: NodePath) -> None:
        item = QTreeWidgetItem(parent, [shape.name or f"({shape.kind})"])
        item.setData(0, PATH_ROLE, path)
        item.setData(0, DETAIL_ROLE, detail(shape))
        item.setToolTip(0, describe(shape))
        item.setIcon(0, shape_icon(shape))
        if shape.align is not None:
            item.setIcon(self.STATUS, icons.icon("link"))
            item.setToolTip(self.STATUS, f"Aligned: {alignment(shape)}")
        if shape.modifiers:
            first = shape.modifiers[0]
            glyph = type(first).icon if len(shape.modifiers) == 1 else "modifier"
            item.setIcon(self.MODIFIERS, icons.icon(glyph))
            item.setToolTip(self.MODIFIERS, f"Modifiers: {modifier_stack(shape)}")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Checked if shape.enabled else Qt.CheckState.Unchecked)
        if not shape.enabled:
            item.setForeground(0, QBrush(QColor("#8c8f99")))
        self._placeholder(item, shape, None)
        labels = SLOT_LABELS.get(shape.kind)
        for slot, children in enumerate(child_lists(shape)):
            holder = item
            if labels:
                holder = QTreeWidgetItem(item, [labels[slot]])
                holder.setData(0, DETAIL_ROLE, "operand")
                holder.setFlags(Qt.ItemFlag.ItemIsEnabled)
            for index, child in enumerate(children):
                self._add(holder, child, (*path, (slot, index)))

    # -- what is inside placed components (read-only) -------------------------

    def _placeholder(self, item: QTreeWidgetItem, shape: Shape, namespace: str | None) -> None:
        """Let a placed component's row expand into its shapes (loaded when opened)."""
        if not isinstance(shape, RefShape):
            return
        project = self.document.project
        try:
            target = project.qualify(shape.component, namespace)
        except KeyError:
            return
        found = project.definition(target)
        if found is None or not found[0].shapes:
            return  # a built-in (or empty) component has nothing to show
        item.setData(0, PLACES_ROLE, target)
        QTreeWidgetItem(item, ["…"])

    def _fill_placed(self, item: QTreeWidgetItem) -> None:
        target = item.data(0, PLACES_ROLE)
        if target is None or item.childCount() != 1 or item.child(0).data(0, INSIDE_ROLE):
            return
        item.takeChild(0)
        found = self.document.project.definition(target)
        if found is None:
            return
        definition, namespace = found
        for index, shape in enumerate(definition.shapes):
            self._add_inside(item, shape, target, namespace, ((0, index),))

    def _add_inside(self, parent, shape: Shape, owner: str, namespace, path: NodePath) -> None:
        item = QTreeWidgetItem(parent, [shape.name or f"({shape.kind})"])
        item.setData(0, INSIDE_ROLE, (owner, path))
        item.setData(0, DETAIL_ROLE, detail(shape))
        item.setIcon(0, shape_icon(shape))
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # not selectable: it belongs to ``owner``
        item.setForeground(0, QBrush(QColor("#8c8f99")))
        font = QFont()
        font.setItalic(True)
        item.setFont(0, font)
        item.setToolTip(
            0,
            f"{describe(shape)}\nPart of {owner}: double-click to edit it there "
            "(changes every copy)",
        )
        if shape.align is not None:
            item.setIcon(self.STATUS, icons.icon("link"))
            item.setToolTip(self.STATUS, f"Aligned: {alignment(shape)}")
        if shape.modifiers:
            first = shape.modifiers[0]
            glyph = type(first).icon if len(shape.modifiers) == 1 else "modifier"
            item.setIcon(self.MODIFIERS, icons.icon(glyph))
            item.setToolTip(self.MODIFIERS, f"Modifiers: {modifier_stack(shape)}")
        self._placeholder(item, shape, namespace)
        labels = SLOT_LABELS.get(shape.kind)
        for slot, children in enumerate(child_lists(shape)):
            holder = item
            if labels:
                holder = QTreeWidgetItem(item, [labels[slot]])
                holder.setData(0, DETAIL_ROLE, "operand")
                holder.setData(0, INSIDE_ROLE, (owner, path))
                holder.setFlags(Qt.ItemFlag.ItemIsEnabled)
            for index, child in enumerate(children):
                self._add_inside(holder, child, owner, namespace, (*path, (slot, index)))
            holder.setExpanded(True)

    def selected_paths(self) -> list[NodePath]:
        return [p for item in self.selectedItems() if (p := item.data(0, PATH_ROLE)) is not None]

    def select_paths(self, paths: list[NodePath]) -> None:
        wanted = set(paths)
        self.blockSignals(True)
        self.clearSelection()
        pending = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        while pending:
            item = pending.pop()
            if item.data(0, PATH_ROLE) in wanted:
                item.setSelected(True)
                self.scrollToItem(item)
            pending.extend(item.child(i) for i in range(item.childCount()))
        self.blockSignals(False)
        self._emit_selection()

    def _emit_selection(self) -> None:
        self.selection_changed_paths.emit(self.selected_paths())

    def _item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        path = item.data(0, PATH_ROLE)
        if self._rebuilding or column != 0 or path is None:
            return
        enabled = item.checkState(0) == Qt.CheckState.Checked
        if enabled != self.document.node(path).enabled:
            self.enabled_toggled.emit(path, enabled)


# -- parameters ----------------------------------------------------------------


class ParametersPanel(_Panel):
    """Parameters of the component in the current tab: default, limits, trial and value.

    A trial value overrides the default for viewing only; it is not saved.
    Library and built-in components are read-only, but trial values work.
    A lock marks an internal parameter: one only the component itself uses,
    not offered where it is placed.
    """

    COLUMNS = ("Name", "Default", "Min", "Max", "Trial", "Value")
    TRIAL = 4

    def __init__(self, document: EditSession) -> None:
        super().__init__()
        self.document = document
        self.title = QLabel()
        self.table = _table(self.COLUMNS)
        self.table.itemChanged.connect(self._changed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.actions = _action_bar(
            self.title,
            ("add", "Add parameter", lambda: self._guard(self.document.parameters.add)),
            ("remove", "Remove the selected parameters", self._remove),
            ("lock", "Make the selected parameters internal (or public)", self._toggle_internal),
            ("clear", "Clear trial values", self._clear_trials),
        )
        layout.addLayout(self.actions)
        layout.addWidget(self.table)
        self._names: list[str] = []

    def _clear_trials(self) -> None:
        for name in list(self.document.trials.get(self.document.active, {})):
            self._guard(lambda n=name: self.document.set_trial(n, None))

    def refresh(self) -> None:
        parameters = self.document.active_definition.parameters
        read_only = self.document.read_only
        suffix = " (read-only; trial values work)" if read_only else ""
        self.title.setText(f"Parameters of <b>{self.document.active}</b>{suffix}")
        try:
            values = self.document.results.scope()
        except Exception:  # noqa: BLE001 - shown as "error" per row
            values = {}
        trials = self.document.trials.get(self.document.active, {})
        self.table.blockSignals(True)
        self.table.setRowCount(len(parameters))
        self._names = [p.name for p in parameters]
        for row, p in enumerate(parameters):
            value = values.get(p.name)
            make = _readonly if read_only else QTableWidgetItem
            cells = [
                make(p.name),
                make(_format(p.default)),
                make(_format(p.min)),
                make(_format(p.max)),
                QTableWidgetItem(_format(trials.get(p.name))),
                _readonly("error" if value is None else f"{value:g}"),
            ]
            cells[0].setIcon(icons.icon("lock") if p.internal else _blank_icon())  # aligned
            access = (
                "Internal: only this component uses it"
                if p.internal
                else "Public: can be set where the component is placed"
            )
            cells[0].setToolTip("\n".join(t for t in (p.description, access) if t))
            cells[self.TRIAL].setToolTip("Try a value without changing the design (not saved)")
            if p.name in trials:
                cells[-1].setForeground(QBrush(QColor("#e0a000")))
            for column, cell in enumerate(cells):
                self.table.setItem(row, column, cell)
        self.table.blockSignals(False)

    def _changed(self, item: QTableWidgetItem) -> None:
        name = self._names[item.row()]
        text = item.text().strip()

        def apply() -> None:
            match item.column():
                case 0:
                    if text != name:
                        self.document.parameters.update(name, name=text)
                case 1:
                    self.document.parameters.update(name, default=parse_value(text))
                case 2:
                    self.document.parameters.update(name, min=float(text) if text else None)
                case 3:
                    self.document.parameters.update(name, max=float(text) if text else None)
                case self.TRIAL:
                    self.document.set_trial(name, parse_value(text) if text else None)

        if not self._guard(apply):
            self.refresh()

    def _toggle_internal(self) -> None:
        """Make the selected parameters internal, or public if they all are already."""
        rows = sorted({i.row() for i in self.table.selectedItems()})
        if not rows:
            self.error.emit("select the parameters to make internal or public")
            return
        definitions = {p.name: p for p in self.document.active_definition.parameters}
        names = [self._names[row] for row in rows]
        internal = not all(definitions[n].internal for n in names)
        self._guard(lambda: self.document.parameters.set_internal(names, internal))

    def _remove(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedItems()}, reverse=True)
        for row in rows:
            self._guard(lambda n=self._names[row]: self.document.parameters.remove(n))


# -- points ------------------------------------------------------------------


class PointsPanel(_Panel):
    """Alignment points the edited component declares, for whoever places it.

    ``At`` is an optional ``shape.point`` the position is measured from; ``X``
    and ``Y`` may be expressions over the component's parameters.
    """

    COLUMNS = ("Name", "At", "X", "Y", "Position")
    FIELDS = ("name", "at", "x", "y")

    def __init__(self, document: EditSession) -> None:
        super().__init__()
        self.document = document
        self.title = QLabel()
        self.table = _table(self.COLUMNS)
        self.table.itemChanged.connect(self._changed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.actions = _action_bar(
            self.title,
            ("add", "Add point", lambda: self._guard(self.document.points.add)),
            ("remove", "Remove the selected points", self._remove),
        )
        layout.addLayout(self.actions)
        layout.addWidget(self.table)
        self._names: list[str] = []

    def refresh(self) -> None:
        points = self.document.active_definition.points
        self.title.setText(f"Points of <b>{self.document.active}</b> (besides center, top, …)")
        try:
            positions = self.document.results.declared_points()
        except Exception:  # noqa: BLE001 - shown as "error" per row
            positions = {}
        self.table.blockSignals(True)
        self.table.setRowCount(len(points))
        self._names = [p.name for p in points]
        for row, point in enumerate(points):
            position = positions.get(point.name)
            make = _readonly if self.document.read_only else QTableWidgetItem
            cells = [
                make(point.name),
                make(point.at or ""),
                make(_format(point.x)),
                make(_format(point.y)),
                _readonly("error" if position is None else "{:g}, {:g}".format(*position)),
            ]
            cells[0].setToolTip(point.description)
            for column, cell in enumerate(cells):
                self.table.setItem(row, column, cell)
        self.table.blockSignals(False)

    def _changed(self, item: QTableWidgetItem) -> None:
        name = self._names[item.row()]
        text = item.text().strip()
        field = self.FIELDS[item.column()] if item.column() < len(self.FIELDS) else None

        def apply() -> None:
            match field:
                case "name":
                    if text != name:
                        self.document.points.update(name, name=text)
                case "at":
                    self.document.points.update(name, at=text or None)
                case "x" | "y":
                    self.document.points.update(name, **{field: parse_value(text or "0")})

        if field is not None and not self._guard(apply):
            self.refresh()

    def _toggle_internal(self) -> None:
        """Make the selected parameters internal, or public if they all are already."""
        rows = sorted({i.row() for i in self.table.selectedItems()})
        if not rows:
            self.error.emit("select the parameters to make internal or public")
            return
        definitions = {p.name: p for p in self.document.active_definition.parameters}
        names = [self._names[row] for row in rows]
        internal = not all(definitions[n].internal for n in names)
        self._guard(lambda: self.document.parameters.set_internal(names, internal))

    def _remove(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedItems()}, reverse=True)
        for row in rows:
            self._guard(lambda n=self._names[row]: self.document.points.remove(n))


# -- process -------------------------------------------------------------------


class LayersPanel(_Panel):
    """The layers as shown: visibility and colour. Click a layer to draw on it.

    Their definitions (GDS numbers, etch loss, rules) are part of the process
    and edited in the Process tab (:class:`LayerDefinitionsPanel`).
    """

    visibility_changed = Signal(str, bool)

    def __init__(self, document: EditSession) -> None:
        super().__init__()
        self.document = document
        self.visible: dict[str, bool] = {}
        self.colors: dict[str, QColor] = {}
        self.layers = _table(("Layer", "GDS"))
        self.layers.itemChanged.connect(self._layer_changed)
        self.layers.setToolTip("Tick to show a layer; click a layer to draw on it")
        self.layers.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.layers)
        self._layer_names: list[str] = []

    def refresh(self) -> None:
        from mems_sketch.gui.canvas import layer_color

        layers = self.document.project.layers
        self._layer_names = list(layers)
        self.colors = {name: layer_color(i) for i, name in enumerate(layers)}
        self.layers.blockSignals(True)
        self.layers.setRowCount(len(layers))
        for row, layer in enumerate(layers.values()):
            name_cell = QTableWidgetItem(layer.name)
            name_cell.setFlags(name_cell.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            shown = self.visible.get(layer.name, True)
            name_cell.setCheckState(Qt.CheckState.Checked if shown else Qt.CheckState.Unchecked)
            name_cell.setIcon(swatch_icon(self.colors[layer.name]))
            self.layers.setItem(row, 0, name_cell)
            self.layers.setItem(row, 1, _readonly(f"{layer.gds_layer}/{layer.gds_datatype}"))
        self.layers.blockSignals(False)

    def _layer_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        name = self._layer_names[item.row()]
        shown = item.checkState() == Qt.CheckState.Checked
        if shown != self.visible.get(name, True):
            self.visible[name] = shown
            self.visibility_changed.emit(name, shown)


class LayerDefinitionsPanel(_Panel):
    """The process's layers: name, GDS mapping, etch loss and rules (in the Process tab)."""

    LAYER_COLUMNS = ("Layer", "GDS", "Datatype", "Undercut µm", "Min width µm", "Min space µm")

    def __init__(self, document: EditSession) -> None:
        super().__init__()
        self.document = document
        self.layers = _table(self.LAYER_COLUMNS)
        self.layers.itemChanged.connect(self._layer_changed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.actions = _action_bar(
            QLabel("Layers"),
            ("add", "Add layer", lambda: self._guard(self.document.process.add_layer)),
            ("remove", "Remove the selected layers", self._remove_layers),
        )
        layout.addLayout(self.actions)
        layout.addWidget(self.layers)
        self._layer_names: list[str] = []

    def refresh(self) -> None:
        layers = self.document.project.layers
        self._layer_names = list(layers)
        self.layers.blockSignals(True)
        self.layers.setRowCount(len(layers))
        for row, layer in enumerate(layers.values()):
            values = [
                layer.name,
                layer.gds_layer,
                layer.gds_datatype,
                layer.undercut,
                layer.min_width,
                layer.min_space,
            ]
            for column, value in enumerate(values):
                self.layers.setItem(row, column, QTableWidgetItem(_format(value)))
        self.layers.blockSignals(False)
        # the name column also holds the check box and the colour swatch
        header = self.layers.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        metrics = self.layers.fontMetrics()
        widest = max((metrics.horizontalAdvance(n) for n in layers), default=40)
        self.layers.setColumnWidth(0, max(widest, metrics.horizontalAdvance("Layer")) + 64)

    def _layer_changed(self, item: QTableWidgetItem) -> None:
        name = self._layer_names[item.row()]
        row = item.row()
        texts = [self.layers.item(row, c).text().strip() for c in range(len(self.LAYER_COLUMNS))]

        def optional(text: str) -> float | None:
            return float(text) if text else None

        def apply() -> None:
            layer = Layer(
                texts[0],
                int(texts[1]),
                int(texts[2]),
                float(texts[3] or 0),
                optional(texts[4]),
                optional(texts[5]),
            )
            self.document.process.set_layer(name, layer)

        if not self._guard(apply):
            self.refresh()

    def _remove_layers(self) -> None:
        rows = sorted({i.row() for i in self.layers.selectedItems()}, reverse=True)
        for row in rows:
            self._guard(lambda n=self._layer_names[row]: self.document.process.remove_layer(n))


class ConstantsPanel(_Panel):
    """Process constants, available in every expression as ``process.<name>``."""

    def __init__(self, document: EditSession) -> None:
        super().__init__()
        self.document = document
        self.constants = _table(["Constant", "Expression", "Value"])
        self.constants.itemChanged.connect(self._constant_changed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.actions = _action_bar(
            QLabel("Use in expressions as process.<name>"),
            ("add", "Add constant", lambda: self._guard(self.document.process.add_constant)),
            ("remove", "Remove the selected constants", self._remove_constants),
        )
        layout.addLayout(self.actions)
        layout.addWidget(self.constants)
        self._constant_names: list[str] = []

    def refresh(self) -> None:
        project = self.document.project
        constants = project.process.constants
        try:
            values = resolve_variables(constants)
        except (ExpressionError, ZeroDivisionError, ValueError):
            values = {}
        self._constant_names = list(constants)
        self.constants.blockSignals(True)
        self.constants.setRowCount(len(constants))
        for row, (name, expression) in enumerate(constants.items()):
            value = values.get(name)
            self.constants.setItem(row, 0, QTableWidgetItem(name))
            self.constants.setItem(row, 1, QTableWidgetItem(_format(expression)))
            self.constants.setItem(row, 2, _readonly("error" if value is None else f"{value:g}"))
        self.constants.blockSignals(False)

    def _constant_changed(self, item: QTableWidgetItem) -> None:
        name = self._constant_names[item.row()]
        text = item.text().strip()

        def apply() -> None:
            if item.column() == 0 and text != name:
                self.document.process.rename_constant(name, text)
            elif item.column() == 1:
                self.document.process.set_constant(name, parse_value(text))

        if not self._guard(apply):
            self.refresh()

    def _remove_constants(self) -> None:
        rows = sorted({i.row() for i in self.constants.selectedItems()}, reverse=True)
        for row in rows:
            self._guard(
                lambda n=self._constant_names[row]: self.document.process.remove_constant(n)
            )


# -- messages ------------------------------------------------------------------


class MessagesPanel(QListWidget):
    """Errors and rule violations. Clicking a violation zooms to it."""

    zoom_requested = Signal(tuple)

    def __init__(self) -> None:
        super().__init__()
        self.itemActivated.connect(self._activated)
        self.itemClicked.connect(self._activated)

    counts_changed = Signal(int, int)  # errors, violations

    def show_messages(self, errors: list[str], violations) -> None:
        self.clear()
        for text in errors:
            item = QListWidgetItem(icons.icon("error"), text)
            item.setToolTip(text)
            self.addItem(item)
        if violations:
            summary = QListWidgetItem(
                f"{len(violations)} rule violation(s) — click one to zoom to it"
            )
            font = QFont()
            font.setBold(True)
            summary.setFont(font)
            self.addItem(summary)
        for v in violations:
            x0, y0, x1, y1 = v.bbox_um or (0, 0, 0, 0)
            where = f" at ({(x0 + x1) / 2:.2f}, {(y0 + y1) / 2:.2f}) µm" if v.bbox_um else ""
            item = QListWidgetItem(
                icons.icon("warning"), f"[{v.rule}] {v.layer}: {v.message}{where}"
            )
            item.setData(PATH_ROLE, v.bbox_um)
            self.addItem(item)
        if not errors and not violations:
            self.addItem(QListWidgetItem(icons.icon("ok"), "No rule violations."))
        self.counts_changed.emit(len(errors), len(violations))

    def _activated(self, item: QListWidgetItem) -> None:
        bbox = item.data(PATH_ROLE)
        if bbox:
            self.zoom_requested.emit(tuple(bbox))
