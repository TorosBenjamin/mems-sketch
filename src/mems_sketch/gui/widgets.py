"""Building blocks every panel uses, so the panels look and behave alike.

- :func:`header_actions`: a panel's action buttons, shown in its tool window's
  header (a panel sets ``header_buttons``, and ``header_context`` for the
  muted text after the title, such as the component it shows).
- :func:`make_table`: a table with left-aligned headers, one column that
  takes the room left, no horizontal scrolling, and the full text of a cut-off
  cell as its tooltip.
- :class:`EmptyState`: a centred message (and an action) where a panel has
  nothing to show.
- :func:`splitter`: parts of a panel that can be resized, divided by a hairline.

Sizes come from the theme's metrics (:func:`mems_sketch.gui.theme.metric`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QSplitterHandle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.gui import icons, theme

Action = tuple[str, str, Callable]  # (icon, tooltip, slot)


class Actions:
    """A panel's action buttons, by tooltip (for tests and shortcuts)."""

    def __init__(self, actions: Sequence[Action]) -> None:
        self.buttons: dict[str, QToolButton] = {}
        for name, tip, slot in actions:
            self.buttons[tip] = action_button(name, tip, slot)

    def __iter__(self):
        return iter(self.buttons.values())


def action_button(icon: str, tip: str, slot: Callable) -> QToolButton:
    """A small icon button for a panel's header or toolbar."""
    button = QToolButton()
    icons.bind(button, icon)
    size = theme.metric("icon_size")
    button.setIconSize(QSize(size, size))
    button.setToolTip(tip)
    button.setAutoRaise(True)
    button.clicked.connect(lambda _checked=False: slot())  # not the flag as an argument
    return button


def header_actions(*actions: Action) -> Actions:
    """Buttons for a panel's tool window header: the panel sets ``header_buttons``
    to them (``list(actions)``)."""
    return Actions(actions)


class ElidedLabel(QLabel):
    """One line of plain text, cut off with "…" (and shown whole as the tooltip)
    when it does not fit."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = ""
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:
        self._text = text
        self._elide()

    def text(self) -> str:
        return self._text

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(self._text, Qt.TextElideMode.ElideRight, self.width())
        super().setText(shown)
        self.setToolTip(self._text if shown != self._text else "")


def header_context() -> ElidedLabel:
    """The muted text after a tool window's title: what the panel is showing."""
    label = ElidedLabel()
    label.setObjectName("header-context")
    return label


class _ElidedTooltip(QStyledItemDelegate):
    """Shows a cell's full text as its tooltip when the cell is too narrow for it
    (unless the cell has a tooltip of its own)."""

    def helpEvent(self, event, view, option, index) -> bool:
        if event.type() == QEvent.Type.ToolTip and not index.data(Qt.ItemDataRole.ToolTipRole):
            text = index.data(Qt.ItemDataRole.DisplayRole)
            if text:
                full = QStyleOptionViewItem(option)
                self.initStyleOption(full, index)
                hint = self.sizeHint(full, index).width()
                if hint > option.rect.width():
                    QToolTip.showText(event.globalPos(), str(text), view)
                    return True
        return super().helpEvent(event, view, option, index)


def tidy_header(header: QHeaderView, stretch: int) -> None:
    """Left-aligned titles over their columns; ``stretch`` takes the room left and
    the others fit their contents."""
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    header.setHighlightSections(False)
    header.setStretchLastSection(False)
    header.setMinimumSectionSize(24)
    for column in range(header.count()):
        mode = (
            QHeaderView.ResizeMode.Stretch
            if column == stretch
            else (QHeaderView.ResizeMode.ResizeToContents)
        )
        header.setSectionResizeMode(column, mode)


def elide_with_tooltips(view: QAbstractItemView) -> None:
    """Cut-off text ends in "…", and its tooltip shows all of it."""
    view.setTextElideMode(Qt.TextElideMode.ElideRight)
    view.setItemDelegate(_ElidedTooltip(view))


def make_table(columns: Sequence[str], stretch: int = 0, scroll: bool = False) -> QTableWidget:
    """A panel's table: column ``stretch`` takes the room left (and is cut off with
    "…" when there is too little), the others fit their contents. Only a table on
    a page (``scroll``) scrolls sideways; a panel's table fits its panel."""
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(list(columns))
    tidy_header(table.horizontalHeader(), stretch)
    if not scroll:
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(theme.metric("row_height"))
    table.setShowGrid(False)
    table.setFrameShape(QTableWidget.Shape.NoFrame)
    elide_with_tooltips(table)
    return table


class EmptyState(QWidget):
    """A centred, muted message where a panel has nothing to show, with an optional
    button to do something about it."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("empty-state")
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.button = QPushButton()
        self.button.hide()
        self._slot: Callable | None = None
        self.button.clicked.connect(lambda _checked=False: self._slot and self._slot())
        layout = QVBoxLayout(self)
        margin = theme.metric("space_xl")
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(theme.metric("space_m"))
        layout.addStretch(1)
        layout.addWidget(self.label)
        layout.addWidget(self.button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)

    def set_text(self, text: str) -> None:
        self.label.setText(text)

    def text(self) -> str:
        return self.label.text()

    def set_action(self, text: str | None, slot: Callable | None = None, tip: str = "") -> None:
        """A button under the message (``None``: no button)."""
        self._slot = slot
        self.button.setVisible(text is not None)
        self.button.setText(text or "")
        self.button.setToolTip(tip)


class _HairlineHandle(QSplitterHandle):
    """A splitter handle that is easy to grab but draws only a 1 px line, ``inset``
    px short of both ends."""

    def __init__(self, orientation: Qt.Orientation, parent: QSplitter, inset: int) -> None:
        super().__init__(orientation, parent)
        self.inset = inset

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        color = theme.color("border_strong")
        inset = self.inset
        if self.orientation() == Qt.Orientation.Horizontal:  # side by side: a vertical line
            painter.fillRect(self.width() // 2, inset, 1, self.height() - 2 * inset, color)
        else:
            painter.fillRect(inset, self.height() // 2, self.width() - 2 * inset, 1, color)


class HairlineSplitter(QSplitter):
    """A splitter whose handles draw a hairline (see :func:`splitter`)."""

    def __init__(self, orientation: Qt.Orientation, inset: int = 0) -> None:
        super().__init__(orientation)
        self.inset = inset
        self.setObjectName("panel-split")
        self.setHandleWidth(theme.metric("space_m") - 1)  # odd: the line sits in the middle

    def createHandle(self) -> QSplitterHandle:
        return _HairlineHandle(self.orientation(), self, self.inset)


def splitter(orientation: Qt.Orientation, inset: int = 0) -> QSplitter:
    """Resizable parts of a panel or page, divided by a hairline (the frame-coloured
    gaps are only between islands). In a panel the island's edge already keeps the
    line off the sides; a page that reaches its island's edges gives an ``inset``."""
    split = HairlineSplitter(orientation, inset)
    split.setChildrenCollapsible(False)
    return split
