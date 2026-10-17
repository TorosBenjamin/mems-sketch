"""The window's own title bar: the toolbar row, with minimize, maximize and close.

With *Settings → Appearance → Use the toolbar as the title bar* (on by
default) the system's title bar is removed. Dragging an empty part of
the toolbar moves the window (also from one screen to another, as the system
does it), a double click maximizes or restores it, and the window's edges and
corners resize it. Switched off, the system's title bar comes back.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QAction, QCursor, QMouseEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QToolBar,
    QToolButton,
    QWidget,
)

from mems_sketch.gui import icons

EDGE = 6  # px: how near the window's border a press resizes it

_CURSORS = {
    Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor,
    Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
    Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor,
    Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
    Qt.Edge.LeftEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeFDiagCursor,
    Qt.Edge.RightEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeFDiagCursor,
    Qt.Edge.RightEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeBDiagCursor,
    Qt.Edge.LeftEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeBDiagCursor,
}


def edges_at(frame: QRect, point: QPoint, margin: int = EDGE) -> Qt.Edge:
    """The window edges ``point`` (global) is near, within ``margin`` inside ``frame``."""
    edges = Qt.Edge(0)
    if not frame.contains(point):
        return edges
    if point.x() < frame.left() + margin:
        edges |= Qt.Edge.LeftEdge
    elif point.x() > frame.right() - margin:
        edges |= Qt.Edge.RightEdge
    if point.y() < frame.top() + margin:
        edges |= Qt.Edge.TopEdge
    elif point.y() > frame.bottom() - margin:
        edges |= Qt.Edge.BottomEdge
    return edges


class WindowButtons(QWidget):
    """Minimize, maximize (or restore) and close, at the right end of the toolbar."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__()
        self.setObjectName("window-buttons")
        self.window_ = window
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(0)
        self.minimize = self._button("minimize", "Minimize", window.showMinimized)
        self.maximize = self._button("maximize", "Maximize", self.toggle_maximized)
        self.close = self._button("close", "Close", window.close)
        self.close.setObjectName("window-close")
        for button in (self.minimize, self.maximize, self.close):
            layout.addWidget(button)
        self.update_state()

    def _button(self, icon: str, tip: str, slot) -> QToolButton:
        button = QToolButton()
        button.setObjectName("window-button")
        icons.bind(button, icon)
        button.setIconSize(QSize(16, 16))
        button.setAutoRaise(True)
        button.setToolTip(tip)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(slot)
        return button

    def toggle_maximized(self) -> None:
        window = self.window_
        if window.isMaximized() or window.isFullScreen():
            window.showNormal()
        else:
            window.showMaximized()

    def update_state(self) -> None:
        maximized = self.window_.isMaximized()
        icons.bind(self.maximize, "restore" if maximized else "maximize")
        self.maximize.setToolTip("Restore" if maximized else "Maximize")


class TitleBar(QObject):
    """Makes ``bar`` the window's title bar (see the module)."""

    def __init__(self, window: QMainWindow, bar: QToolBar) -> None:
        super().__init__(window)
        self.window = window
        self.bar = bar
        self.buttons = WindowButtons(window)
        self.buttons_action: QAction = bar.addWidget(self.buttons)
        self.enabled = False
        self._cursor_set = False
        self.buttons_action.setVisible(False)
        QApplication.instance().installEventFilter(self)

    def set_enabled(self, enabled: bool) -> None:
        if enabled == self.enabled:
            return
        self.enabled = enabled
        window = self.window
        shown = window.isVisible()
        geometry = window.geometry()
        window.setWindowFlag(Qt.WindowType.FramelessWindowHint, enabled)
        self.buttons_action.setVisible(enabled)
        self._restore_cursor()
        if shown:  # a change of flags hides the window
            window.setGeometry(geometry)
            window.show()

    # -- moving and resizing ----------------------------------------------------

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        kind = event.type()
        if kind == QEvent.Type.WindowStateChange and obj is self.window:
            self.buttons.update_state()
            return False
        if not self.enabled or kind not in (
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonDblClick,
        ):
            return False
        if not isinstance(obj, QWidget) or obj.window() is not self.window:
            return False
        assert isinstance(event, QMouseEvent)
        point = event.globalPosition().toPoint()
        resizable = not (self.window.isMaximized() or self.window.isFullScreen())
        edges = edges_at(self.window.frameGeometry(), point) if resizable else Qt.Edge(0)
        if kind == QEvent.Type.MouseMove:
            if event.buttons() == Qt.MouseButton.NoButton:
                self._show_cursor(edges)
            return False
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        if edges and kind == QEvent.Type.MouseButtonPress:
            handle = self.window.windowHandle()
            return bool(handle is not None and handle.startSystemResize(edges))
        if not self._on_empty_bar(obj):
            return False
        if kind == QEvent.Type.MouseButtonDblClick:
            self.buttons.toggle_maximized()
            return True
        handle = self.window.windowHandle()
        return bool(handle is not None and handle.startSystemMove())

    def _on_empty_bar(self, widget: QWidget) -> bool:
        """Whether a press on ``widget`` is on the toolbar but not on anything in it."""
        if widget is not self.bar and not self.bar.isAncestorOf(widget):
            return False
        interactive = (QAbstractButton, QLineEdit, QComboBox, QAbstractSpinBox)
        return not isinstance(widget, interactive)

    def _show_cursor(self, edges: Qt.Edge) -> None:
        shape = _CURSORS.get(edges)
        if shape is None:
            self._restore_cursor()
        elif not self._cursor_set:
            QApplication.setOverrideCursor(QCursor(shape))
            self._cursor_set = True
        else:
            QApplication.changeOverrideCursor(QCursor(shape))

    def _restore_cursor(self) -> None:
        if self._cursor_set:
            QApplication.restoreOverrideCursor()
            self._cursor_set = False
