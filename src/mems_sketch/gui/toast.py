"""Toasts: a short note over the editor saying what just happened.

"Exported resonator.gds", "Aligned A to B", "Undid Move": the result of
something the user did, where they are looking, instead of in the status bar
(which keeps hints for the active tool). A note fades in at the bottom of the
editor and out again after a few seconds; an error stays until it is closed
and can carry a button ("Show messages"). Pointing at a note keeps it. The
same text again only restarts its time, and at most ``MOST`` are shown.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.gui import icons, motion, theme

KINDS = {"info": "info", "success": "ok", "warning": "warning", "error": "error"}
SECONDS = {"info": 4, "success": 3, "warning": 6, "error": 0}  # 0: until closed
MOST = 3
WIDTH = 400  # px: every note is as wide (less in a narrow editor)


class Toast(QWidget):
    def __init__(
        self,
        text: str,
        kind: str = "info",
        action: tuple[str, Callable] | None = None,
        seconds: float | None = None,
        key: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        self.setProperty("kind", kind)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.kind = kind
        self.key = key or text
        self.closing = False
        icon = QLabel()
        size = theme.metric("icon_size")
        icon.setPixmap(icons.icon(KINDS.get(kind, "info")).pixmap(size, size))
        self.label = QLabel(text)
        self.label.setObjectName("toast-text")
        self.label.setWordWrap(True)
        row = QHBoxLayout(self)
        pad = theme.metric("space_m")
        row.setContentsMargins(pad + 2, pad - 2, pad - 2, pad - 2)
        row.setSpacing(pad)
        row.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.label, 1)
        self.button: QPushButton | None = None
        if action is not None:
            label, slot = action
            self.button = QPushButton(label)
            self.button.setObjectName("toast-action")
            self.button.setAutoDefault(False)
            self.button.clicked.connect(lambda: (slot(), self.dismiss()))
            row.addWidget(self.button, 0, Qt.AlignmentFlag.AlignVCenter)
        close = QToolButton()
        close.setObjectName("toast-close")
        icons.bind(close, "close")
        close.setToolTip("Close")
        close.clicked.connect(self.dismiss)
        row.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._seconds = SECONDS.get(kind, 4) if seconds is None else seconds

    def text(self) -> str:
        return self.label.text()

    def show_for(self) -> None:
        """Fade in and start the time it stays (pointing at it keeps it)."""
        self.closing = False
        self.show()
        start = self._opacity.opacity()
        motion.run(self, lambda t: self._opacity.setOpacity(motion.mix(start, 1.0, t)), key="fade")
        self.restart()

    def restart(self) -> None:
        if self._seconds > 0 and not self.underMouse():
            self._timer.start(int(self._seconds * 1000))

    def enterEvent(self, event) -> None:
        self._timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.restart()
        super().leaveEvent(event)

    def dismiss(self) -> None:
        if self.closing:
            return
        self.closing = True
        self._timer.stop()
        start = self._opacity.opacity()
        motion.run(
            self,
            lambda t: self._opacity.setOpacity(motion.mix(start, 0.0, t)),
            done=self._gone,
            key="fade",
        )

    def _gone(self) -> None:
        stack = self.parent()
        self.hide()
        self.deleteLater()
        if isinstance(stack, Toasts):
            stack.forget(self)


class Toasts(QWidget):
    """The notes over ``area`` (a widget of ``window``), newest at the bottom."""

    def __init__(self, window: QWidget, area: QWidget) -> None:
        super().__init__(window)
        self.setObjectName("toasts")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._area = area
        self._column = QVBoxLayout(self)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(theme.metric("space_s"))
        self._shown: list[Toast] = []
        self._watch = _Watcher(self)
        area.installEventFilter(self._watch)
        window.installEventFilter(self._watch)
        self.hide()

    def show_toast(
        self,
        text: str,
        kind: str = "info",
        action: tuple[str, Callable] | None = None,
        seconds: float | None = None,
        key: str = "",
    ) -> Toast:
        """Show a note; one with the same ``key`` (or text) is replaced."""
        for toast in self._shown:
            if toast.key == (key or text) and not toast.closing:
                self.forget(toast)
                toast.hide()
                toast.deleteLater()
                break
        while len([t for t in self._shown if not t.closing]) >= MOST:
            oldest = next(t for t in self._shown if not t.closing)
            self.forget(oldest)
            oldest.hide()
            oldest.deleteLater()
        toast = Toast(text, kind, action, seconds, key, self)
        self._shown.append(toast)
        self._column.addWidget(toast)
        self.show()
        self.raise_()
        toast.show_for()
        self.place()
        return toast

    def toasts(self) -> list[Toast]:
        """The notes shown, oldest first (not those fading out)."""
        return [t for t in self._shown if not t.closing]

    def texts(self) -> list[str]:
        return [t.text() for t in self.toasts()]

    def clear(self) -> None:
        for toast in list(self._shown):
            self.forget(toast)
            toast.hide()
            toast.deleteLater()

    def forget(self, toast: Toast) -> None:
        if toast in self._shown:
            self._shown.remove(toast)
            self._column.removeWidget(toast)
        if not self._shown:
            self.hide()
        else:
            self.place()

    def place(self) -> None:
        """At the bottom of the editor, in the middle, clear of the scale bar; all
        notes as wide, so they line up."""
        if not self._shown:
            return
        area = self._area
        width = max(min(WIDTH, area.width() - 2 * theme.metric("space_xl")), 160)
        layout = self.layout()
        height = (
            layout.totalHeightForWidth(width)
            if layout.hasHeightForWidth()
            else layout.totalSizeHint().height()
        )
        corner = area.mapTo(self.parentWidget(), QPoint(0, 0))
        x = corner.x() + (area.width() - width) // 2
        y = corner.y() + area.height() - height - theme.metric("space_xl") * 3
        self.setGeometry(x, max(corner.y(), y), width, height)


class _Watcher(QObject):
    def __init__(self, stack: Toasts) -> None:
        super().__init__(stack)
        self._stack = stack

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            self._stack.place()
        return False
