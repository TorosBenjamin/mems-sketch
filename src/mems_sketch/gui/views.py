"""Component tabs: each tab shows one component on its own canvas.

A :class:`ComponentView` keeps what belongs to one tab: the canvas (and its
zoom), the selection and the view mode. The :class:`EditorArea` holds the tabs
in one pane, or two side by side (split view), like a code editor. The panels
of the main window follow the current tab.
"""

from __future__ import annotations

import klayout.db as kdb
from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtWidgets import QSplitter, QTabBar, QTabWidget, QToolButton, QVBoxLayout, QWidget

from mems_sketch.core.component import Geometry
from mems_sketch.core.shapes import NodePath
from mems_sketch.gui import icons
from mems_sketch.gui.canvas import LayoutCanvas
from mems_sketch.gui.document import ProjectDocument

MAX_PANES = 2


class ComponentView(QWidget):
    """One tab: a component, its canvas, selection and view mode."""

    def __init__(self, document: ProjectDocument, component: str) -> None:
        super().__init__()
        self.document = document
        self.component = component
        self.canvas = LayoutCanvas()
        self.selection: list[NodePath] = []
        self.view_mode = "drawn"
        self.node_regions: list[tuple[NodePath, kdb.Region]] = []
        self.violations: list = []
        self.errors: list[str] = []
        self._fitted = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)

    @property
    def read_only(self) -> bool:
        return self.component not in self.document.project.components

    def title(self) -> str:
        name = self.component
        if self.read_only:
            return f"{name} (read-only)"
        return f"{name}*" if self.document.modified(name) else name

    def icon_name(self) -> str:
        if self.read_only:
            return "lock"
        return "top" if self.component == self.document.project.top else "component"

    def tooltip(self) -> str:
        if self.read_only:
            where = "library" if "." in self.component else "built-in"
            return f"{self.component} — {where} component, read-only (trial values work)"
        top = " (top)" if self.component == self.document.project.top else ""
        changed = ", changed since the last save" if self.document.modified(self.component) else ""
        return f"{self.component}{top} — project component{changed}"

    def refresh(self, colors: dict, visible: dict[str, bool]) -> None:
        """Recompile the component and redraw; errors are kept for the messages panel."""
        self.errors = []
        try:
            drawn = self.document.geometry(component=self.component)
            geometry = (
                drawn
                if self.view_mode == "drawn"
                else self.document.geometry(self.view_mode, self.component)
            )
            self.violations = self.document.check(drawn)
        except Exception as exc:  # noqa: BLE001 - shown in the messages panel
            geometry = Geometry()
            self.violations = []
            self.errors.append(str(exc))
        self.node_regions = self.document.node_regions(visible, self.component)
        self.selection = [
            p for p in self.selection if p in self.document.inspection(self.component)
        ]
        self.canvas.show_geometry(geometry, colors, visible)
        if not self._fitted and geometry.layers:
            self._fitted = True
            self.canvas.fit()


class EditorArea(QSplitter):
    """Tabs of component views in one or two panes.

    ``current`` is the view the user works in: the current tab of the pane
    that was used last. ``current_changed`` is emitted when it changes.
    """

    current_changed = Signal(object)  # ComponentView
    tabs_changed = Signal()  # tabs were opened, closed, moved or renamed
    tab_menu_requested = Signal(object, QPoint)  # ComponentView, global position

    def __init__(self, document: ProjectDocument) -> None:
        super().__init__(Qt.Orientation.Horizontal)
        self.document = document
        self.panes: list[QTabWidget] = []
        self.current: ComponentView | None = None
        self._add_pane()

    # -- panes ---------------------------------------------------------------

    def _add_pane(self) -> QTabWidget:
        pane = QTabWidget()
        pane.setMovable(True)
        pane.setDocumentMode(True)
        pane.tabCloseRequested.connect(lambda index, p=pane: self.close_tab(p, index))
        pane.currentChanged.connect(lambda _index, p=pane: self._pane_changed(p))
        pane.tabBarClicked.connect(lambda index, p=pane: self._focus(p, index))
        bar = pane.tabBar()
        bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        bar.customContextMenuRequested.connect(lambda pos, p=pane: self._tab_menu(p, pos))
        self.addWidget(pane)
        self.panes.append(pane)
        return pane

    def _close_button(self, pane: QTabWidget, view: ComponentView) -> None:
        """A small close button on the tab (the style's own one does not fit the theme)."""
        button = QToolButton()
        icons.bind(button, "close")
        button.setIconSize(QSize(12, 12))
        button.setAutoRaise(True)
        button.setToolTip("Close (Ctrl+W)")
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setObjectName("tab-close")
        button.clicked.connect(lambda: self.close_view(view))
        pane.tabBar().setTabButton(pane.indexOf(view), QTabBar.ButtonPosition.RightSide, button)

    def _tab_menu(self, pane: QTabWidget, pos: QPoint) -> None:
        index = pane.tabBar().tabAt(pos)
        if index >= 0:
            self.tab_menu_requested.emit(pane.widget(index), pane.tabBar().mapToGlobal(pos))

    def close_others(self, view: ComponentView) -> None:
        for other in self.views():
            if other is not view:
                self.close_view(other)
        self.set_current(view)

    def _remove_pane(self, pane: QTabWidget) -> None:
        self.panes.remove(pane)
        pane.setParent(None)
        pane.deleteLater()

    def pane_of(self, view: ComponentView) -> QTabWidget | None:
        return next((p for p in self.panes if p.indexOf(view) >= 0), None)

    @property
    def split(self) -> bool:
        return len(self.panes) > 1

    # -- views ---------------------------------------------------------------

    def views(self) -> list[ComponentView]:
        return [pane.widget(i) for pane in self.panes for i in range(pane.count())]

    def find(self, component: str, pane: QTabWidget | None = None) -> ComponentView | None:
        panes = [pane] if pane is not None else self.panes
        for p in panes:
            for i in range(p.count()):
                if p.widget(i).component == component:
                    return p.widget(i)
        return None

    def open(
        self, component: str, pane: QTabWidget | None = None, anywhere: bool = False
    ) -> ComponentView:
        """Show ``component`` in a tab of ``pane`` (default: the current pane), made current.

        Like a code editor, an existing tab in that pane is reused, else a new
        one is opened there. With ``anywhere``, a tab in any pane is reused first.
        """
        pane = pane or self._current_pane()
        view = self.find(component, pane)
        if view is None and anywhere:
            view = self.find(component)
        if view is None:
            view = ComponentView(self.document, component)
            pane.addTab(view, icons.icon(view.icon_name()), view.title())
            pane.setTabToolTip(pane.indexOf(view), view.tooltip())
            self._close_button(pane, view)
            self.tabs_changed.emit()
        self.set_current(view)
        return view

    def set_current(self, view: ComponentView) -> None:
        pane = self.pane_of(view)
        pane.setCurrentWidget(view)
        self._make_current(view)

    def close_tab(self, pane: QTabWidget, index: int) -> None:
        view = pane.widget(index)
        was_current = self.current is view
        if was_current:
            self.current = None  # removing the tab switches tabs; do not refer to it
        pane.removeTab(index)
        view.deleteLater()
        if pane.count() == 0 and self.split:
            self._remove_pane(pane)
            pane = self.panes[0]
        remaining = self.views()
        if was_current and remaining:
            self._make_current(pane.currentWidget() or remaining[0])
        self.tabs_changed.emit()
        if not remaining:
            self.open(self.document.project.top)

    def close_view(self, view: ComponentView) -> None:
        pane = self.pane_of(view)
        self.close_tab(pane, pane.indexOf(view))

    def close_all(self) -> None:
        self.current = None
        for pane in self.panes[1:]:
            self._remove_pane(pane)
        pane = self.panes[0]
        while pane.count():
            view = pane.widget(0)
            pane.removeTab(0)
            view.deleteLater()
        self.current = None

    def split_view(self) -> ComponentView | None:
        """Open the current component in the other pane (creating it), like a code editor."""
        if self.current is None:
            return None
        component = self.current.component
        if self.split:
            other = next(p for p in self.panes if p is not self.pane_of(self.current))
        else:
            other = self._add_pane()
            self.setSizes([1, 1])
        return self.open(component, other)

    def unsplit(self) -> None:
        """Merge the second pane's tabs into the first."""
        while self.split:
            pane = self.panes[-1]
            while pane.count():
                view = pane.widget(0)
                title = pane.tabText(0)
                pane.removeTab(0)
                if self.find(view.component, self.panes[0]) is None:
                    self.panes[0].addTab(view, icons.icon(view.icon_name()), title)
                    self._close_button(self.panes[0], view)
                else:
                    if self.current is view:
                        self.current = None
                    view.deleteLater()
            self._remove_pane(pane)
        if self.current is None and self.views():
            self._make_current(self.panes[0].currentWidget())
        self.tabs_changed.emit()

    def remove_pane_if_empty(self) -> None:
        for pane in list(self.panes):
            if pane.count() == 0 and self.split:
                self._remove_pane(pane)

    def rename(self, old: str, new: str) -> None:
        for view in self.views():
            if view.component == old:
                view.component = new
        self.update_titles()

    def update_titles(self) -> None:
        for pane in self.panes:
            for i in range(pane.count()):
                view = pane.widget(i)
                pane.setTabText(i, view.title())
                pane.setTabIcon(i, icons.icon(view.icon_name()))
                pane.setTabToolTip(i, view.tooltip())

    def layout_state(self) -> dict:
        """Open tabs per pane and the current one, e.g. to restore them next time."""
        return {
            "panes": [
                [pane.widget(i).component for i in range(pane.count())] for pane in self.panes
            ],
            "current": self.current.component if self.current else None,
            "current_pane": self.panes.index(self._current_pane()),
        }

    # -- focus ---------------------------------------------------------------

    def _current_pane(self) -> QTabWidget:
        pane = self.pane_of(self.current) if self.current is not None else None
        return pane or self.panes[0]

    def _focus(self, pane: QTabWidget, index: int) -> None:
        if index >= 0:
            self._make_current(pane.widget(index))

    def _pane_changed(self, pane: QTabWidget) -> None:
        view = pane.currentWidget()
        # A tab switch in the pane being worked in changes the current view.
        if view is not None and self._current_pane() is pane:
            self._make_current(view)

    def _make_current(self, view: ComponentView) -> None:
        if view is not self.current:
            self.current = view
            self.current_changed.emit(view)
