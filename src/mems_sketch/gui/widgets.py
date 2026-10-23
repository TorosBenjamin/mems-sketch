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
- :class:`Card` and :func:`page`: a page (the Process page) of sections, each a
  card with its title, actions and "?", at a readable width.
- :func:`placeholder`: a muted message in a list or table while it is empty.
- :class:`MenuTidier`: no menu starts or ends with a separator, or shows two in a row.
- Qualified names (``std.perforated_plate``, ``base.top``): :func:`qualified_html`,
  :class:`QualifiedDelegate` and :class:`QualifiedCombo` show where a name is from
  in the muted colour and the name itself in the text colour.
- Dialogs: :class:`SideList` (the column of pages in Settings and New Project),
  :func:`form` (labelled fields) and :func:`with_help` (a field and its "?").

Sizes come from the theme's metrics (:func:`mems_sketch.gui.theme.metric`).
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Sequence

from PySide6 import QtGui
from PySide6.QtCore import (
    QEvent,
    QItemSelectionModel,
    QModelIndex,
    QObject,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFontMetrics, QKeySequence, QPainter, QPalette, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QSplitterHandle,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionComboBox,
    QStyleOptionViewItem,
    QStylePainter,
    QTableWidget,
    QToolButton,
    QToolTip,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.gui import icons, theme
from mems_sketch.gui.help import HelpButton

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
    when it does not fit: at its end, or (``mode``) in the middle, for a path."""

    def __init__(
        self,
        text: str = "",
        parent: QWidget | None = None,
        mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
    ) -> None:
        super().__init__(parent)
        self._text = ""
        self._mode = mode
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
        shown = self.fontMetrics().elidedText(self._text, self._mode, self.width())
        super().setText(shown)
        self.setToolTip(self._text if shown != self._text else "")


def header_context() -> ElidedLabel:
    """The muted text after a tool window's title: what the panel is showing."""
    label = ElidedLabel()
    label.setObjectName("header-context")
    return label


CELL_PADDING = 4  # px each side: the style sheet's ``QTableView::item`` padding


class _ElidedTooltip(QStyledItemDelegate):
    """Shows a cell's full text as its tooltip when the cell is too narrow for it
    (unless the cell has a tooltip of its own), and asks for room for the style
    sheet's cell padding, which Qt leaves out (columns fitting their contents
    would otherwise cut off their longest text)."""

    def sizeHint(self, option, index) -> QSize:
        hint = super().sizeHint(option, index)
        return QSize(hint.width() + 2 * CELL_PADDING, hint.height())

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


class IconAfterText(_ElidedTooltip):
    """A column whose icon (a lock, say) follows the text, so that the text starts
    at the column's edge like the other columns' (#254)."""

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        option.decorationPosition = QStyleOptionViewItem.Position.Right


TOOLTIP_CHARACTERS = 60  # a tooltip's lines wrap at about this many characters


def wrapped_tooltip(text: str) -> str | None:
    """``text`` as a tooltip that wraps (rich text, which Qt wraps at its width),
    or None when it is short or already rich."""
    if len(text) <= TOOLTIP_CHARACTERS or QtGui.Qt.mightBeRichText(text):
        return None
    lines = "<br>".join(html.escape(line) for line in text.split("\n"))
    width = QFontMetrics(QToolTip.font()).averageCharWidth() * TOOLTIP_CHARACTERS
    return f'<p style="white-space: pre-wrap" width="{width}">{lines}</p>'


class WrappedTooltips(QObject):
    """Installed on the application: a long plain tooltip, a widget's or a list
    row's (a component's description), wraps at about ``TOOLTIP_CHARACTERS``
    characters instead of running across the window (#233)."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.ToolTip or not isinstance(watched, QWidget):
            return False
        text = watched.toolTip()
        view = watched.parent()
        if isinstance(view, QAbstractItemView) and watched is view.viewport():
            index = view.indexAt(event.pos())
            text = str(index.data(Qt.ItemDataRole.ToolTipRole) or "") if index.isValid() else ""
        wrapped = wrapped_tooltip(text) if text else None
        if wrapped is None:
            return False
        QToolTip.showText(event.globalPos(), wrapped, watched)
        return True


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


def select_row(view: QAbstractItemView, row: int) -> None:
    """Select just ``row`` and make it current. Not ``selectRow``: that follows the
    keys held down, and after Shift+Enter selects every row up to it."""
    flags = QItemSelectionModel.SelectionFlag
    index = view.model().index(row, 0)
    view.selectionModel().setCurrentIndex(index, flags.ClearAndSelect | flags.Rows)


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
    """A splitter handle that is easy to grab but draws only a 1 px line."""

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        color = theme.color("border_strong")
        if self.orientation() == Qt.Orientation.Horizontal:  # side by side: a vertical line
            painter.fillRect(self.width() // 2, 0, 1, self.height(), color)
        else:
            painter.fillRect(0, self.height() // 2, self.width(), 1, color)


class HairlineSplitter(QSplitter):
    """A splitter whose handles draw a hairline (see :func:`splitter`)."""

    def __init__(self, orientation: Qt.Orientation) -> None:
        super().__init__(orientation)
        self.setObjectName("panel-split")
        self.setHandleWidth(theme.metric("space_m") - 1)  # odd: the line sits in the middle

    def createHandle(self) -> QSplitterHandle:
        return _HairlineHandle(self.orientation(), self)


def splitter(orientation: Qt.Orientation) -> QSplitter:
    """Resizable parts of a panel, divided by a hairline (the frame-coloured gaps are
    only between islands; the island's edge keeps the line off the panel's sides)."""
    split = HairlineSplitter(orientation)
    split.setChildrenCollapsible(False)
    return split


class Card(QFrame):
    """A section of a page: a header with its title, its actions and "?", then
    ``body`` (a vertical layout) for its contents."""

    def __init__(self, title: str, help: str | None = None) -> None:
        super().__init__()
        self.setObjectName("card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        pad = theme.metric("space_m")
        outer.setContentsMargins(pad, pad // 2, pad, pad)
        outer.setSpacing(theme.metric("space_s"))
        self.header = QHBoxLayout()
        self.header.setSpacing(theme.metric("space_xs"))
        self.title = QLabel(title)
        self.title.setObjectName("card-heading")
        self.header.addWidget(self.title)
        self.header.addStretch(1)
        self._help = HelpButton(help) if help else None
        if self._help is not None:
            self.header.addWidget(self._help)
        outer.addLayout(self.header)
        self.body = QVBoxLayout()
        self.body.setSpacing(theme.metric("space_s"))
        outer.addLayout(self.body)
        self.actions = Actions(())

    def add_actions(self, *actions: Action) -> Actions:
        """Buttons in the card's header, before its "?"."""
        self.actions = Actions(actions)
        at = self.header.count() - (1 if self._help is not None else 0)
        for button in self.actions:
            self.header.insertWidget(at, button)
            at += 1
        return self.actions


def fit_to_rows(table: QTableWidget, minimum: int = 1) -> None:
    """Make a table on a page exactly as tall as its rows (at least ``minimum``),
    so the page scrolls rather than the table."""
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    row = table.verticalHeader().defaultSectionSize()
    header = table.horizontalHeader().sizeHint().height()
    heights = sum(table.rowHeight(r) for r in range(table.rowCount()))  # some may be taller
    missing = max(minimum - table.rowCount(), 0) * row
    bar = (
        table.horizontalScrollBar().sizeHint().height()
        if table.horizontalScrollBar().isVisible()
        else 0
    )
    table.setFixedHeight(header + heights + missing + 2 * table.frameWidth() + bar)


def page(*sections: QWidget, header: QWidget | None = None, width: int = 1100) -> QScrollArea:
    """A scrolling page: ``header``, then ``sections`` one under the other, at most
    ``width`` px wide and centred."""
    content = QWidget()
    content.setObjectName("page")
    content.setMaximumWidth(width)
    column = QVBoxLayout(content)
    margin = theme.metric("space_xl")
    column.setContentsMargins(margin, theme.metric("space_l"), margin, margin)
    column.setSpacing(theme.metric("space_l"))
    if header is not None:
        column.addWidget(header)
    for section in sections:
        column.addWidget(section)
    column.addStretch(1)
    centred = QWidget()
    centred.setObjectName("page")
    row = QHBoxLayout(centred)
    row.setContentsMargins(0, 0, 0, 0)
    row.addStretch(0)
    row.addWidget(content, 1)
    row.addStretch(0)
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    area.setWidget(centred)
    return area


class _Placeholder(QLabel):
    def __init__(self, view: QAbstractItemView, text: str) -> None:
        super().__init__(text, view.viewport())
        self.setObjectName("placeholder")
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._view = view
        model = view.model()
        for signal in (model.rowsInserted, model.rowsRemoved, model.modelReset):
            signal.connect(self._update)
        view.viewport().installEventFilter(self)
        self._update()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize:
            self._update()
        return False

    def _update(self, *_args) -> None:
        margin = theme.metric("space_xl")
        self.setGeometry(self._view.viewport().rect().adjusted(margin, margin, -margin, -margin))
        self.setVisible(not any(self._shown(row) for row in range(self._view.model().rowCount())))

    def _shown(self, row: int) -> bool:
        if isinstance(self._view, QTreeView):
            return not self._view.isRowHidden(row, QModelIndex())
        return not self._view.isRowHidden(row)

    refresh = _update


def placeholder(view: QAbstractItemView, text: str) -> QLabel:
    """A muted message in the middle of ``view`` while it shows no rows. Change it
    with ``setText``; after hiding rows, call its ``refresh()``."""
    return _Placeholder(view, text)


class SideList(QWidget):
    """A dialog's column of pages (Settings, New Project): ``top`` (a search field),
    then the pages with their icons, on the side colour rather than behind a line.
    ``entries`` are ``(icon, text)``; the list is :attr:`list`."""

    def __init__(
        self, entries: Sequence[tuple[str, str]], top: QWidget | None = None, width: int = 190
    ) -> None:
        super().__init__()
        self.setObjectName("dialog-side")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(width)
        self.list = QListWidget()
        self.list.setObjectName("side-list")
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        size = theme.metric("icon_size") + 2
        self.list.setIconSize(QSize(size, size))
        self.list.setSpacing(1)
        for icon, text in entries:
            self.add(icon, text)
        layout = QVBoxLayout(self)
        pad = theme.metric("space_m")
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(pad)
        if top is not None:
            layout.addWidget(top)
        layout.addWidget(self.list, 1)

    def add(self, icon: str, text: str) -> QListWidgetItem:
        item = QListWidgetItem(icons.icon(icon), text)
        item.setSizeHint(QSize(0, theme.metric("row_height") + theme.metric("space_s")))
        self.list.addItem(item)
        return item


def form() -> QFormLayout:
    """A dialog's labelled fields: labels on the left, level with their fields."""
    layout = QFormLayout()
    layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
    layout.setHorizontalSpacing(theme.metric("space_l"))
    layout.setVerticalSpacing(theme.metric("space_m"))
    return layout


def with_help(field: QWidget, help: str, stretch: bool = True) -> QWidget:
    """``field`` with a "?" after it that explains it."""
    row = QWidget()
    line = QHBoxLayout(row)
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(theme.metric("space_s"))
    line.addWidget(field, 0 if stretch else 1)
    line.addWidget(HelpButton(help))
    if stretch:
        line.addStretch(1)
    return row


def dialog_margins(layout: QLayout) -> QLayout:
    """The theme's margins and spacing around a dialog's contents."""
    pad = theme.metric("space_xl")
    layout.setContentsMargins(pad, pad, pad, pad)
    layout.setSpacing(theme.metric("space_l"))
    return layout


def tidy_separators(menu: QMenu) -> None:
    """Show only the separators between visible entries: none first or last, none
    twice in a row (a menu built in groups may have an empty group)."""
    previous_shown = None  # the last visible entry: an action or a separator
    for action in menu.actions():
        if action.isSeparator():
            action.setVisible(previous_shown is not None and not previous_shown.isSeparator())
            if action.isVisible():
                previous_shown = action
        elif action.isVisible():
            previous_shown = action
    if previous_shown is not None and previous_shown.isSeparator():
        previous_shown.setVisible(False)


class MenuTidier(QObject):
    """Installed on the application: tidies every menu's separators as it opens."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Show and isinstance(watched, QMenu):
            tidy_separators(watched)
        return False


# -- qualified names ------------------------------------------------------------
# A name from somewhere else is written with where it is from: ``std.perforated_plate``
# (a library's component), ``base.top`` (a shape's point), ``device.anchor`` (a
# level's layer). That is how it is typed, so the dot stays; the part before it is
# muted, so the eye goes to the name.

HEADER_ROLE = Qt.ItemDataRole.UserRole + 40  # a list's row that heads a group


def split_qualified(name: str) -> tuple[str, str]:
    """``std.perforated_plate`` → ``("std.", "perforated_plate")``; ``("", name)``
    for a name from here."""
    head, dot, last = name.rpartition(".")
    if not (head and last) or " " in head:
        return "", name
    return head + dot, last


def qualified_html(name: str) -> str:
    """A qualified name as rich text: where it is from muted, the name plain."""
    prefix, last = split_qualified(name)
    if not prefix:
        return html.escape(name)
    muted = theme.color("muted").name()
    return f'<span style="color:{muted}">{html.escape(prefix)}</span>{html.escape(last)}'


def draw_qualified(painter: QPainter, rect: QRect, text: str, color: QColor, muted: QColor) -> None:
    """Draw ``text`` in ``rect`` (left, centred vertically) with its qualifier in
    ``muted``; when it is too long the qualifier is cut first, then the name."""
    prefix, last = split_qualified(text)
    metrics = painter.fontMetrics()
    flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    name_width = metrics.horizontalAdvance(last)
    room = rect.width() - name_width
    if prefix and room > metrics.horizontalAdvance("…."):
        shown = metrics.elidedText(prefix, Qt.TextElideMode.ElideLeft, room)
        painter.save()
        painter.setPen(muted)
        painter.drawText(rect, flags, shown)
        painter.restore()
        rect = rect.adjusted(metrics.horizontalAdvance(shown), 0, 0, 0)
    painter.setPen(color)
    painter.drawText(
        rect, flags, metrics.elidedText(last, Qt.TextElideMode.ElideRight, rect.width())
    )


class QualifiedDelegate(QStyledItemDelegate):
    """Draws a list's qualified names with their qualifier muted. ``short`` shows
    only the name, for a list whose rows are grouped under headings that say
    where they are from (rows with ``HEADER_ROLE`` set)."""

    def __init__(self, parent: QObject | None = None, short: bool = False) -> None:
        super().__init__(parent)
        self.short = short

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        size = super().sizeHint(option, index)
        if _is_separator(index):
            return QSize(size.width(), 9)
        return QSize(size.width(), max(size.height(), theme.metric("row_height")))

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        if _is_separator(index):
            y = option.rect.center().y()
            painter.save()
            painter.setPen(theme.color("border_strong"))
            painter.drawLine(option.rect.left() + 6, y, option.rect.right() - 6, y)
            painter.restore()
            return
        item = QStyleOptionViewItem(option)
        self.initStyleOption(item, index)
        text, item.text = item.text, ""
        widget = item.widget
        style = widget.style() if widget is not None else QApplication.style()
        header = bool(index.data(HEADER_ROLE))
        if header:
            item.state &= ~QStyle.StateFlag.State_Selected
            item.state &= ~QStyle.StateFlag.State_MouseOver
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, item, painter, widget)
        rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, item, widget)
        margin = style.pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin, None, widget) + 1
        rect = rect.adjusted(margin, 0, -margin, 0)
        enabled = bool(item.state & QStyle.StateFlag.State_Enabled)
        group = QPalette.ColorGroup.Normal if enabled else QPalette.ColorGroup.Disabled
        color = item.palette.color(group, QPalette.ColorRole.Text)
        muted = theme.color("muted")
        painter.save()
        painter.setFont(item.font)
        if header:
            painter.setPen(muted)
            painter.drawText(
                rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text
            )
        elif self.short and split_qualified(text)[0]:
            painter.setPen(color)
            indented = rect.adjusted(theme.metric("space_m"), 0, 0, 0)
            last = split_qualified(text)[1]
            elided = painter.fontMetrics().elidedText(
                last, Qt.TextElideMode.ElideRight, indented.width()
            )
            painter.drawText(
                indented, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), elided
            )
        else:
            draw_qualified(painter, rect, text, color, muted)
        painter.restore()


def _is_separator(index: QModelIndex) -> bool:
    return index.data(Qt.ItemDataRole.AccessibleDescriptionRole) == "separator"


class QualifiedCombo(QComboBox):
    """A list to choose from whose choices are qualified names: the chosen one and
    the open list show the qualifier muted. ``short`` lists only the names, under
    a heading per qualifier (see :meth:`add_grouped`)."""

    def __init__(self, parent: QWidget | None = None, short: bool = False) -> None:
        super().__init__(parent)
        self.setItemDelegate(QualifiedDelegate(self, short))

    def add_grouped(self, names: Sequence[str]) -> None:
        """Add ``names``, those with a qualifier under a heading per qualifier (a
        row that can't be chosen); names without one come first."""
        plain = [n for n in names if not split_qualified(n)[0]]
        self.addItems(plain)
        heading = None
        for name in names:
            prefix = split_qualified(name)[0]
            if not prefix:
                continue
            if prefix != heading:
                heading = prefix
                self.addItem(prefix[:-1])
                row = self.count() - 1
                self.setItemData(row, True, HEADER_ROLE)
                model_item = self.model().item(row)  # a QStandardItemModel's
                model_item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.addItem(name)
        if self.itemData(self.currentIndex(), HEADER_ROLE):
            self.setCurrentIndex(-1)  # a heading is never the choice

    def paintEvent(self, event) -> None:
        text = self.currentText()
        if self.isEditable() or self.currentIndex() < 0 or not split_qualified(text)[0]:
            super().paintEvent(event)
            return
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        option.currentText = ""
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)
        rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxEditField, self
        )
        if not option.currentIcon.isNull():
            rect.adjust(option.iconSize.width() + 4, 0, 0, 0)
        group = QPalette.ColorGroup.Normal if self.isEnabled() else QPalette.ColorGroup.Disabled
        color = self.palette().color(group, QPalette.ColorRole.ButtonText)
        draw_qualified(painter, rect.adjusted(1, 0, 0, 0), text, color, theme.color("muted"))


class DescriptionEdit(QPlainTextEdit):
    """A description: a text box that wraps, grows with its text up to a few lines
    and then scrolls. Enter is a new line; leaving it or Ctrl+Enter finishes it
    (``editingFinished``, as a one-line field has)."""

    editingFinished = Signal()

    MIN_LINES, MAX_LINES = 2, 6

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setTabChangesFocus(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for keys in ("Ctrl+Return", "Ctrl+Enter"):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(self.editingFinished)
        self.document().contentsChanged.connect(self._fit)
        self._fit()

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, text: str) -> None:
        self.setPlainText(text)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.editingFinished.emit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        """As tall as its lines (as wrapped now), between MIN_LINES and MAX_LINES."""
        line = self.fontMetrics().lineSpacing()
        layout = self.document().documentLayout()
        lines = max(1, round(layout.documentSize().height()))  # lines, in a plain text edit
        shown = min(max(lines, self.MIN_LINES), self.MAX_LINES)
        margins = self.contentsMargins()
        frame = 2 * self.document().documentMargin() + margins.top() + margins.bottom()
        self.setFixedHeight(round(shown * line + frame))


_CODE = re.compile(r"`([^`]+)`")
_STRONG = re.compile(r"\*\*([^*]+)\*\*")
_EMPHASIS = re.compile(r"(?<![\w*])\*([^*]+)\*(?![\w*])")


def markdown_html(text: str) -> str:
    """A description's markdown (code spans, **bold**, *emphasis*, paragraphs) as the
    rich text labels and tooltips show: code in the monospace font. Rich text, so a
    tooltip of it wraps instead of running across the window."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    out = []
    for paragraph in paragraphs:
        escaped = html.escape(" ".join(paragraph.split()), quote=False)
        escaped = _CODE.sub(r"<code>\1</code>", escaped)
        escaped = _STRONG.sub(r"<b>\1</b>", escaped)
        escaped = _EMPHASIS.sub(r"<i>\1</i>", escaped)
        out.append(f"<p>{escaped}</p>")
    return "<qt>" + "".join(out) + "</qt>"


def markdown_label(text: str) -> QLabel:
    """A label showing a description written in markdown (see :func:`markdown_html`)."""
    label = QLabel(markdown_html(text))
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label
