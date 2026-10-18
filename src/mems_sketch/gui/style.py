"""Menus drawn by hand, in the theme's colours.

A style sheet can't colour a menu's shortcuts apart from its labels, draw a
check box beside an option that is off, or round a popup without a square
window behind it, so menus are drawn here, in the theme's colours, and the
style sheet leaves them alone (a ``QMenu`` background rule would make Qt draw
them itself). A menu is a translucent window with a soft shadow around a
rounded panel; :class:`MenuPolisher` gives every menu :class:`MenuStyle` and
makes it such a window before it first shows. Only menus have this style: a
style written in Python is called for everything it draws, and the rest of the
window is drawn faster by Fusion alone.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QMenu,
    QProxyStyle,
    QStyle,
    QStyleOptionMenuItem,
    QWidget,
)

from mems_sketch.gui import theme
from mems_sketch.gui.widgets import draw_qualified

SHADOW = 8  # px around a menu's panel, transparent but for its shadow
PAD = 5  # px between a menu's panel and its entries
ITEM_LEFT = 8  # px before an entry's check box, icon or label
ITEM_RIGHT = 12  # px after its shortcut or arrow
COLUMN_GAP = 8  # px between an entry's check box, icon and label
SHORTCUT_GAP = 28  # px at least between a label and its shortcut
INDICATOR = 14  # px: a check box's side
ARROW = 12  # px for a submenu's arrow
# A menu with this property set lists qualified names (std.perforated_plate):
# where each is from is drawn muted.
QUALIFIED = "qualified_names"

_PE = QStyle.PrimitiveElement
_CE = QStyle.ControlElement
_PM = QStyle.PixelMetric
_KIND = QStyleOptionMenuItem.MenuItemType
_CHECK = QStyleOptionMenuItem.CheckType


def _ui(key: str) -> QColor:
    return QColor(theme.current().ui[key])


class MenuStyle(QProxyStyle):
    """Fusion, with menus in the theme's look (a menu's style, not the application's)."""

    def __init__(self) -> None:
        super().__init__("Fusion")

    # -- sizes -------------------------------------------------------------
    def pixelMetric(self, metric, option=None, widget=None) -> int:
        if metric in (_PM.PM_MenuHMargin, _PM.PM_MenuVMargin):
            return PAD
        if metric == _PM.PM_MenuPanelWidth:
            return 1
        if metric == _PM.PM_SubMenuOverlap:
            # A submenu's panel meets its menu's: their shadows overlap.
            return SHADOW + PAD - 2 if isinstance(widget, QMenu) else 0
        return super().pixelMetric(metric, option, widget)

    def sizeFromContents(self, kind, option, size, widget=None) -> QSize:
        if kind != QStyle.ContentsType.CT_MenuItem or not isinstance(option, QStyleOptionMenuItem):
            return super().sizeFromContents(kind, option, size, widget)
        if option.menuItemType == _KIND.Separator:
            return QSize(size.width(), 9)
        width = ITEM_LEFT + _columns(option) + size.width() + ITEM_RIGHT
        if "\t" in option.text:
            width += SHORTCUT_GAP
        if option.menuItemType == _KIND.SubMenu:
            width += ARROW + COLUMN_GAP
        return QSize(width, max(size.height() + 10, theme.metric("row_height")))

    # -- drawing -----------------------------------------------------------
    def drawPrimitive(self, element, option, painter, widget=None) -> None:
        if element == _PE.PE_PanelMenu and isinstance(widget, QMenu):
            draw_panel(painter, QRectF(widget.rect()), _shadowed(widget))
            return
        if element == _PE.PE_FrameMenu and isinstance(widget, QMenu):
            return  # the panel has its border
        super().drawPrimitive(element, option, painter, widget)

    def drawControl(self, element, option, painter, widget=None) -> None:
        if element == _CE.CE_MenuItem and isinstance(option, QStyleOptionMenuItem):
            _draw_item(painter, option, bool(widget is not None and widget.property(QUALIFIED)))
            return
        if element in (_CE.CE_MenuEmptyArea, _CE.CE_MenuHMargin, _CE.CE_MenuVMargin):
            return  # the panel is drawn already
        super().drawControl(element, option, painter, widget)


def _shadowed(menu: QMenu) -> bool:
    return bool(menu.property("mems_shadow"))


def _columns(option: QStyleOptionMenuItem) -> int:
    """The width of an entry's icon column, with its gap. An option's check box
    takes its icon's place: whether it is on says more than the icon."""
    if option.maxIconWidth or option.menuHasCheckableItems:
        return max(INDICATOR, theme.metric("icon_size")) + COLUMN_GAP
    return 0


def draw_panel(painter: QPainter, rect: QRectF, shadowed: bool) -> None:
    """A menu's rounded panel, and its shadow when it has room for one."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    radius = theme.metric("radius_popup")
    if shadowed:
        panel = rect.adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW)
        dark = theme.current().dark
        painter.setPen(Qt.PenStyle.NoPen)
        for step in range(SHADOW, 0, -1):  # a blur by layers, darker inwards, a little low
            shade = QColor(0, 0, 0, round((60 if dark else 26) * (1 - step / SHADOW) ** 2))
            painter.setBrush(shade)
            grown = panel.adjusted(-step, -step + 2, step, step + 2)
            painter.drawRoundedRect(grown, radius + step, radius + step)
    else:
        panel = rect
    painter.setBrush(_ui("editor"))
    painter.setPen(QPen(_ui("border_strong"), 1))
    painter.drawRoundedRect(panel.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
    painter.restore()


def _draw_item(painter: QPainter, option: QStyleOptionMenuItem, qualified: bool) -> None:
    kind = option.menuItemType
    rect = option.rect
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if kind == _KIND.Separator:
        y = rect.center().y() + 0.5
        painter.setPen(QPen(_ui("border_strong"), 1))
        painter.drawLine(QPointF(rect.left() + 6, y), QPointF(rect.right() - 6, y))
        painter.restore()
        return
    if kind not in (_KIND.Normal, _KIND.SubMenu, _KIND.DefaultItem):
        painter.restore()
        return
    enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
    selected = enabled and bool(option.state & QStyle.StateFlag.State_Selected)
    if selected:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_ui("selected"))
        radius = theme.metric("radius_control")
        painter.drawRoundedRect(QRectF(rect), radius, radius)

    x = rect.left() + ITEM_LEFT
    middle = rect.center().y()
    column = _columns(option)
    if column:
        size = column - COLUMN_GAP
        if option.checkType != _CHECK.NotCheckable:
            left = x + (size - INDICATOR) / 2
            box = QRectF(left, middle - INDICATOR / 2 + 1, INDICATOR, INDICATOR)
            _draw_indicator(painter, box, option.checked, option.checkType, enabled)
        elif not option.icon.isNull():
            mode = QIcon.Mode.Normal if enabled else QIcon.Mode.Disabled
            ratio = painter.device().devicePixelRatioF()
            pixmap = option.icon.pixmap(QSize(size, size), ratio, mode)
            painter.drawPixmap(QPoint(x, middle - size // 2 + 1), pixmap)
        x += column

    label, _tab, shortcut = option.text.partition("\t")
    painter.setFont(option.font)
    text = _ui("text") if enabled else _ui("muted")
    right = rect.left() + rect.width() - ITEM_RIGHT
    if kind == _KIND.SubMenu:
        _draw_arrow(painter, QPointF(right - ARROW / 2 + 2, middle + 0.5), _ui("muted"))
        right -= ARROW + COLUMN_GAP
    line = Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine
    if shortcut:
        painter.setPen(_ui("muted"))
        painter.drawText(
            QRect(x, rect.top(), right - x, rect.height()),
            int(line | Qt.AlignmentFlag.AlignRight),
            shortcut,
        )
    room = QRect(x, rect.top(), right - x, rect.height())
    if qualified:
        draw_qualified(painter, room, label.replace("&&", "&"), text, _ui("muted"))
    else:
        painter.setPen(text)
        flags = line | Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextHideMnemonic
        painter.drawText(room, int(flags), label)
    painter.restore()


def _draw_indicator(
    painter: QPainter, box: QRectF, checked: bool, kind: _CHECK, enabled: bool
) -> None:
    """A check box (or a round one, for one of a set) beside a menu option."""
    on = _ui("accent")
    if not enabled:
        on = _ui("muted")
    radius = INDICATOR / 2 if kind == _CHECK.Exclusive else theme.metric("radius_small")
    inner = box.adjusted(0.5, 0.5, -0.5, -0.5)
    if checked:
        painter.setPen(QPen(on, 1))
        painter.setBrush(on)
    else:
        painter.setPen(QPen(_ui("input_border"), 1))
        painter.setBrush(_ui("editor"))
    painter.drawRoundedRect(inner, radius, radius)
    if not checked:
        return
    mark = QPen(_ui("accent_text"), 1.8)
    mark.setCapStyle(Qt.PenCapStyle.RoundCap)
    mark.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(mark)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if kind == _CHECK.Exclusive:
        painter.setBrush(_ui("accent_text"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(box.center(), 3, 3)
        return
    s = INDICATOR / 16
    path = QPainterPath(QPointF(box.left() + 3.8 * s, box.top() + 8.2 * s))
    path.lineTo(box.left() + 6.6 * s, box.top() + 11 * s)
    path.lineTo(box.left() + 12.2 * s, box.top() + 5.2 * s)
    painter.drawPath(path)


def _draw_arrow(painter: QPainter, center: QPointF, color: QColor) -> None:
    pen = QPen(color, 1.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath(center + QPointF(-1.5, -3.5))
    path.lineTo(center + QPointF(1.5, 0))
    path.lineTo(center + QPointF(-1.5, 3.5))
    painter.drawPath(path)


class MenuPolisher(QObject):
    """Installed on the application: makes each menu a translucent window with room
    for its shadow before it first shows, and moves a menu that opens at the cursor
    or below a button so its panel, not its shadow, starts there."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if kind in (QEvent.Type.Polish, QEvent.Type.Paint) and _is_combo_popup(watched):
            return _combo_popup(watched, event)
        if not isinstance(watched, QMenu):
            return False
        if event.type() == QEvent.Type.Polish and not watched.property("mems_menu"):
            watched.setProperty("mems_menu", True)
            if not watched.testAttribute(Qt.WidgetAttribute.WA_WState_Created):
                watched.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
                watched.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint)
                watched.setContentsMargins(SHADOW, SHADOW, SHADOW, SHADOW)
                watched.setProperty("mems_shadow", True)
            watched.setStyle(menu_style())
        elif event.type() == QEvent.Type.Show and _shadowed(watched) and not _is_submenu(watched):
            watched.move(watched.pos() - QPoint(SHADOW, SHADOW - 2))
        return False


COMBO_PAD = 4  # px between a list's rounded panel and its entries


def _is_combo_popup(widget: QObject) -> bool:
    """The window a combo box's list opens in (Qt's private container)."""
    return isinstance(widget, QWidget) and widget.metaObject().className() == (
        "QComboBoxPrivateContainer"
    )


def _combo_popup(container: QWidget, event: QEvent) -> bool:
    """A combo box's list as a rounded panel like a menu's, with nothing square
    behind its corners: a translucent window, its list drawn on the panel."""
    if event.type() == QEvent.Type.Polish:
        if not container.property("mems_combo"):
            container.setProperty("mems_combo", True)
            if not container.testAttribute(Qt.WidgetAttribute.WA_WState_Created):
                container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
                container.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint)
                container.setProperty("mems_translucent", True)
            container.setContentsMargins(COMBO_PAD, COMBO_PAD, COMBO_PAD, COMBO_PAD)
            for view in container.findChildren(QAbstractItemView):
                view.viewport().setAutoFillBackground(False)
        return False
    painter = QPainter(container)
    rect = QRectF(container.rect())
    if container.property("mems_translucent"):
        draw_panel(painter, rect, False)
    else:  # no translucency (a window made before): a plain panel to the edges
        painter.fillRect(rect, _ui("editor"))
        painter.setPen(QPen(_ui("border_strong"), 1))
        painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
    painter.end()
    return True


def _is_submenu(menu: QMenu) -> bool:
    """Whether ``menu`` opens from an entry of another open menu."""
    return any(
        isinstance(owner, QMenu) and owner is not menu and owner.isVisible()
        for owner in menu.menuAction().associatedObjects()
    )


_style: MenuStyle | None = None


def menu_style() -> MenuStyle:
    """The one style every menu shares (kept here: a widget does not own its style)."""
    global _style
    if _style is None:
        _style = MenuStyle()
    return _style
