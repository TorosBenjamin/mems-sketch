"""Placing components: the Place popup (Shift+A) and what it shares with the
Components tab of Search Everywhere.

The components that can be placed in the open component are listed by where
they are from (this project, each library, the built-in ones), matched by what
is typed (every word must be in the name or the description; names that start
with it come first) and drawn as rows by :class:`RowDelegate`: an icon, the
name, then muted the description or where it is from. A source's heading folds
its rows away. :func:`thumbnail` draws a component's geometry for a preview.

The popup lists the recently placed components first, then every source;
typing turns it into one ranked list. Enter (or a click) places the component:
it follows the cursor until a click puts it down (see ``PlaceTool``), or, opened
from a right-click, it lands where that click was. *Browse all components* opens
the Components tab of Search Everywhere, with a preview.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QSettings, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.gui import icons, theme
from mems_sketch.gui.settings import APPLICATION, ORGANIZATION

THIS_PROJECT = "This project"
BUILT_IN = "Built-in"
RECENT = "Recent"
RECENT_KEY = "place/recent"  # the names placed last, newest first
RECENT_MOST = 3
FOLDED_KEY = "place/folded"  # the sources folded away in Search Everywhere
ENTRY_ROLE = Qt.ItemDataRole.UserRole + 40  # a row: its Entry
HEADING_ROLE = Qt.ItemDataRole.UserRole + 41  # a heading: (source, count, folded or None)
QUALIFIED_ROLE = Qt.ItemDataRole.UserRole + 42  # a row shows where it is from before its name
SOURCE_ROLE = Qt.ItemDataRole.UserRole + 43  # a row shows where it is from on the right
MATCH_ROLE = Qt.ItemDataRole.UserRole + 44  # the typed text: the start of the name it matches
ROW_HEIGHT = 28
HEADING_HEIGHT = 24
HEADING_SCALE = 0.85  # a heading's text, against a row's
POPUP_WIDTH = 330
POPUP_MAX_ROWS = 12
KEYCAP_HEIGHT = 18  # rows shown before the list scrolls


@dataclass(frozen=True)
class Entry:
    """A component that can be placed: the name to place it by, where it is from
    and what it is (its description's first sentence)."""

    name: str
    source: str
    description: str = ""
    icon: str = "component"

    @property
    def short(self) -> str:
        """The name without where it is from (``perforated_plate`` for ``std.perforated_plate``)."""
        return self.name.rsplit(".", 1)[-1]

    @property
    def prefix(self) -> str:
        return self.name[: len(self.name) - len(self.short)]

    def score(self, words: list[str]) -> int | None:
        """How well it matches (lower is better), or None when a word is missing."""
        name = self.name.lower()
        haystack = f"{name} {self.source} {self.description}".lower()
        if not all(word in haystack for word in words):
            return None
        if not words:
            return 2
        text = " ".join(words)
        short = self.short.lower()
        return 0 if short.startswith(text) or name.startswith(text) else 1 if text in name else 2


def first_sentence(text: str) -> str:
    text = " ".join(text.split())
    head, dot, _ = text.partition(". ")
    return head.rstrip(".") if dot else text.rstrip(".")


def entries(document) -> list[Entry]:
    """The components that can be placed in the open component, by source: this
    project's, each library's (by name), then the built-in ones."""
    from mems_sketch.gui.panels import component_icon  # panels imports widgets that use this

    project = document.project
    found = []
    for name in document.component_names():
        if name == document.active:
            continue
        source = source_of(project, name)
        try:
            description = first_sentence(document.definition_of(name).description or "")
        except Exception:  # noqa: BLE001 - a component that does not load: no description
            description = ""
        found.append(Entry(name, source, description, component_icon(project, name)))
    order = {THIS_PROJECT: 0, BUILT_IN: 2}
    return sorted(found, key=lambda e: (order.get(e.source, 1), e.source, e.short.lower()))


def source_of(project, name: str) -> str:
    """Where a component is from: this project, a library (its name), or built in."""
    if name in project.components:
        return THIS_PROJECT
    if "." in name:
        return name.split(".", 1)[0]
    return BUILT_IN


def sources(found: list[Entry]) -> list[str]:
    """The sources of ``found``, in the order they are listed."""
    return list(dict.fromkeys(e.source for e in found))


def ranked(found: list[Entry], text: str) -> list[Entry]:
    words = text.lower().split()
    scored = [(score, i, e) for i, e in enumerate(found) if (score := e.score(words)) is not None]
    return [e for _, _, e in sorted(scored, key=lambda s: s[:2])]


# -- remembered -------------------------------------------------------------------


def _settings() -> QSettings:
    return QSettings(ORGANIZATION, APPLICATION)


def recent() -> list[str]:
    return [str(n) for n in (_settings().value(RECENT_KEY, [], type=list) or [])]


def remember(name: str) -> None:
    """Put ``name`` first among the recently placed components."""
    names = [name, *(n for n in recent() if n != name)][:10]
    _settings().setValue(RECENT_KEY, names)


def folded() -> set[str]:
    return {str(s) for s in (_settings().value(FOLDED_KEY, [], type=list) or [])}


def set_folded(source: str, fold: bool) -> None:
    now = folded() | {source} if fold else folded() - {source}
    _settings().setValue(FOLDED_KEY, sorted(now))


# -- rows -------------------------------------------------------------------------


def heading_item(
    source: str, count: int | None = None, fold: bool | None = None
) -> QListWidgetItem:
    """A source's heading; ``fold`` (folded or not) makes it fold its rows on a click."""
    item = QListWidgetItem(source)
    item.setData(HEADING_ROLE, (source, count, fold))
    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
    item.setSizeHint(QSize(0, HEADING_HEIGHT))
    return item


def entry_item(
    entry: Entry, qualified: bool = False, source: bool = False, match: str = ""
) -> QListWidgetItem:
    """A component's row: its name (with where it is from before it when
    ``qualified``, after it when ``source``) and the typed text it starts with."""
    item = QListWidgetItem(entry.name)
    item.setData(ENTRY_ROLE, entry)
    item.setData(QUALIFIED_ROLE, qualified)
    item.setData(SOURCE_ROLE, source)
    item.setData(MATCH_ROLE, match)
    item.setToolTip(f"{entry.name}\n{entry.description}".strip())
    item.setSizeHint(QSize(0, ROW_HEIGHT))
    return item


class RowDelegate(QStyledItemDelegate):
    """Draws a component's row (icon, name, then muted its description or source)
    or a source's heading (with a fold arrow), in the theme's colours.

    ``descriptions``: rows show the component's description after its name."""

    def __init__(self, parent=None, descriptions: bool = False) -> None:
        super().__init__(parent)
        self.descriptions = descriptions

    def sizeHint(self, option, index) -> QSize:
        heading = index.data(HEADING_ROLE) is not None
        return QSize(option.rect.width(), HEADING_HEIGHT if heading else ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index) -> None:
        heading = index.data(HEADING_ROLE)
        entry = index.data(ENTRY_ROLE)
        if heading is None and entry is None:
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect
        text, muted = theme.color("text"), theme.color("muted")
        if heading is not None:
            source, count, fold = heading
            x = rect.left() + 8
            if fold is not None:
                arrow = icons.pixmap("right" if fold else "down", 12, muted.name())
                painter.drawPixmap(x - 2, rect.center().y() - 5, arrow)
                x += 14
            font = QFont(option.font)
            font.setWeight(QFont.Weight.DemiBold)
            font.setPointSizeF(option.font.pointSizeF() * HEADING_SCALE)
            painter.setFont(font)
            painter.setPen(muted)
            box = QRect(x, rect.top() + 4, rect.width() - (x - rect.left()), rect.height() - 4)
            flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            painter.drawText(box, flags, source)
            if count:
                shift = painter.fontMetrics().horizontalAdvance(source) + 6
                font.setWeight(QFont.Weight.Normal)
                painter.setFont(font)
                painter.drawText(box.adjusted(shift, 0, 0, 0), flags, str(count))
            painter.restore()
            return
        if option.state & QStyle.StateFlag.State_Selected:
            path = QPainterPath()
            path.addRoundedRect(QRectF(rect).adjusted(2, 1, -2, -1), 6, 6)
            painter.fillPath(path, theme.color("selected"))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            path = QPainterPath()
            path.addRoundedRect(QRectF(rect).adjusted(2, 1, -2, -1), 6, 6)
            painter.fillPath(path, theme.color("hover"))
        size = 16
        painter.drawPixmap(
            rect.left() + 10, rect.center().y() - size // 2 + 1, icons.pixmap(entry.icon, size)
        )
        x = rect.left() + 34
        right = rect.right() - 10
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        metrics = painter.fontMetrics()
        source = entry.source if index.data(SOURCE_ROLE) else ""
        if source:
            width = metrics.horizontalAdvance(source)
            painter.setPen(muted)
            painter.drawText(QRect(right - width, rect.top(), width, rect.height()), flags, source)
            right -= width + 12

        def draw(part: str, color: QColor, bold: bool = False) -> None:
            nonlocal x
            if not part or x >= right:
                return
            font = QFont(option.font)
            if bold:
                font.setWeight(QFont.Weight.Bold)
            painter.setFont(font)
            shown = painter.fontMetrics().elidedText(part, Qt.TextElideMode.ElideRight, right - x)
            painter.setPen(color)
            painter.drawText(QRect(x, rect.top(), right - x, rect.height()), flags, shown)
            x += painter.fontMetrics().horizontalAdvance(shown)

        if index.data(QUALIFIED_ROLE):
            draw(entry.prefix, muted)
        match = (index.data(MATCH_ROLE) or "").lower()
        if match and entry.short.lower().startswith(match):
            draw(entry.short[: len(match)], text, bold=True)
            draw(entry.short[len(match) :], text)
        else:
            draw(entry.short, text)
        if self.descriptions and entry.description:
            x += 10
            draw(entry.description, muted)
        painter.restore()


# -- a component's picture --------------------------------------------------------


def thumbnail(geometry, colors: dict[str, QColor], size: QSize, ratio: float = 2.0) -> QPixmap:
    """``geometry`` drawn to fit ``size`` (centred, a margin around it), each layer
    filled lightly in its colour and outlined, as on the canvas."""
    from mems_sketch.gui.canvas import geometry_path, layer_color  # canvas is heavy

    image = QPixmap(round(size.width() * ratio), round(size.height() * ratio))
    image.fill(Qt.GlobalColor.transparent)
    image.setDevicePixelRatio(ratio)
    box = geometry.bbox() if geometry is not None else None
    if box is None:
        return image
    x0, y0, x1, y1 = box
    width, height = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
    margin = 12
    scale = min((size.width() - 2 * margin) / width, (size.height() - 2 * margin) / height)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    # µm, y up, to pixels, y down, centred
    transform = QTransform()
    transform.translate(size.width() / 2, size.height() / 2)
    transform.scale(scale, -scale)
    transform.translate(-(x0 + x1) / 2, -(y0 + y1) / 2)
    painter.setTransform(transform)
    for index, layer in enumerate(geometry.layer_names()):
        color = QColor(colors.get(layer) or layer_color(index))
        fill = QColor(color)
        fill.setAlphaF(0.35)
        pen = QPen(color, 1.2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(fill)
        painter.drawPath(geometry_path(geometry, layer))
    painter.end()
    return image


# -- the Place popup --------------------------------------------------------------


class PlacePopup(QFrame):
    """A popup listing the components that can be placed, searched by typing.

    Emits ``chosen`` with the component's name, or ``browse`` for the Components
    tab of Search Everywhere."""

    chosen = Signal(str)
    browse = Signal()

    def __init__(self, found: list[Entry], parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        from mems_sketch.gui.style import SHADOW

        self.setObjectName("place-popup")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.entries = found
        self.search = QLineEdit()
        self.search.setObjectName("picker-search")
        self.search.setPlaceholderText("Place a component…")
        self.search.addAction(icons.icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._fill)
        self.search.installEventFilter(self)
        self.list = QListWidget()
        self.list.setObjectName("picker-list")
        self.list.setItemDelegate(RowDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.itemClicked.connect(self._clicked)
        self.list.itemActivated.connect(self._clicked)
        self.list.installEventFilter(self)
        self.nothing = QLabel()
        self.nothing.setObjectName("muted")
        self.nothing.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.nothing.setContentsMargins(0, 10, 0, 10)
        line = QFrame()
        line.setObjectName("picker-line")
        line.setFixedHeight(1)
        self.browse_button = QToolButton()
        self.browse_button.setObjectName("picker-browse")
        self.browse_button.setText("Browse all components")
        icons.bind(self.browse_button, "search")
        self.browse_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.browse_button.setAutoRaise(True)
        self.browse_button.setToolTip("Search Everywhere, on components, with a preview")
        self.browse_button.clicked.connect(self._browse)
        foot = QHBoxLayout()
        foot.setContentsMargins(4, 0, 4, 0)
        foot.addWidget(self.browse_button)
        foot.addStretch(1)
        foot.addWidget(keycap("Tab"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SHADOW + 8, SHADOW + 8, SHADOW + 8, SHADOW + 6)
        layout.setSpacing(6)
        layout.addWidget(self.search)
        layout.addWidget(self.list)
        layout.addWidget(self.nothing)
        layout.addWidget(line)
        layout.addLayout(foot)
        self._fill("")

    # -- the list -----------------------------------------------------------------

    def _fill(self, text: str) -> None:
        self.list.clear()
        text = text.strip()
        if text:
            found = ranked(self.entries, text)
            if found:
                self.list.addItem(
                    heading_item(f"{len(found)} match{'es' if len(found) > 1 else ''}")
                )
            for entry in found:
                self.list.addItem(entry_item(entry, qualified=True, source=True, match=text))
        else:
            by_name = {e.name: e for e in self.entries}
            latest = [by_name[n] for n in recent() if n in by_name][:RECENT_MOST]
            if latest:
                self.list.addItem(heading_item(RECENT))
                for entry in latest:
                    self.list.addItem(entry_item(entry, qualified=True))
            for source in sources(self.entries):
                rows = [e for e in self.entries if e.source == source]
                count = len(rows) if source != THIS_PROJECT else None
                self.list.addItem(heading_item(source, count))
                for entry in rows:
                    self.list.addItem(entry_item(entry))
        rows = self.rows()
        self.nothing.setText(
            f"No component matches “{text}”." if text else "No components to place."
        )
        self.nothing.setVisible(not rows)
        self.list.setVisible(bool(rows))
        if rows:
            self.list.setCurrentItem(rows[0])
        self._fit()

    def rows(self) -> list[QListWidgetItem]:
        return [
            item
            for i in range(self.list.count())
            if (item := self.list.item(i)).data(ENTRY_ROLE) is not None
        ]

    def _fit(self) -> None:
        """As tall as its rows, up to a point; the list scrolls past it."""
        height = 0
        rows = 0
        for i in range(self.list.count()):
            if rows >= POPUP_MAX_ROWS:
                break
            heading = self.list.item(i).data(HEADING_ROLE) is not None
            height += HEADING_HEIGHT if heading else ROW_HEIGHT
            rows += 0 if heading else 1
        self.list.setFixedHeight(max(height, ROW_HEIGHT) + 4)
        self.setFixedWidth(POPUP_WIDTH + 2 * self._shadow())
        self.adjustSize()

    @staticmethod
    def _shadow() -> int:
        from mems_sketch.gui.style import SHADOW

        return SHADOW

    # -- choosing -----------------------------------------------------------------

    def _clicked(self, item: QListWidgetItem) -> None:
        entry = item.data(ENTRY_ROLE)
        if entry is not None:
            self.close()
            self.chosen.emit(entry.name)

    def _browse(self) -> None:
        self.close()
        self.browse.emit()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(watched, event)
        key = event.key()
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            rows = self.rows()
            if rows:
                current = self.list.currentItem()
                index = rows.index(current) if current in rows else -1
                index += 1 if key == Qt.Key.Key_Down else -1
                self.list.setCurrentItem(rows[max(0, min(index, len(rows) - 1))])
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.list.currentItem() is not None:
                self._clicked(self.list.currentItem())
            return True
        if key == Qt.Key.Key_Tab:
            self._browse()
            return True
        if watched is self.list and event.text() and event.text().isprintable():
            self.search.setFocus()
            self.search.event(event)  # typing anywhere in the popup searches
            return True
        return super().eventFilter(watched, event)

    def popup(self, at: QPoint) -> None:
        """Show with its panel's top left corner at ``at`` (global), kept on the screen."""
        shadow = self._shadow()
        self.adjustSize()
        screen = QApplication.screenAt(at) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        x = min(max(at.x() - shadow, area.left()), area.right() - self.width())
        y = min(max(at.y() - shadow, area.top()), area.bottom() - self.height())
        self.move(x, y)
        self.show()
        self.search.setFocus()

    def paintEvent(self, event) -> None:
        from mems_sketch.gui.style import draw_panel

        painter = QPainter(self)
        draw_panel(painter, QRectF(self.rect()), True)
        painter.end()


def keycap(text: str) -> QLabel:
    """A key, as a small rounded label (``Enter``, ``Tab``)."""
    label = QLabel(text)
    label.setObjectName("keycap")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setFixedHeight(KEYCAP_HEIGHT)
    return label


# -- the preview (Search Everywhere's Components tab) -------------------------------


@dataclass
class Info:
    """What the preview shows of a component."""

    name: str
    source: str
    description: str
    parameters: list[tuple[str, str]]  # name, default as written
    points: list[str]
    picture: QPixmap | None = None


PICTURE = QSize(268, 150)


def component_info(document, entry: Entry, colors: dict[str, QColor]) -> Info:
    """A component's preview: its picture (built at its defaults), description,
    parameters with their defaults, and the points it declares."""
    try:
        definition = document.definition_of(entry.name)
        description = " ".join((definition.description or "").split())
        parameters = [
            (p.name, f"{p.default:g}" if isinstance(p.default, float) else str(p.default))
            for p in definition.parameters
        ]
        points = [p.name for p in getattr(definition, "points", [])]
    except Exception:  # noqa: BLE001 - it does not load: say only what is known
        description, parameters, points = entry.description, [], []
    try:
        picture = thumbnail(document.results.geometry(entry.name), colors, PICTURE)
    except Exception:  # noqa: BLE001 - it does not build: no picture
        picture = None
    return Info(entry.name, entry.source, description, parameters, points, picture)


class ComponentPreview(QWidget):
    """A component's picture, description, parameters and points, and the buttons
    that place or open it."""

    place = Signal()
    open = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("component-preview")
        self.picture = QLabel()
        self.picture.setObjectName("preview-picture")
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.picture.setFixedHeight(PICTURE.height() + 10)
        self.title = QLabel()
        self.title.setObjectName("preview-title")
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.description = QLabel()
        self.description.setObjectName("muted")
        self.description.setWordWrap(True)
        self.details = QLabel()
        self.details.setTextFormat(Qt.TextFormat.RichText)
        self.details.setWordWrap(True)
        self.details.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        scroll = QScrollArea()
        scroll.setObjectName("preview-scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("preview-body")
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)
        column.addWidget(self.title)
        column.addWidget(self.description)
        column.addSpacing(4)
        column.addWidget(self.details)
        column.addStretch(1)
        scroll.setWidget(body)
        self.open_button = QPushButton("Open")
        self.open_button.setToolTip("Open it in its own tab (Ctrl+Enter)")
        self.open_button.setAutoDefault(False)
        self.open_button.clicked.connect(self.open)
        self.place_button = QPushButton("Place")
        self.place_button.setAutoDefault(False)
        self.place_button.setDefault(True)  # drawn as the main button; Enter is the search's
        self.place_button.setToolTip("Place it: it follows the cursor until you click (Enter)")
        self.place_button.clicked.connect(self.place)
        # The keys and the buttons, for the dialog's bottom row.
        self.footer = QWidget()
        row = QHBoxLayout(self.footer)
        row.setContentsMargins(4, 0, 0, 0)
        row.setSpacing(6)
        for keys, what in (("Enter", "Place"), ("Ctrl+Enter", "Open")):
            row.addWidget(keycap(keys))
            label = QLabel(what)
            label.setObjectName("muted")
            row.addWidget(label)
            row.addSpacing(10)
        row.addStretch(1)
        row.addWidget(self.open_button)
        row.addWidget(self.place_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self.picture)
        layout.addWidget(scroll, 1)

    def show_info(self, info: Info | None, can_place: bool = True) -> None:
        self.setEnabled(info is not None)
        if info is None:
            self.picture.clear()
            self.title.clear()
            self.description.clear()
            self.details.clear()
            return
        if info.picture is not None and not info.picture.isNull():
            self.picture.setPixmap(info.picture)
        else:
            self.picture.setText("No picture")
        muted = theme.color("muted").name()
        name = info.name.rsplit(".", 1)[-1]
        self.title.setText(
            f"<span style='font-size:15px; font-weight:600'>{_html(name)}</span>"
            f"&nbsp;&nbsp;<span style='color:{muted}'>{_html(info.source.lower() if info.source == BUILT_IN else info.source)}</span>"
        )
        self.description.setText(info.description or "No description.")
        rows = []
        if info.parameters:
            rows.append(f"<p style='color:{muted}; font-weight:600; margin:0'>Parameters</p>")
            rows.append("<table width='100%' cellspacing='0' cellpadding='1'>")
            rows += [
                f"<tr><td>{_html(n)}</td><td align='right' style='color:{muted}'>{_html(v)}</td></tr>"
                for n, v in info.parameters
            ]
            rows.append("</table>")
        if info.points:
            rows.append(
                f"<p style='color:{muted}; font-weight:600; margin:8px 0 0 0'>Points</p>"
                f"<p style='margin:0'>{_html(', '.join(info.points))}</p>"
            )
        self.details.setText("".join(rows))
        self.place_button.setEnabled(can_place)


def _html(text: str) -> str:
    return html.escape(text)
