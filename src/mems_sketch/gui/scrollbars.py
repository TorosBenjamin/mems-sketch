"""Scroll bars that float over the content instead of taking a strip beside it.

The style sheet gives every ordinary scroll bar no width, so it takes no room;
:class:`OverlayScrollBars`, installed on the application, then lays a thin bar
of its own over the edge of each scroll area, following the hidden one. It
shows while the content moves or the pointer is over the area, widens when
pointed at, and fades out a moment after.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QGraphicsOpacityEffect,
    QScrollBar,
    QWidget,
)

from mems_sketch.gui import motion

NAME = "overlay-scroll"  # the floating bars' object name, for the style sheet
THICKNESS = 10  # px: what can be pointed at; the handle is drawn thinner until then
IDLE_MS = 1000  # shown this long after the last scroll or pointer movement


class OverlayBar(QScrollBar):
    """A floating bar over one edge of ``area``, in step with its own (hidden) bar."""

    def __init__(self, area: QAbstractScrollArea, orientation: Qt.Orientation) -> None:
        super().__init__(orientation, area)
        self.setObjectName(NAME)
        self.area = area
        self.native = (
            area.verticalScrollBar()
            if orientation == Qt.Orientation.Vertical
            else area.horizontalScrollBar()
        )
        self.setProperty("wide", False)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.effect = QGraphicsOpacityEffect(self)
        self.effect.setOpacity(0.0)
        self.setGraphicsEffect(self.effect)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.shown = False
        self.idle = QTimer(self, singleShot=True, interval=IDLE_MS, timeout=self.fade)
        self._copy()
        native = self.native
        native.rangeChanged.connect(lambda *_: self._copy())
        native.valueChanged.connect(self._native_moved)
        self.valueChanged.connect(native.setValue)
        self.hide()

    def _copy(self) -> None:
        n = self.native
        self.blockSignals(True)
        self.setRange(n.minimum(), n.maximum())
        self.setPageStep(n.pageStep())
        self.setSingleStep(n.singleStep())
        self.setValue(n.value())
        self.blockSignals(False)
        for bar in bars_of(self.area):  # the other one may now leave the corner
            bar.place()

    def _native_moved(self, value: int) -> None:
        if value != self.value():
            self.blockSignals(True)
            self.setValue(value)
            self.blockSignals(False)
        self.wake()

    def needed(self) -> bool:
        """Whether the area would show its own bar here: not turned off, and
        something to scroll."""
        area = self.area
        policy = (
            area.verticalScrollBarPolicy()
            if self.orientation() == Qt.Orientation.Vertical
            else area.horizontalScrollBarPolicy()
        )
        return policy != Qt.ScrollBarPolicy.ScrollBarAlwaysOff and self.maximum() > self.minimum()

    def near(self, point, widget: QWidget) -> bool:
        """Whether ``point`` (in ``widget``) is by this bar's edge."""
        at = widget.mapTo(self.area, point)
        if self.orientation() == Qt.Orientation.Vertical:
            return at.x() >= self.geometry().left() - THICKNESS
        return at.y() >= self.geometry().top() - THICKNESS

    def place(self) -> None:
        if not self.needed():
            self.hide()
            return
        rect = self.area.viewport().geometry()
        other = next((b for b in bars_of(self.area) if b is not self), None)
        corner = THICKNESS if other is not None and other.needed() else 0  # leave it the corner
        if self.orientation() == Qt.Orientation.Vertical:
            self.setGeometry(
                rect.right() - THICKNESS + 1, rect.top(), THICKNESS, rect.height() - corner
            )
        else:
            self.setGeometry(
                rect.left(), rect.bottom() - THICKNESS + 1, rect.width() - corner, THICKNESS
            )
        self.show()
        self.raise_()

    def wake(self) -> None:
        """Show (fading in) and stay a moment."""
        self.place()
        if not self.needed():
            return
        self.idle.start()
        if not self.shown:
            self.shown = True
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            self._fade_to(1.0)

    def fade(self) -> None:
        if self.underMouse() or self.isSliderDown():
            self.idle.start()
            return
        self.shown = False
        # Faded out, it lets clicks through to what is under it.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._fade_to(0.0)

    def _fade_to(self, target: float) -> None:
        start = self.effect.opacity()
        motion.run(self, lambda t: self.effect.setOpacity(motion.mix(start, target, t)), key="fade")

    def _widen(self, wide: bool) -> None:
        if self.property("wide") != wide:
            self.setProperty("wide", wide)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()

    def enterEvent(self, event) -> None:
        self._widen(True)
        self.wake()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if not self.isSliderDown():
            self._widen(False)
        self.idle.start()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if not self.underMouse():
            self._widen(False)


_WATCHED = {
    QEvent.Type.Show,
    QEvent.Type.Resize,
    QEvent.Type.Enter,
    QEvent.Type.Wheel,
    QEvent.Type.MouseMove,
}


class OverlayScrollBars(QObject):
    """Installed on the application: gives each scroll area floating bars as it
    shows, keeps them on its edges, and shows them as the pointer comes in."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if kind not in _WATCHED or not isinstance(watched, QWidget):
            return False
        if isinstance(watched, OverlayBar):  # a bar's own events: it handles them
            return False
        area = watched if isinstance(watched, QAbstractScrollArea) else watched.parentWidget()
        if not isinstance(area, QAbstractScrollArea):
            return False
        if kind == QEvent.Type.Show and area is watched and not area.property(NAME):
            area.setProperty(NAME, True)
            OverlayBar(area, Qt.Orientation.Vertical)
            OverlayBar(area, Qt.Orientation.Horizontal)
            return False
        for bar in bars_of(area):
            if kind in (QEvent.Type.Resize, QEvent.Type.Show):
                bar.place()
            elif kind != QEvent.Type.MouseMove or bar.near(event.position().toPoint(), watched):
                bar.wake()
        return False


def bars_of(area: QAbstractScrollArea) -> list[OverlayBar]:
    """The floating bars of ``area`` (vertical first), once it has been shown."""
    bars = area.findChildren(OverlayBar, NAME, Qt.FindChildOption.FindDirectChildrenOnly)
    return sorted(bars, key=lambda bar: bar.orientation() != Qt.Orientation.Vertical)


def install(app: QApplication) -> None:
    """Give the application's scroll areas floating bars (once)."""
    if getattr(app, "_overlay_scroll_bars", None) is None:
        app._overlay_scroll_bars = OverlayScrollBars(app)
        app.installEventFilter(app._overlay_scroll_bars)
