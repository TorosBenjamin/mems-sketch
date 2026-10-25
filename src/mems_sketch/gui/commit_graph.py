"""The commit graph: which lane each commit sits in and the lines between them
(:func:`layout`), and the painter for a row of it (:class:`GraphDelegate`).

Commits come newest first, a parent always after its children. Each lane holds
the commit it is waiting for; a commit takes the first lane waiting for it (the
others join it), then waits for its first parent there; a merge's other parents
get a lane of their own, or the one already waiting for them. A lane keeps its
colour while it lasts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from mems_sketch.gui import theme
from mems_sketch.storage import git

LANE_PX = 14
ROW_PX = 24
DOT_RADIUS = 4.5
LANE_COLOURS = ("blue", "orange", "green", "purple", "red")  # icon colours, in turn (a
# local branch's label is tinted with its lane's colour, its name in a deeper shade)
GRAPH_ROLE = Qt.ItemDataRole.UserRole + 40  # a row: (Row, Commit)

Line = tuple[int, int, int]  # from lane, to lane, colour


@dataclass
class Row:
    """One commit's part of the graph. ``top`` lines run from the row's top edge
    to its middle, ``bottom`` ones from the middle down."""

    lane: int
    colour: int
    top: list[Line] = field(default_factory=list)
    bottom: list[Line] = field(default_factory=list)

    @property
    def width(self) -> int:
        """Lanes the row uses."""
        used = [self.lane, *(a for a, _, _ in self.top + self.bottom)]
        used += [b for _, b, _ in self.top + self.bottom]
        return max(used) + 1


def layout(commits: list[git.Commit]) -> list[Row]:
    """The graph rows for ``commits`` (see the module docstring)."""
    lanes: list[str | None] = []  # the commit each lane waits for
    colours: list[int] = []
    next_colour = 0
    rows = []

    def free_lane() -> int:
        for i, waiting in enumerate(lanes):
            if waiting is None:
                return i
        lanes.append(None)
        colours.append(0)
        return len(lanes) - 1

    for commit in commits:
        hits = [i for i, waiting in enumerate(lanes) if waiting == commit.sha]
        top = [
            (i, hits[0] if waiting == commit.sha else i, colours[i])
            for i, waiting in enumerate(lanes)
            if waiting is not None
        ]
        if hits:
            lane = hits[0]
            for i in hits[1:]:
                lanes[i] = None
        else:
            lane = free_lane()
            colours[lane] = next_colour
            next_colour += 1
        lanes[lane] = commit.parents[0] if commit.parents else None
        colour = colours[lane]
        bottom = [(lane, lane, colours[lane])] if commit.parents else []
        new = set()
        for parent in commit.parents[1:]:
            target = next((i for i, w in enumerate(lanes) if w == parent), None)
            if target is None:
                target = free_lane()
                lanes[target] = parent
                colours[target] = next_colour
                next_colour += 1
                new.add(target)
            bottom.append((lane, target, colours[target]))
        bottom += [
            (i, i, colours[i])
            for i, waiting in enumerate(lanes)
            if waiting is not None and i != lane and i not in new
        ]
        while lanes and lanes[-1] is None:
            lanes.pop()
            colours.pop()
        rows.append(Row(lane, colour, top, bottom))
    return rows


def _smaller(font: QFont) -> QFont:
    """``font`` at 85 % (labels), sized in points or pixels as it is."""
    small = QFont(font)
    if font.pointSizeF() > 0:
        small.setPointSizeF(font.pointSizeF() * 0.85)
    else:
        small.setPixelSize(max(1, round(font.pixelSize() * 0.85)))
    return small


def lane_colour(index: int) -> QColor:
    names = LANE_COLOURS
    return QColor(theme.current().icons[names[index % len(names)]])


class GraphDelegate(QStyledItemDelegate):
    """Paints the first column: the lanes and the commit's dot, its branch and tag
    labels, then its subject. ``flat`` draws just the dots (the rows are filtered, so
    the lines would join the wrong commits)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.flat = False

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        found = index.data(GRAPH_ROLE)
        if found is None:
            super().paint(painter, option, index)
            return
        row, commit = found
        style_option = QStyleOptionViewItem(option)
        self.initStyleOption(style_option, index)
        style_option.text = ""
        widget = option.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, style_option, painter, widget)

        rect = option.rect
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        middle = rect.center().y() + 0.5
        left = rect.left() + LANE_PX * 0.75

        def x(lane: int) -> float:
            return left + lane * LANE_PX

        head = any(ref.role == git.HEAD for ref in commit.refs)
        lane = 0 if self.flat else row.lane
        if not self.flat:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for lines, y0, y1 in (
                (row.top, rect.top(), middle),
                (row.bottom, middle, rect.bottom() + 1),
            ):
                for a, b, colour in lines:
                    painter.setPen(QPen(lane_colour(colour), 2))
                    path = QPainterPath(QPointF(x(a), y0))
                    bend = (y1 - y0) * 0.6
                    path.cubicTo(
                        QPointF(x(a), y0 + bend), QPointF(x(b), y1 - bend), QPointF(x(b), y1)
                    )
                    painter.drawPath(path)
        colour = lane_colour(row.colour)
        painter.setPen(QPen(theme.color("island"), 1.5))
        painter.setBrush(colour)
        painter.drawEllipse(QPointF(x(lane), middle), DOT_RADIUS, DOT_RADIUS)
        if head:  # the commit checked out: a ring round its dot
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(colour, 1.5))
            painter.drawEllipse(QPointF(x(lane), middle), DOT_RADIUS + 3, DOT_RADIUS + 3)

        text_x = x(1 if self.flat else row.width) + 2
        small = _smaller(option.font)
        for ref in commit.refs:
            text_x += self._chip(painter, ref, small, text_x, middle, colour) + 4
        font = QFont(option.font)
        font.setBold(head)
        painter.setFont(font)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.setPen(
            option.palette.highlightedText().color() if selected else theme.color("text")
        )
        text_rect = QRectF(text_x + 2, rect.top(), max(0, rect.right() - text_x - 2), rect.height())
        subject = painter.fontMetrics().elidedText(
            commit.subject, Qt.TextElideMode.ElideRight, int(text_rect.width())
        )
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, subject)
        painter.restore()

    def _chip(
        self, painter: QPainter, ref: git.Ref, font: QFont, x: float, middle: float, colour: QColor
    ) -> float:
        """A ref's label: filled in its lane's colour for a local branch (bold for the
        one checked out), outlined for a remote one, a tag in yellow. Its width."""
        bold = QFont(font)
        bold.setBold(ref.role == git.HEAD)
        painter.setFont(bold)
        width = painter.fontMetrics().horizontalAdvance(ref.name) + 12
        box = QRectF(x, middle - 8.5, width, 17)
        if ref.role == git.TAG:
            tag = QColor(theme.current().icons["yellow"])
            fill, ink = QColor(tag), theme.color("text")
            fill.setAlpha(70)
            edge = tag
        elif ref.role == git.REMOTE:
            fill, edge, ink = theme.color("island"), colour, colour
        else:  # tinted, its name in a deeper shade: white on orange did not read (#315)
            fill = QColor(colour)
            fill.setAlpha(46)
            edge = colour
            ink = colour.lighter(135) if theme.current().dark else colour.darker(160)
        painter.setPen(QPen(edge, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(box, 8.5, 8.5)
        painter.setPen(ink)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, ref.name)
        return width

    def sizeHint(self, option, index) -> QSize:
        size = super().sizeHint(option, index)
        return QSize(size.width(), max(size.height(), ROW_PX))
