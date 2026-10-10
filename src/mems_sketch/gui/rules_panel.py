"""The design rules, in the Process tab: the process the project uses, and
one row per rule, edited in place.

A rule is a rule kind (a plugin, see :mod:`mems_sketch.process.rules`) on some
layers with values. The check box by its name turns a rule off without
deleting it, so it shows as off rather than disappearing. Values are written
``name=value``, separated by commas; a value can be a number or an expression
over the process constants (``undercut=process.undercut``).

A project can use a process from a library (requirement PRJ-8). Editing one
of that process's rules changes it for this project only, listed with its
reason; *Reset* takes the process's rule again.
"""

from __future__ import annotations

import dataclasses

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QTableWidgetItem,
    QVBoxLayout,
)

from mems_sketch.core.process import SEVERITIES, Rule, RuleValue, rule_changes
from mems_sketch.editing import EditSession
from mems_sketch.gui.panels import _action_bar, _format, _Panel, _readonly, _table, parse_value
from mems_sketch.process.rules import available_rule_kinds

COLUMNS = ("Rule", "Kind", "Layers", "Values", "Severity", "Note", "From", "Reason")
NAME, KIND, LAYERS, VALUES, SEVERITY, NOTE, SOURCE, REASON = range(len(COLUMNS))


def format_values(values: dict[str, RuleValue]) -> str:
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
        self.rules = _table(COLUMNS)
        self.rules.itemChanged.connect(self._rule_changed)
        self.actions = _action_bar(
            QLabel("Rules"),
            ("add", "Add a rule", self._show_kinds),
            ("undo", "Reset the selected rules to the process's", self._reset_rules),
            ("remove", "Remove the selected rules", self._remove_rules),
            help="What the layers are checked with after every change. Each rule is a "
            "*kind* of check on some layers with its values, written *name=value*; a "
            "value can be an expression such as *process.undercut*. Untick a rule to "
            "turn it off: it stays listed as off. *Errors* fail `check` on the command "
            "line; *warnings* only with `--strict`. On a library's process, a changed or "
            "added rule can say why (*Reason*); *Reset* takes the process's rule again.",
        )
        row = QHBoxLayout()
        row.setContentsMargins(6, 4, 6, 4)
        row.addWidget(QLabel("Process"))
        row.addWidget(self.process, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(row)
        layout.addLayout(self.actions)
        layout.addWidget(self.rules)
        self._rows: list[str] = []

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
        self._rows = list(process.rules)
        self.rules.blockSignals(True)
        self.rules.setRowCount(len(self._rows))
        for row, (name, rule) in enumerate(process.rules.items()):
            item = QTableWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if rule.enabled else Qt.CheckState.Unchecked)
            item.setToolTip("Untick to turn the rule off")
            self.rules.setItem(row, NAME, item)
            kind = kinds.get(rule.kind)
            kind_item = _readonly(kind.title if kind else f"{rule.kind} (not installed)")
            if kind is not None:
                kind_item.setToolTip(f"{rule.kind}: layers {', '.join(kind.roles)}")
            self.rules.setItem(row, KIND, kind_item)
            self.rules.setItem(row, LAYERS, QTableWidgetItem(", ".join(rule.layers)))
            self.rules.setItem(row, VALUES, QTableWidgetItem(format_values(rule.values)))
            self.rules.setItem(row, SEVERITY, QTableWidgetItem(rule.severity))
            self.rules.setItem(row, NOTE, QTableWidgetItem(rule.message))
            if base is None:
                source = "project"
            elif name not in base.rules:
                source = "project (added)"
            elif rule_changes(base.rules[name], rule):
                source = f"{project.process_name} (changed)"
            else:
                source = project.process_name
            self.rules.setItem(row, SOURCE, _readonly(source))
            own = source == project.process_name or base is None  # nothing to give a reason for
            reason = _readonly("") if own else QTableWidgetItem(project.reasons.get(name, ""))
            self.rules.setItem(row, REASON, reason)
        self.rules.blockSignals(False)
        # the name column also holds the check box
        header = self.rules.horizontalHeader()
        header.setSectionResizeMode(NAME, QHeaderView.ResizeMode.Interactive)
        metrics = self.rules.fontMetrics()
        widest = max((metrics.horizontalAdvance(n) for n in self._rows), default=40)
        self.rules.setColumnWidth(NAME, max(widest, metrics.horizontalAdvance("Rule")) + 48)

    # -- editing -------------------------------------------------------------

    def _rule_changed(self, item: QTableWidgetItem) -> None:
        name = self._rows[item.row()]
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
            if column == NOTE:
                return {"message": text}
            return {}

        def apply() -> None:
            edits = self.document.process
            if column == REASON:
                edits.set_reason(name, text)
            elif column == NAME:
                enabled = changes()["enabled"]
                if enabled != rule.enabled and text == name:
                    edits.enable_rule(name, enabled)
                else:
                    edits.set_rule(name, dataclasses.replace(rule, name=text))
            else:
                edits.set_rule(name, Rule(**{**dataclasses.asdict(rule), **changes()}))

        if not self._guard(apply):
            self.refresh()

    def _selected_rows(self, table) -> list[int]:
        return sorted({i.row() for i in table.selectedItems()}, reverse=True)

    def _remove_rules(self) -> None:
        for row in self._selected_rows(self.rules):
            self._guard(lambda n=self._rows[row]: self.document.process.remove_rule(n))

    def _reset_rules(self) -> None:
        for row in self._selected_rows(self.rules):
            self._guard(lambda n=self._rows[row]: self.document.process.reset_rule(n))
