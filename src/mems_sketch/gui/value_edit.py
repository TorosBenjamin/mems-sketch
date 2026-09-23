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
"""

from __future__ import annotations

import re

from PySide6.QtCore import QPoint, QRect, QStringListModel, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QCompleter, QLineEdit, QMenu

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
        self.setToolTip(tip)
        if self.property("expression") != expression or self.property("invalid") != invalid:
            self.setProperty("expression", expression)
            self.setProperty("invalid", invalid)
            self.style().unpolish(self)
            self.style().polish(self)
        metrics = self.fontMetrics()
        left = metrics.horizontalAdvance(self.prefix) + 6 if self.prefix else 0
        right = metrics.horizontalAdvance(self._hint) + 6 if self._hint else 0
        self.setTextMargins(left, 0, right, 0)
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not (self.prefix or self._hint):
            return
        painter = QPainter(self)
        painter.setPen(self.palette().placeholderText().color())
        rect = self.contentsRect().adjusted(7, 0, -7, 0)
        if self._bind.isVisible():
            rect.setRight(rect.right() - 20)
        if self.prefix:
            painter.drawText(
                rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self.prefix
            )
        if self._hint:
            painter.drawText(
                rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight), self._hint
            )
        painter.end()

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
