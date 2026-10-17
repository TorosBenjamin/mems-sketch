"""The design rules, in the Process tab: the process the project uses, and
one row per rule, edited in place.

A rule is a rule kind (a plugin, see :mod:`mems_sketch.process.rules`) on some
layers with values. The check box by its name turns a rule off without
deleting it, so it shows as off rather than disappearing. Values are written
``name=value``, separated by commas (just the value for a rule kind with one
parameter); a value can be a number or an expression over the process
constants (``undercut=process.undercut``).

A project can use a process from a library (requirement PRJ-8). Editing one
of that process's rules changes it for this project only, listed with its
reason; *Reset* takes the process's rule again.

A row shows what is scanned for: the rule, its kind, layers, values and
severity. The rest folds out under the selected rule: its note, where it comes
from and why it was changed. A rule changed or added on a library's process is
marked in its row.
"""

from __future__ import annotations

import dataclasses

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.core.process import SEVERITIES, Rule, RuleValue, rule_changes
from mems_sketch.editing import EditSession
from mems_sketch.gui import icons, motion, theme, widgets
from mems_sketch.gui.panels import _blank_icon, _format, _Panel, _readonly, _table, parse_value
from mems_sketch.process.rules import available_rule_kinds

COLUMNS = ("Rule", "Kind", "Layers", "Values", "Severity")
NAME, KIND, LAYERS, VALUES, SEVERITY = range(len(COLUMNS))


def _single(kind) -> str | None:
    """The name of a rule kind's only parameter, if it has just one."""
    parameters = getattr(kind, "parameters", ())
    return parameters[0].name if len(parameters) == 1 else None


def format_values(values: dict[str, RuleValue], kind=None) -> str:
    """``name=value, ...``; just the value for a kind with one parameter (``2``,
    not ``value=2``)."""
    if len(values) == 1 and _single(kind) in values:
        return _format(next(iter(values.values())))
    return ", ".join(f"{name}={_format(value)}" for name, value in values.items())


def parse_assignments(text: str) -> list[tuple[str, str]]:
    pairs = []
    for part in filter(None, (p.strip() for p in text.split(","))):
        name, sep, value = part.partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not name:
            raise ValueError(f"write values as name=value, not '{part}'")
        pairs.append((name, value))
    return pairs


def parse_values(text: str, kind: str) -> dict[str, RuleValue]:
    """``name=value, ...`` for a rule of ``kind``: numbers or expressions for
    number parameters, yes/no for switches, text for the rest."""
    cls = available_rule_kinds().get(kind)
    declared = {p.name: p for p in cls.parameters} if cls else {}
    only = _single(cls)
    if only is not None and text.strip() and "=" not in text:  # just the value
        text = f"{only}={text}"
    values: dict[str, RuleValue] = {}
    for name, value in parse_assignments(text):
        option = declared.get(name)
        if option is None and cls is not None:
            known = ", ".join(declared) or "none"
            raise ValueError(f"'{kind}' has no parameter '{name}' (its parameters: {known})")
        if option is not None and option.value_type is bool:
            values[name] = option.parse(value)
        elif option is not None and option.value_type is str:
            values[name] = option.check(value)
        else:
            values[name] = parse_value(value)
    return values


class RuleDetails(QWidget):
    """What folds out under the selected rule: its note, where it comes from and,
    for a change to a library's process, why."""

    def __init__(self, panel: RulesPanel, name: str, source: str, own: bool) -> None:
        super().__init__()
        self.setObjectName("rule-details")
        rule = panel.document.project.process.rules[name]
        self.note = QLineEdit(rule.message)
        self.note.setPlaceholderText("Shown with the rule's violations")
        self.note.editingFinished.connect(lambda: panel.set_message(name, self.note.text().strip()))
        self.source = QLabel(source)
        self.source.setObjectName("muted")
        self.reason = QLineEdit(panel.document.project.reasons.get(name, ""))
        self.reason.setPlaceholderText("Why this project changes the process's rule")
        self.reason.editingFinished.connect(
            lambda: panel.set_reason(name, self.reason.text().strip())
        )
        row = QHBoxLayout(self)
        pad = theme.metric("space_m")
        row.setContentsMargins(3 * pad, pad // 2, pad, pad // 2)  # under the rule's name
        row.setSpacing(theme.metric("space_s"))
        row.addWidget(QLabel("Note"))
        row.addWidget(self.note, 2)
        row.addSpacing(theme.metric("space_l"))
        row.addWidget(QLabel("From"))
        row.addWidget(self.source)
        reason_label = QLabel("Reason")
        for widget in (reason_label, self.reason):
            widget.setVisible(not own)  # only a change to a library's process has one
        row.addSpacing(theme.metric("space_l"))
        row.addWidget(reason_label)
        row.addWidget(self.reason, 3)


class RulesPanel(_Panel):
    """The process the project uses and its design rules (requirements DRC-1,
    DRC-6 to DRC-8, PRJ-8)."""

    def __init__(self, document: EditSession) -> None:
        super().__init__()
        self.document = document
        self.process = QComboBox()
        self.process.setToolTip(
            "The process the project uses: one of its own, or a library's (library.process), "
            "whose layers then stay as they are there"
        )
        self.process.activated.connect(self._process_chosen)
        self.process.setMinimumWidth(180)  # shown in the Process page's header
        self.rules = _table(COLUMNS, stretch=SEVERITY)
        self.rules.itemChanged.connect(self._rule_changed)
        self.rules.itemSelectionChanged.connect(self._selection_changed)
        self.details: RuleDetails | None = None  # under the selected rule
        self._open: str | None = None  # the rule whose details are shown
        card = widgets.Card(
            "Rules",
            help="What the layers are checked with after every change. Each rule is a "
            "*kind* of check on some layers with its values: a rule with one value shows "
            "just the value, one with several *name=value*; a value can be an expression "
            "such as *process.undercut*. Untick a rule to turn it off: it stays listed as "
            "off. *Errors* fail `check` on the command line; *warnings* only with "
            "`--strict`. Select a rule for its note and where it comes from. On a "
            "library's process, a changed or added rule is marked and can say why "
            "(*Reason*); *Reset* takes the process's rule again.",
        )
        self.actions = card.add_actions(
            ("plus", "Add a rule", self._show_kinds),
            ("undo", "Reset the selected rules to the process's", self._reset_rules),
            ("minus", "Remove the selected rules", self._remove_rules),
        )
        card.body.addWidget(self.rules)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(card)
        self._rows: list[str | None] = []  # per table row: its rule; None for the details

    # -- adding --------------------------------------------------------------

    def kind_menu(self) -> QMenu:
        menu = QMenu(self)
        for name, cls in sorted(available_rule_kinds().items(), key=lambda kv: kv[1].title):
            action = menu.addAction(f"{cls.title}  ({', '.join(cls.roles)})")
            action.triggered.connect(
                lambda _=False, n=name: self._guard(lambda: self.document.process.add_rule(n))
            )
        return menu

    def _show_kinds(self) -> None:
        self.kind_menu().exec(QCursor.pos())

    def _process_chosen(self, index: int) -> None:
        name = self.process.itemText(index)
        if name != self.document.project.process_name and not self._guard(
            lambda: self.document.process.use(name)
        ):
            self.refresh()

    # -- showing -------------------------------------------------------------

    def refresh(self) -> None:
        project = self.document.project
        process, base = project.process, project.base_process
        self.process.clear()
        self.process.addItems(project.process_names())
        self.process.setCurrentText(project.process_name)
        kinds = available_rule_kinds()
        if self._open not in process.rules:
            self._open = None
        self._rows = []
        for name in process.rules:
            self._rows.append(name)
            if name == self._open:
                self._rows.append(None)
        self.rules.blockSignals(True)
        self.rules.clearSpans()
        self.rules.setRowCount(0)  # drops the old details too
        self.rules.setRowCount(len(self._rows))
        self.details = None
        for row, name in enumerate(self._rows):
            if name is None:
                continue
            rule = process.rules[name]
            if base is None:
                source = "project"
            elif name not in base.rules:
                source = "project (added)"
            elif rule_changes(base.rules[name], rule):
                source = f"{project.process_name} (changed)"
            else:
                source = project.process_name
            own = source == project.process_name or base is None  # nothing to give a reason for
            item = QTableWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if rule.enabled else Qt.CheckState.Unchecked)
            item.setIcon(_blank_icon() if own else icons.icon("modified"))
            item.setToolTip("Untick to turn the rule off" + ("" if own else f"\n{source}"))
            self.rules.setItem(row, NAME, item)
            kind = kinds.get(rule.kind)
            kind_item = _readonly(kind.title if kind else f"{rule.kind} (not installed)")
            if kind is not None:
                kind_item.setToolTip(f"{rule.kind}: layers {', '.join(kind.roles)}")
            self.rules.setItem(row, KIND, kind_item)
            self.rules.setItem(row, LAYERS, QTableWidgetItem(", ".join(rule.layers)))
            self.rules.setItem(row, VALUES, QTableWidgetItem(format_values(rule.values, kind)))
            severity = QTableWidgetItem(rule.severity)
            severity.setIcon(icons.icon("error" if rule.severity == "error" else "warning"))
            severity.setToolTip(" or ".join(SEVERITIES))
            self.rules.setItem(row, SEVERITY, severity)
            if name == self._open:  # its details fold out in the next row
                self.details = RuleDetails(self, name, source, own)
                self.rules.setSpan(row + 1, 0, 1, len(COLUMNS))
                self.rules.setCellWidget(row + 1, 0, self.details)
                self.rules.setRowHeight(row + 1, self.details.sizeHint().height())
        self.rules.blockSignals(False)
        widgets.fit_to_rows(self.rules)

    def _selection_changed(self) -> None:
        """One rule selected: its details fold out under it."""
        names = {self._rows[i.row()] for i in self.rules.selectedItems()} - {None}
        if len(names) != 1 or next(iter(names)) == self._open:
            return
        self._open = next(iter(names))
        self.refresh()
        selection = self.rules.selectionModel()
        selection.blockSignals(True)
        widgets.select_row(self.rules, self._rows.index(self._open))
        selection.blockSignals(False)
        self._unfold()

    def _unfold(self) -> None:
        """Open the details row from nothing to its height, so the rows below are
        seen moving down rather than jumping."""
        if None not in self._rows:
            return
        row, details = self._rows.index(None), self.details
        full = self.rules.rowHeight(row)

        def step(t: float) -> None:
            if self.details is not details or row >= self.rules.rowCount():
                return  # refreshed meanwhile: the new rows have their own heights
            self.rules.setRowHeight(row, max(1, round(full * t)))
            widgets.fit_to_rows(self.rules)

        motion.run(self, step, motion.SHORT, key="unfold")

    def open_details(self, name: str) -> RuleDetails:
        """Fold out the details of the rule ``name`` (as selecting it does)."""
        widgets.select_row(self.rules, self._rows.index(name))
        return self.details

    def set_message(self, name: str, text: str) -> None:
        rule = self.document.project.process.rules[name]
        if text != rule.message and not self._guard(
            lambda: self.document.process.set_rule(name, dataclasses.replace(rule, message=text))
        ):
            self.refresh()

    def set_reason(self, name: str, text: str) -> None:
        if text != self.document.project.reasons.get(name, "") and not self._guard(
            lambda: self.document.process.set_reason(name, text)
        ):
            self.refresh()

    # -- editing -------------------------------------------------------------

    def _rule_changed(self, item: QTableWidgetItem) -> None:
        name = self._rows[item.row()]
        if name is None:
            return
        rule = self.document.project.process.rules[name]
        text = item.text().strip()
        column = item.column()

        def changes() -> dict:
            if column == NAME:
                return {"enabled": item.checkState() == Qt.CheckState.Checked}
            if column == LAYERS:
                return {"layers": [n.strip() for n in text.split(",") if n.strip()]}
            if column == VALUES:
                return {"values": parse_values(text, rule.kind)}
            if column == SEVERITY:
                if text not in SEVERITIES:
                    raise ValueError(f"a rule's severity is {' or '.join(SEVERITIES)}")
                return {"severity": text}
            return {}

        def apply() -> None:
            edits = self.document.process
            if column == NAME:
                enabled = changes()["enabled"]
                if enabled != rule.enabled and text == name:
                    edits.enable_rule(name, enabled)
                else:
                    edits.set_rule(name, dataclasses.replace(rule, name=text))
            else:
                edits.set_rule(name, Rule(**{**dataclasses.asdict(rule), **changes()}))

        if not self._guard(apply):
            self.refresh()

    def _selected(self) -> list[str]:
        rows = {i.row() for i in self.rules.selectedItems()}
        return [name for row in sorted(rows) if (name := self._rows[row]) is not None]

    def _remove_rules(self) -> None:
        for name in self._selected():
            self._guard(lambda n=name: self.document.process.remove_rule(n))

    def _reset_rules(self) -> None:
        for name in self._selected():
            self._guard(lambda n=name: self.document.process.reset_rule(n))
