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
* When the field is not being edited, a text too long for it is shown from
  its start and cut with "…" (the value only if there is room left); the
  whole text is in the tooltip and back while editing. The field may shrink,
  so long names never widen the panel.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QPoint, QRect, QSize, QStringListModel, Qt, Signal
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import QCompleter, QLineEdit, QMenu, QStyle, QStyleOptionFrame

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


class ValueEdit(QLineEdit):
    """See the module docstring. ``scope`` holds the names an expression may use."""

    make_parameter = Signal(object)  # the field's current value: make a parameter of it

    def __init__(
        self,
        value=None,
        scope: dict[str, float] | None = None,
        parameters: list[str] | None = None,
        prefix: str = "",
        optional: bool = False,
    ) -> None:
        super().__init__("" if value is None else _format(value))
        self.scope = dict(scope or {})
        self.parameters = list(parameters or [])  # the component's own, listed first
        self.prefix = prefix
        self.optional = optional
        self._hint = ""
        self.setProperty("expression", False)
        self.setProperty("invalid", False)
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
        self._update()

    # -- reading -------------------------------------------------------------

    def value(self) -> float | str | None:
        """A number, an expression, or None (empty and optional)."""
        text = self.text().strip()
        if not text:
            if self.optional:
                return None
            raise ValueError("a value is required")
        return float(text) if is_number(text) else text

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

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout()

    def enterEvent(self, event) -> None:
        self._bind.setVisible(self.isEnabled() and not self.isReadOnly())
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if not self.hasFocus():
            self._bind.setVisible(False)
        super().leaveEvent(event)

    def focusOutEvent(self, event) -> None:
        self._bind.setVisible(False)
        super().focusOutEvent(event)
        self.setCursorPosition(0)  # next time, start at the beginning
        self._layout()

    # -- names -------------------------------------------------------------------

    def _names(self) -> list[str]:
        known = [*self.parameters, *sorted(n for n in self.scope if n not in self.parameters)]
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
            menu.addSection("Parameters")
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
        menu.addSeparator()
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
