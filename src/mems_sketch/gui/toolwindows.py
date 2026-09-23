"""Tool windows opened and closed from stripes on the window edges, as in IntelliJ.

Every window has an anchor. ``left-top`` and ``left-bottom`` share the left
side, split in two when both have a window open; ``bottom`` sits under the
editor and ``right`` beside it. An anchor shows one window at a time:
opening another one there replaces it. Each window has a button on a stripe:
the left stripe holds the two left anchors at the top (with a gap between
them) and ``bottom`` at the bottom; the right stripe holds ``right``.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.gui import icons
from mems_sketch.gui.theme import HEADER_HEIGHT

ANCHORS = ("left-top", "left-bottom", "bottom", "right")
STRIPE_WIDTH = 38
GROUP_GAP = 14  # px between the two left groups on the stripe
# Splitter sizes until the user changes them: [left, editor, right], [left top,
# left bottom] and [editor, bottom].
DEFAULT_SIZES = {"main": [270, 900, 320], "left": [300, 380], "center": [700, 120]}


@dataclass
class _Window:
    name: str
    title: str
    widget: QWidget
    anchor: str
    button: QToolButton
    header_buttons: QWidget


class _Host(QFrame):
    """The panel of one anchor: a header with the open window's title and its own
    buttons (a panel's ``header_buttons``), and the window."""

    def __init__(self, hide) -> None:
        super().__init__()
        self.setObjectName("tool-window")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QWidget()
        header.setObjectName("dock-title")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header.setFixedHeight(HEADER_HEIGHT)  # as tall as the editor tabs: edges line up
        row = self.row = QHBoxLayout(header)
        row.setContentsMargins(10, 0, 4, 0)
        row.setSpacing(1)
        self.title = QLabel()
        self.title.setObjectName("dock-title-label")
        row.addWidget(self.title, 1)
        close = QToolButton()
        close.setAutoRaise(True)
        close.setToolTip("Hide")
        icons.bind(close, "minimize")
        close.clicked.connect(hide)
        row.addWidget(close)
        self.stack = QStackedWidget()
        layout.addWidget(header)
        layout.addWidget(self.stack, 1)
        self.current: _Window | None = None


class ToolWindows(QWidget):
    """The window's body: the editor with the tool windows around it."""

    changed = Signal()  # a window was opened or closed, or a panel resized

    def __init__(self, editor: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._windows: dict[str, _Window] = {}
        self._hosts = {anchor: _Host(lambda a=anchor: self._hide(a)) for anchor in ANCHORS}

        self.left_side = QSplitter(Qt.Orientation.Vertical)
        self.left_side.addWidget(self._hosts["left-top"])
        self.left_side.addWidget(self._hosts["left-bottom"])
        self.center = QSplitter(Qt.Orientation.Vertical)
        self.center.addWidget(editor)
        self.center.addWidget(self._hosts["bottom"])
        self.center.setStretchFactor(0, 1)
        self.main = QSplitter(Qt.Orientation.Horizontal)
        self.main.addWidget(self.left_side)
        self.main.addWidget(self.center)
        self.main.addWidget(self._hosts["right"])
        self.main.setStretchFactor(1, 1)
        self._splitters = {"main": self.main, "left": self.left_side, "center": self.center}
        for name, splitter in self._splitters.items():
            splitter.setChildrenCollapsible(False)
            splitter.setSizes(DEFAULT_SIZES[name])
            splitter.splitterMoved.connect(self.changed)

        self._groups: dict[str, QVBoxLayout] = {}
        left = self._stripe("left")
        self._groups["left-top"] = self._group(left)
        left.layout().addSpacing(GROUP_GAP)
        self._groups["left-bottom"] = self._group(left)
        left.layout().addStretch()
        self._groups["bottom"] = self._group(left)
        right = self._stripe("right")
        self._groups["right"] = self._group(right)
        right.layout().addStretch()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(left)
        layout.addWidget(self.main, 1)
        layout.addWidget(right)
        self._update()

    def _stripe(self, side: str) -> QWidget:
        stripe = QWidget()
        stripe.setObjectName(f"tool-window-stripe-{side}")
        stripe.setFixedWidth(STRIPE_WIDTH)
        column = QVBoxLayout(stripe)
        column.setContentsMargins(4, 6, 4, 6)
        column.setSpacing(4)
        return stripe

    @staticmethod
    def _group(stripe: QWidget) -> QVBoxLayout:
        group = QVBoxLayout()
        group.setSpacing(4)
        stripe.layout().addLayout(group)
        return group

    # -- windows -------------------------------------------------------------

    def add(self, name: str, title: str, icon: str, widget: QWidget, anchor: str) -> None:
        """Add a tool window (closed) with a button on its stripe."""
        if anchor not in ANCHORS:
            raise ValueError(f"unknown anchor '{anchor}'")
        button = QToolButton()
        button.setCheckable(True)
        button.setAutoRaise(True)
        button.setFixedSize(30, 30)
        button.setIconSize(QSize(18, 18))
        button.setToolTip(title)
        icons.bind(button, icon)
        button.clicked.connect(lambda _=False, n=name: self.toggle(n))
        self._groups[anchor].addWidget(button)
        host = self._hosts[anchor]
        host.stack.addWidget(widget)
        buttons = QWidget()  # the panel's own buttons, shown in the header while it is open
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(1)
        for header_button in getattr(widget, "header_buttons", []):
            row.addWidget(header_button)
        buttons.hide()
        host.row.insertWidget(host.row.count() - 1, buttons)  # before the hide button
        self._windows[name] = _Window(name, title, widget, anchor, button, buttons)

    def names(self) -> list[str]:
        return list(self._windows)

    def title(self, name: str) -> str:
        return self._windows[name].title

    def button(self, name: str) -> QToolButton:
        return self._windows[name].button

    def is_open(self, name: str) -> bool:
        window = self._windows[name]
        return self._hosts[window.anchor].current is window

    def open(self, name: str) -> None:
        """Show a window, in place of the one open at its anchor."""
        window = self._windows[name]
        host = self._hosts[window.anchor]
        if host.current is not None and host.current is not window:
            host.current.button.setChecked(False)
            host.current.header_buttons.hide()
        host.current = window
        host.stack.setCurrentWidget(window.widget)
        host.title.setText(window.title)
        window.header_buttons.show()
        window.button.setChecked(True)
        self._update()
        self.changed.emit()

    def close(self, name: str) -> None:
        window = self._windows[name]
        host = self._hosts[window.anchor]
        window.button.setChecked(False)
        if host.current is window:
            window.header_buttons.hide()
            host.current = None
            self._update()
            self.changed.emit()

    def toggle(self, name: str) -> None:
        if self.is_open(name):
            self.close(name)
        else:
            self.open(name)

    def _hide(self, anchor: str) -> None:
        current = self._hosts[anchor].current
        if current is not None:
            self.close(current.name)

    def _update(self) -> None:
        for host in self._hosts.values():
            host.setVisible(host.current is not None)
        left = [self._hosts["left-top"], self._hosts["left-bottom"]]
        self.left_side.setVisible(any(h.current is not None for h in left))

    # -- remembering the layout ------------------------------------------------

    def state(self) -> dict:
        """Which windows are open and the panel sizes, to restore them later."""
        return {
            "open": [name for name in self._windows if self.is_open(name)],
            "sizes": {name: s.sizes() for name, s in self._splitters.items()},
        }

    def restore(self, state: dict) -> None:
        """Bring back a :meth:`state`; unknown windows and unusable sizes are skipped."""
        for name in self._windows:
            self.close(name)
        for name in state.get("open", []):
            if name in self._windows:
                self.open(name)
        for name, sizes in state.get("sizes", {}).items():
            splitter = self._splitters.get(name)
            default = DEFAULT_SIZES.get(name)
            if splitter is None or not isinstance(sizes, list) or len(sizes) != len(default):
                continue
            if all(isinstance(v, int) for v in sizes):
                # A panel that was closed was saved as 0: it gets its default size back.
                splitter.setSizes([v if v > 0 else d for v, d in zip(sizes, default, strict=True)])
