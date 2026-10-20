"""A field for a number or an expression, used wherever a value can be parametric.

* A plain number looks like any field. An expression (``pitch * 2``) is tinted
  and shows what it comes to, small and grey inside the field on the right.
  If it cannot be evaluated the field is marked and the tooltip says why.
* Typing a name offers the matching parameters, process constants, points
  (``mass.center.x``) and ``i``/``j``, with their values.
* The parameter button (shown on hover) lists the parameters to use one,
  makes a new parameter from the current value, or replaces an expression by
  its value.
* An optional prefix (``x``, ``y``) is drawn inside the field, so pairs such
  as x and y can sit side by side.
* A number can be dragged, as in Blender: press on the field and drag left or
  right to decrease or increase it (Shift: finer, Ctrl: in round steps). While
  dragging, :attr:`ValueEdit.scrubbed` reports each value (for a live
  preview) and :attr:`ValueEdit.scrub_finished` the last one. A click without
  dragging edits the text. An empty optional field starts from the number in
  its placeholder (the default); an expression is not dragged.
* When the field is not being edited, a text too long for it is shown from
  its start and cut with "…" (the value only if there is room left); the
  whole text is in the tooltip and back while editing. The field may shrink,
  so long names never widen the panel.
* Editing a text too long for the field (or typing past its end, or
  Shift+Enter) opens :class:`ExpandedEdit` over it: as wide as the panel,
  wrapping onto more lines, with the same completion. Enter applies, as in the
  field; Esc leaves the field as it was; clicking elsewhere applies it, as
  leaving a field of the Properties panel does.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence

import shiboken6
from PySide6.QtCore import QPoint, QRect, QSize, QStringListModel, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPalette, QTextCursor, QTextOption
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QCompleter,
    QLineEdit,
    QMenu,
    QStyle,
    QStyleOptionFrame,
    QTextEdit,
)

from mems_sketch.core.expressions import evaluate
from mems_sketch.gui import icons

_NAME_BEFORE_CURSOR = re.compile(r"[A-Za-z_][\w.]*$")


def _format(value) -> str:
    return f"{value:g}" if isinstance(value, float | int) else str(value)


def is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def number_or_expression(text: str) -> float | str:
    """A number if ``text`` is one, else the text as an expression; ``nan``,
    ``inf`` and numbers too big for a float (``1e999``) are refused."""
    if not is_number(text):
        return text
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"'{text}' is not a finite number")
    return value


class ElidedLineEdit(QLineEdit):
    """A line edit that, when not being edited, shows a text too long for it from
    its start, cut with "…" (the whole text is in the tooltip and back while
    editing). It may shrink, so a long text never widens its panel."""

    def __init__(self, text: str = "", tip: str = "") -> None:
        super().__init__(text)
        self.tip = tip  # shown in the tooltip, after the text when that is cut
        self.textChanged.connect(self._changed)
        self._changed()

    def _changed(self) -> None:
        self._layout()
        self.update()

    def _text_rect(self) -> QRect:
        """Where the text is drawn, as QLineEdit does it (without the text margins)."""
        option = QStyleOptionFrame()
        self.initStyleOption(option)
        rect = self.style().subElementRect(QStyle.SubElement.SE_LineEditContents, option, self)
        return rect.adjusted(2, 0, -2, 0)

    def _elided(self) -> bool:
        width = self.fontMetrics().horizontalAdvance(self.text())
        return not self.hasFocus() and width > self._text_rect().width()

    def _layout(self) -> None:
        """While elided, the field's own text is pushed out of view (painted cut instead)."""
        elided = self._elided()
        right = self.width() if elided else 0
        if self.textMargins().right() != right:
            self.setTextMargins(0, 0, right, 0)
        self.setToolTip("\n".join(t for t in (self.text() if elided else "", self.tip) if t))

    def minimumSizeHint(self) -> QSize:
        return QSize(48, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:  # not widened by the pushed-out text
        return QSize(120, super().sizeHint().height())

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._elided():
            painter = QPainter(self)
            painter.setPen(self.palette().color(QPalette.ColorRole.Text))
            rect = self._text_rect()
            shown = self.fontMetrics().elidedText(
                self.text(), Qt.TextElideMode.ElideRight, rect.width()
            )
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignVCenter), shown)
            painter.end()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._layout()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.setCursorPosition(0)
        self._layout()


DRAG_START_PX = 3  # a smaller move is a click
PIXELS_PER_STEP = 8  # of a whole number


def drag_step(value: float, integer: bool = False) -> float:
    """How far one pixel of dragging moves ``value``: finer for small numbers."""
    if integer:
        return 1 / PIXELS_PER_STEP
    magnitude = abs(value) if value else 1.0
    return max(10 ** math.floor(math.log10(magnitude)) / 50, 0.001)


def dragged_value(
    start: float,
    dx: float,
    modifiers=Qt.KeyboardModifier.NoModifier,
    integer: bool = False,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    """``start`` dragged ``dx`` pixels (Shift: ten times finer, Ctrl: round steps)."""
    step = drag_step(start, integer)
    if modifiers & Qt.KeyboardModifier.ShiftModifier:
        step /= 10
    value = start + dx * step
    if integer:
        value = round(value)
    elif modifiers & Qt.KeyboardModifier.ControlModifier:
        value = round(value / (step * 50)) * step * 50
    else:
        value = round(value / step) * step
    if minimum is not None:
        value = max(value, minimum)
    if maximum is not None:
        value = min(value, maximum)
    return round(value, 9)


class ValueEdit(QLineEdit):
    """See the module docstring. ``scope`` holds the names an expression may use."""

    make_parameter = Signal(object)  # the field's current value: make a parameter of it
    scrubbed = Signal(float)  # each value while it is dragged
    scrub_finished = Signal(float)  # the value when the drag ends

    def __init__(
        self,
        value=None,
        scope: dict[str, float] | None = None,
        parameters: list[str] | None = None,
        prefix: str = "",
        optional: bool = False,
        offered: Sequence[str] = (),
    ) -> None:
        super().__init__("" if value is None else _format(value))
        self.scope = dict(scope or {})
        self.offered = list(offered)  # completed too, though without a value here
        self.parameters = list(parameters or [])  # the component's own, listed first
        self.prefix = prefix
        self.optional = optional
        self.integer = False  # whole numbers only (counts), when dragged
        self.minimum: float | None = None
        self.maximum: float | None = None
        self._press: tuple[float, float] | None = None  # x and value where a drag began
        self._scrubbing = False
        self.expanded: ExpandedEdit | None = None  # the wider editor, while open
        self._back_from_expanded = False  # focus returning from it: do not open it again
        self._hint = ""
        self.setProperty("expression", False)
        self.setProperty("invalid", False)
        # A click must not focus the field before it sees the press (Qt does that for
        # click focus): the press may start a drag. It takes focus itself for an edit.
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._model = QStringListModel(self._names(), self)  # kept: the completer does not own it
        self._completer = QCompleter(self._model, self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.setWidget(self)
        self._completer.activated.connect(self._complete)
        self._bind = self.addAction(
            icons.icon("parameter"), QLineEdit.ActionPosition.TrailingPosition
        )
        self._bind.setToolTip("Use a parameter, or make one from this value")
        self._bind.triggered.connect(self._bind_menu)
        self._bind.setVisible(False)
        self.textChanged.connect(self._update)
        self.textEdited.connect(self._suggest)
        self.textEdited.connect(self._overflowed)
        self._update()

    # -- reading -------------------------------------------------------------

    def value(self) -> float | str | None:
        """A number, an expression, or None (empty and optional)."""
        text = self.text().strip()
        if not text:
            if self.optional:
                return None
            raise ValueError("a value is required")
        return number_or_expression(text)

    # -- looks ------------------------------------------------------------------

    def _update(self) -> None:
        text = self.text().strip()
        expression = bool(text) and not is_number(text)
        self._hint, invalid, tip = "", False, ""
        if expression:
            try:
                self._hint = _format(round(evaluate(text, self.scope), 9))
                tip = f"{text} = {self._hint}"
            except Exception as exc:  # noqa: BLE001 - shown on the field
                invalid, tip = True, f"cannot evaluate: {exc}"
        elif not text and self.optional:
            tip = "empty: the default"
        if text and not tip:
            tip = text
        self.setToolTip(tip)
        if self.property("expression") != expression or self.property("invalid") != invalid:
            self.setProperty("expression", expression)
            self.setProperty("invalid", invalid)
            self.style().unpolish(self)
            self.style().polish(self)
        self._layout()
        self.update()

    def _elided(self) -> bool:
        """The text is too long and not being edited: :meth:`paintEvent` draws it cut."""
        return not self.hasFocus() and not self._fits(with_hint=False)

    def _layout(self) -> None:
        """Room for the prefix, and for the value hint if it fits. While the text is
        elided the field's own text is pushed out of view (painted cut instead)."""
        metrics = self.fontMetrics()
        left = metrics.horizontalAdvance(self.prefix) + 6 if self.prefix else 0
        right = 0
        if self._elided():
            right = self.width()
        elif self._hint and self._fits(with_hint=True):
            right = metrics.horizontalAdvance(self._hint) + 6
        if self.textMargins().left() != left or self.textMargins().right() != right:
            self.setTextMargins(left, 0, right, 0)

    def _room(self) -> QRect:
        rect = self.contentsRect().adjusted(7, 0, -7, 0)
        if self._bind.isVisible():
            rect.setRight(rect.right() - 20)
        return rect

    def _fits(self, with_hint: bool) -> bool:
        metrics = self.fontMetrics()
        needed = metrics.horizontalAdvance(self.text())
        if self.prefix:
            needed += metrics.horizontalAdvance(self.prefix) + 6
        if with_hint:
            needed += metrics.horizontalAdvance(self._hint) + 8
        return needed <= self._room().width()

    def minimumSizeHint(self) -> QSize:
        return QSize(48, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:
        return QSize(80, super().sizeHint().height())

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        muted = self.palette().placeholderText().color()
        rect = self._room()
        middle = Qt.AlignmentFlag.AlignVCenter
        metrics = self.fontMetrics()
        if self.prefix:
            painter.setPen(muted)
            painter.drawText(rect, int(middle | Qt.AlignmentFlag.AlignLeft), self.prefix)
            rect.setLeft(rect.left() + metrics.horizontalAdvance(self.prefix) + 6)
        hint = self._hint if self._fits(with_hint=True) else ""
        if hint:
            painter.setPen(muted)
            painter.drawText(rect, int(middle | Qt.AlignmentFlag.AlignRight), hint)
        if self._elided():
            painter.setPen(self.palette().color(QPalette.ColorRole.Text))
            shown = metrics.elidedText(self.text(), Qt.TextElideMode.ElideRight, rect.width())
            painter.drawText(rect, int(middle | Qt.AlignmentFlag.AlignLeft), shown)
        painter.end()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._layout()
        self._update_cursor()
        if self._back_from_expanded:
            self._back_from_expanded = False
        elif not self._fits(with_hint=False):
            QTimer.singleShot(0, self.expand)  # after the click has placed the cursor

    def expand(self) -> None:
        """Edit in :class:`ExpandedEdit`, over the field and as wide as its panel."""
        if self.isReadOnly() or self.expanded is not None or not self.hasFocus():
            return
        self.expanded = ExpandedEdit(self)
        self.expanded.open()

    def keyPressEvent(self, event) -> None:
        enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if enter and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.expand()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout()

    # -- dragging the value -------------------------------------------------------

    def _number(self) -> float | None:
        """The number shown (or the default in the placeholder), if it can be dragged."""
        if self.isReadOnly() or not self.isEnabled():
            return None
        text = self.text().strip() or self.placeholderText().strip()
        return float(text) if is_number(text) and math.isfinite(float(text)) else None

    def _dragged(self, dx: float, modifiers) -> float:
        return dragged_value(
            self._press[1], dx, modifiers, self.integer, self.minimum, self.maximum
        )

    def mousePressEvent(self, event) -> None:
        number = None if self.hasFocus() else self._number()
        if event.button() != Qt.MouseButton.LeftButton or number is None:
            if not self.hasFocus():
                self.setFocus(Qt.FocusReason.MouseFocusReason)
            super().mousePressEvent(event)
            return
        self._press = (event.position().x(), number)  # a drag, or a click to edit
        self._scrubbing = False
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._press is None:
            super().mouseMoveEvent(event)
            return
        dx = event.position().x() - self._press[0]
        if not self._scrubbing and abs(dx) < DRAG_START_PX:
            return
        self._scrubbing = True
        value = self._dragged(dx, event.modifiers())
        self.setText(_format(value))
        self.scrubbed.emit(value)

    def mouseReleaseEvent(self, event) -> None:
        if self._press is None:
            super().mouseReleaseEvent(event)
            return
        self._press = None
        if self._scrubbing:
            self._scrubbing = False
            value = float(self.text())
            self.scrub_finished.emit(value)
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)  # a click: edit the text
        self.selectAll()

    def _update_cursor(self) -> None:
        dragging = not self.hasFocus() and self._number() is not None
        self.setCursor(Qt.CursorShape.SizeHorCursor if dragging else Qt.CursorShape.IBeamCursor)

    def enterEvent(self, event) -> None:
        self._bind.setVisible(self.isEnabled() and not self.isReadOnly())
        self._update_cursor()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if not self.hasFocus():
            self._bind.setVisible(False)
        super().leaveEvent(event)

    def focusOutEvent(self, event) -> None:
        if not shiboken6.isValid(self._bind):  # the field is being destroyed
            super().focusOutEvent(event)
            return
        self._bind.setVisible(False)
        super().focusOutEvent(event)
        self.setCursorPosition(0)  # next time, start at the beginning
        self._layout()
        self._update_cursor()

    def _overflowed(self) -> None:
        """Typing past the end of the field continues in the wider editor."""
        if not self._fits(with_hint=False):
            self.expand()

    # -- names -------------------------------------------------------------------

    def _names(self) -> list[str]:
        known = [
            *self.parameters,
            *self.offered,
            *sorted(n for n in self.scope if n not in self.parameters),
        ]
        return [n for n in dict.fromkeys(known) if n.isidentifier() or "." in n]

    def _token(self) -> tuple[int, str]:
        """The name being typed before the cursor: its start and text."""
        before = self.text()[: self.cursorPosition()]
        match = _NAME_BEFORE_CURSOR.search(before)
        return (match.start(), match.group()) if match else (len(before), "")

    def _suggest(self) -> None:
        _, token = self._token()
        if not token or is_number(token):
            self._completer.popup().hide()
            return
        self._completer.setCompletionPrefix(token)
        if self._completer.completionCount() == 0:
            self._completer.popup().hide()
            return
        width = max(220, self.width())
        self._completer.complete(QRect(0, 0, width, self.height()))

    def _complete(self, name: str) -> None:
        start, token = self._token()
        text = self.text()
        end = start + len(token)
        self.setText(text[:start] + name + text[end:])
        self.setCursorPosition(start + len(name))

    # -- the parameter button ---------------------------------------------------------

    def _bind_menu(self) -> None:
        menu = self.parameter_menu()
        corner = QPoint(max(0, self.width() - menu.sizeHint().width()), self.height())
        menu.exec(self.mapToGlobal(corner))

    def parameter_menu(self) -> QMenu:
        """The parameter button's menu: use a parameter, make one, or use the value."""
        menu = QMenu(self)
        if self.parameters:
            for name in self.parameters:
                shown = f"{name}  = {_format(self.scope[name])}" if name in self.scope else name
                action = menu.addAction(shown)
                action.triggered.connect(lambda _=False, n=name: self._use(n))
        constants = sorted(n for n in self.scope if n.startswith("process."))
        if constants:
            sub = menu.addMenu("Process constants")
            for name in constants:
                action = sub.addAction(f"{name}  = {_format(self.scope[name])}")
                action.triggered.connect(lambda _=False, n=name: self._use(n))
        if not menu.isEmpty():
            menu.addSeparator()  # a line only between groups
        make = menu.addAction(icons.icon("add"), "Make a parameter from this value…")
        make.triggered.connect(self._make_parameter)
        make.setEnabled(bool(self.text().strip()))
        if self._hint:
            value = menu.addAction(f"Use the value ({self._hint})")
            value.triggered.connect(lambda: self._use(self._hint))
        return menu

    def _use(self, text: str) -> None:
        self.setText(text)
        self.returnPressed.emit()  # apply, like Enter

    def _make_parameter(self) -> None:
        try:
            value = self.value()
        except ValueError:
            return
        self.make_parameter.emit(value)


class ExpandedEdit(QTextEdit):
    """A wider editor for a :class:`ValueEdit` whose text does not fit it (see the
    module docstring). It opens over the field, as wide as the panel, and grows a
    line at a time as the text wraps."""

    MAX_LINES = 8

    def __init__(self, field: ValueEdit) -> None:
        super().__init__(field, Qt.WindowType.Popup)
        self.setObjectName("expanded-value")
        self.field = field
        self._done = False
        self.setAcceptRichText(False)
        self.setTabChangesFocus(True)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setPlainText(field.text())
        cursor = self.textCursor()
        if field.hasSelectedText():  # a click selects all: keep it selected
            cursor.setPosition(field.selectionStart())
            cursor.setPosition(field.selectionEnd(), QTextCursor.MoveMode.KeepAnchor)
        else:
            cursor.setPosition(field.cursorPosition())
        self.setTextCursor(cursor)
        self._completer = QCompleter(field._model, self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.setWidget(self)
        self._completer.activated.connect(self._complete)
        self.document().contentsChanged.connect(self._fit)

    def open(self) -> None:
        field = self.field
        at = field.mapToGlobal(QPoint(0, 0))
        left, width = at.x(), max(field.width(), 320)
        panel = field.parentWidget()
        while panel is not None and not isinstance(panel, QAbstractScrollArea):
            panel = panel.parentWidget()
        if panel is not None:  # the panel's width, less its edges
            corner = panel.viewport().mapToGlobal(QPoint(0, 0))
            left, width = corner.x() + 4, max(panel.viewport().width() - 8, field.width())
        self.setGeometry(left, at.y(), width, field.height())
        self.show()
        self.setFocus()
        self._fit()

    def _fit(self) -> None:
        """As tall as its lines, up to :attr:`MAX_LINES`."""
        self.document().setTextWidth(self.viewport().width())
        line = self.fontMetrics().lineSpacing()
        content = min(self.document().size().height(), line * self.MAX_LINES + 8)
        frame = self.height() - self.viewport().height()
        self.resize(self.width(), max(self.field.height(), round(content) + frame))

    def text(self) -> str:
        return " ".join(self.toPlainText().split("\n"))

    def keyPressEvent(self, event) -> None:
        key = event.key()
        enter = key in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if self._completer.popup().isVisible() and (enter or key == Qt.Key.Key_Escape):
            event.ignore()  # the list of names takes it
            return
        if enter:
            self.finish(apply=True)
            return
        if key == Qt.Key.Key_Escape:
            self.finish(keep=False)
            return
        super().keyPressEvent(event)
        if event.text():
            self._suggest()

    def finish(self, apply: bool = False, keep: bool = True) -> None:
        """Close: ``keep`` the text in the field, and ``apply`` it as Enter does."""
        if self._done:
            return
        self._done = True
        field = self.field
        if not shiboken6.isValid(field):  # the panel was rebuilt under it
            return
        if keep:
            field.setText(self.text())
        field.expanded = None
        field._back_from_expanded = True
        self.close()
        field.setFocus(Qt.FocusReason.OtherFocusReason)
        if apply:
            field.returnPressed.emit()

    def hideEvent(self, event) -> None:  # clicked elsewhere: the popup closes
        self.finish(apply=True)  # as leaving a field does
        super().hideEvent(event)

    def _token(self) -> tuple[int, str]:
        cursor = self.textCursor().position()
        before = self.toPlainText()[:cursor]
        match = _NAME_BEFORE_CURSOR.search(before)
        return (match.start(), match.group()) if match else (len(before), "")

    def _suggest(self) -> None:
        _, token = self._token()
        if not token or is_number(token):
            self._completer.popup().hide()
            return
        self._completer.setCompletionPrefix(token)
        if self._completer.completionCount() == 0:
            self._completer.popup().hide()
            return
        rect = self.cursorRect()
        self._completer.complete(QRect(rect.left(), rect.top(), 240, rect.height()))

    def _complete(self, name: str) -> None:
        start, token = self._token()
        cursor = self.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(start + len(token), QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(name)
        self.setTextCursor(cursor)
