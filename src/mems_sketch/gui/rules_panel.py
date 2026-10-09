"""The design rules, in the Process tab: one row per rule, edited in place.

A rule is a rule kind (a plugin, see :mod:`mems_sketch.process.rules`) on some
layers with values. The *On* check box turns a rule off without deleting it,
so it shows as off rather than disappearing. Values are written ``name=value``,
separated by commas; a value can be a number or an expression over the
process constants (``undercut=process.undercut``).
"""

from __future__ import annotations

import dataclasses

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QHeaderView, QLabel, QMenu, QTableWidgetItem, QVBoxLayout

from mems_sketch.core.process import SEVERITIES, Rule, RuleValue
from mems_sketch.editing import EditSession
from mems_sketch.gui.panels import _action_bar, _format, _Panel, _readonly, _table, parse_value
from mems_sketch.process.rules import available_rule_kinds

COLUMNS = ("Rule", "Kind", "Layers", "Values", "Severity", "Note")
NAME, KIND, LAYERS, VALUES, SEVERITY, NOTE = range(len(COLUMNS))


def format_values(values: dict[str, RuleValue]) -> str:
    return ", ".join(f"{name}={_format(value)}" for name, value in values.items())


def parse_values(text: str, kind: str) -> dict[str, RuleValue]:
    """``name=value, ...`` for a rule of ``kind``: numbers or expressions for
    number parameters, yes/no for switches, text for the rest."""
    cls = available_rule_kinds().get(kind)
    declared = {p.name: p for p in cls.parameters} if cls else {}
    values: dict[str, RuleValue] = {}
    for part in filter(None, (p.strip() for p in text.split(","))):
        name, sep, value = part.partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not name:
            raise ValueError(f"write values as name=value, not '{part}'")
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
    """The project's design rules (requirements DRC-1, DRC-6, DRC-7)."""

    def __init__(self, document: EditSession) -> None:
        super().__init__()
        self.document = document
        self.rules = _table(COLUMNS)
        self.rules.itemChanged.connect(self._rule_changed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.actions = _action_bar(
            QLabel("Rules"),
            ("add", "Add a rule", self._show_kinds),
            ("remove", "Remove the selected rules", self._remove_rules),
            help="What the layers are checked with after every change. Each rule is a "
            "*kind* of check on some layers with its values, written *name=value*; a "
            "value can be an expression such as *process.undercut*. Untick a rule to "
            "turn it off: it stays listed as off. *Errors* fail `check` on the command "
            "line; *warnings* only with `--strict`.",
        )
        layout.addLayout(self.actions)
        layout.addWidget(self.rules)
        self._rule_names: list[str] = []

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

    def refresh(self) -> None:
        rules = self.document.project.process.rules
        kinds = available_rule_kinds()
        self._rule_names = list(rules)
        self.rules.blockSignals(True)
        self.rules.setRowCount(len(rules))
        for row, rule in enumerate(rules.values()):
            name = QTableWidgetItem(rule.name)
            name.setFlags(name.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            name.setCheckState(Qt.CheckState.Checked if rule.enabled else Qt.CheckState.Unchecked)
            name.setToolTip("Untick to turn the rule off")
            self.rules.setItem(row, NAME, name)
            kind = kinds.get(rule.kind)
            kind_item = _readonly(kind.title if kind else f"{rule.kind} (not installed)")
            if kind is not None:
                kind_item.setToolTip(f"{rule.kind}: layers {', '.join(kind.roles)}")
            self.rules.setItem(row, KIND, kind_item)
            self.rules.setItem(row, LAYERS, QTableWidgetItem(", ".join(rule.layers)))
            self.rules.setItem(row, VALUES, QTableWidgetItem(format_values(rule.values)))
            self.rules.setItem(row, SEVERITY, QTableWidgetItem(rule.severity))
            self.rules.setItem(row, NOTE, QTableWidgetItem(rule.message))
        self.rules.blockSignals(False)
        # the name column also holds the check box
        header = self.rules.horizontalHeader()
        header.setSectionResizeMode(NAME, QHeaderView.ResizeMode.Interactive)
        metrics = self.rules.fontMetrics()
        widest = max((metrics.horizontalAdvance(n) for n in rules), default=40)
        self.rules.setColumnWidth(NAME, max(widest, metrics.horizontalAdvance("Rule")) + 48)

    def _rule_changed(self, item: QTableWidgetItem) -> None:
        name = self._rule_names[item.row()]
        rule = self.document.project.process.rules[name]
        text = item.text().strip()
        column = item.column()

        def apply() -> None:
            if column == NAME:
                enabled = item.checkState() == Qt.CheckState.Checked
                if enabled != rule.enabled and text == name:
                    self.document.process.enable_rule(name, enabled)
                else:
                    self.document.process.set_rule(name, dataclasses.replace(rule, name=text))
                return
            changes: dict = {}
            if column == LAYERS:
                changes["layers"] = [n.strip() for n in text.split(",") if n.strip()]
            elif column == VALUES:
                changes["values"] = parse_values(text, rule.kind)
            elif column == SEVERITY:
                if text not in SEVERITIES:
                    raise ValueError(f"a rule's severity is {' or '.join(SEVERITIES)}")
                changes["severity"] = text
            elif column == NOTE:
                changes["message"] = text
            self.document.process.set_rule(name, Rule(**{**dataclasses.asdict(rule), **changes}))

        if not self._guard(apply):
            self.refresh()

    def _remove_rules(self) -> None:
        rows = sorted({i.row() for i in self.rules.selectedItems()}, reverse=True)
        for row in rows:
            self._guard(lambda n=self._rule_names[row]: self.document.process.remove_rule(n))
