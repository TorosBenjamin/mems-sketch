"""The design rules, in the Process tab: the rule decks the project uses, and
one row per rule, edited in place.

A rule is a rule kind (a plugin, see :mod:`mems_sketch.process.rules`) on some
layers with values. The check box by its name turns a rule off without
deleting it, so it shows as off rather than disappearing. Values are written
``name=value``, separated by commas; a value can be a number or an expression
over the process constants (``undercut=process.undercut``).

The rules of a deck (a shared file) are listed after the project's own, named
``deck.rule``. Editing one changes it for this project only: an override,
listed with its reason, which *Reset* removes. A deck's parameters are set the
same way: what the project sets wins over the deck's value.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QFileDialog,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMenu,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.core.process import SEVERITIES, Rule, RuleValue
from mems_sketch.editing import EditSession
from mems_sketch.gui.panels import _action_bar, _format, _Panel, _readonly, _table, parse_value
from mems_sketch.process.rules import available_rule_kinds

COLUMNS = ("Rule", "Kind", "Layers", "Values", "Severity", "Note", "From", "Reason")
NAME, KIND, LAYERS, VALUES, SEVERITY, NOTE, SOURCE, REASON = range(len(COLUMNS))
DECK_COLUMNS = ("Deck", "File", "Parameters")
DECK_NAME, DECK_FILE, DECK_PARAMETERS = range(len(DECK_COLUMNS))
DECK_FILTER = "Rule decks (*.yaml *.yml)"


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
    """The project's rule decks and design rules (requirements DRC-1, DRC-6 to DRC-8)."""

    def __init__(self, document: EditSession) -> None:
        super().__init__()
        self.document = document
        self.decks = _table(DECK_COLUMNS)
        self.decks.itemChanged.connect(self._deck_changed)
        self.rules = _table(COLUMNS)
        self.rules.itemChanged.connect(self._rule_changed)
        self.deck_actions = _action_bar(
            QLabel("Rule decks"),
            ("add", "Use a rule deck…", self._use_deck),
            ("recompile", "Read the selected decks' files again", self._reload_decks),
            ("save", "Save the project's own rules as a rule deck…", self._save_as_deck),
            ("remove", "Stop using the selected decks", self._remove_decks),
            help="Shared sets of rules with parameters of their own, such as a fab's, "
            "kept in files of their own. Their rules are listed under *Rules* and checked "
            "like the project's. Set a deck parameter here for this project only "
            "(*name=value*); the deck's other values follow its file.",
        )
        self.actions = _action_bar(
            QLabel("Rules"),
            ("add", "Add a rule", self._show_kinds),
            ("undo", "Reset the selected deck rules to the deck's", self._reset_rules),
            ("remove", "Remove the selected rules", self._remove_rules),
            help="What the layers are checked with after every change. Each rule is a "
            "*kind* of check on some layers with its values, written *name=value*; a "
            "value can be an expression such as *process.undercut*. Untick a rule to "
            "turn it off: it stays listed as off. *Errors* fail `check` on the command "
            "line; *warnings* only with `--strict`. A deck's rules can be changed for "
            "this project (give a *reason*); *Reset* takes the deck's again.",
        )
        top, bottom = QWidget(), QWidget()
        for widget, bar, table in (
            (top, self.deck_actions, self.decks),
            (bottom, self.actions, self.rules),
        ):
            box = QVBoxLayout(widget)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(0)
            box.addLayout(bar)
            box.addWidget(table)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(top)
        split.addWidget(bottom)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 4)
        split.setSizes([90, 360])  # decks are few; rules many
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(split)
        self._rows: list[tuple[str | None, str]] = []  # (deck, rule) per row; deck None: own
        self._deck_names: list[str] = []

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

    def _use_deck(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Use a rule deck", "", DECK_FILTER)
        if path:
            self._guard(lambda: self.document.process.use_deck(path))

    def _save_as_deck(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save the rules as a rule deck", "", DECK_FILTER
        )
        if not path:
            return
        if not Path(path).suffix:
            path += ".yaml"
        name, ok = QInputDialog.getText(
            self, "Rule deck", "Name of the deck:", text=Path(path).stem
        )
        if ok and name.strip():
            self._guard(lambda: self.document.process.save_rules_as_deck(path, name.strip()))

    # -- showing -------------------------------------------------------------

    def refresh(self) -> None:
        process = self.document.project.process
        self._refresh_decks()
        rows: list[tuple[str | None, str, Rule]] = [(None, n, r) for n, r in process.rules.items()]
        for deck_name, use in process.decks.items():
            rows += [(deck_name, n, use.rule(n)) for n in use.deck.rules]
        kinds = available_rule_kinds()
        self._rows = [(deck, name) for deck, name, _ in rows]
        self.rules.blockSignals(True)
        self.rules.setRowCount(len(rows))
        for row, (deck, name, rule) in enumerate(rows):
            shown = f"{deck}.{name}" if deck else name
            item = QTableWidgetItem(shown)
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
            override = process.decks[deck].overrides.get(name) if deck else None
            source = "project" if deck is None else f"{deck} (changed)" if override else deck
            self.rules.setItem(row, SOURCE, _readonly(source))
            reason = QTableWidgetItem(override.reason if override else "")
            if override is None:  # only a change of a deck rule has a reason
                reason = _readonly("")
            self.rules.setItem(row, REASON, reason)
        self.rules.blockSignals(False)
        # the name column also holds the check box
        header = self.rules.horizontalHeader()
        header.setSectionResizeMode(NAME, QHeaderView.ResizeMode.Interactive)
        metrics = self.rules.fontMetrics()
        names = [self.rules.item(r, NAME).text() for r in range(len(rows))]
        widest = max((metrics.horizontalAdvance(n) for n in names), default=40)
        self.rules.setColumnWidth(NAME, max(widest, metrics.horizontalAdvance("Rule")) + 48)

    def _refresh_decks(self) -> None:
        decks = self.document.project.process.decks
        self._deck_names = list(decks)
        self.decks.blockSignals(True)
        self.decks.setRowCount(len(decks))
        for row, (name, use) in enumerate(decks.items()):
            self.decks.setItem(row, DECK_NAME, _readonly(name))
            file_item = _readonly(str(use.deck.path or ""))
            if use.error:
                file_item.setText(f"{use.deck.path} — {use.error}")
            self.decks.setItem(row, DECK_FILE, file_item)
            values = {**use.deck.parameters, **use.parameters}
            item = QTableWidgetItem(format_values(values))
            item.setToolTip(
                "Set here for this project: "
                + (format_values(use.parameters) or "nothing")
                + "\nThe deck's: "
                + (format_values(use.deck.parameters) or "no parameters")
            )
            self.decks.setItem(row, DECK_PARAMETERS, item)
        self.decks.blockSignals(False)

    # -- editing -------------------------------------------------------------

    def _rule_changed(self, item: QTableWidgetItem) -> None:
        deck, name = self._rows[item.row()]
        process = self.document.project.process
        rule = process.decks[deck].rule(name) if deck else process.rules[name]
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
            if deck is not None:
                if column == REASON:
                    edits.override_rule(deck, name, {}, reason=text)
                    return
                change = changes()
                if column == NAME and item.text() != f"{deck}.{name}":
                    raise ValueError("a deck's rules keep their names")
                change = change.pop("values", None) or change  # values by parameter name
                edits.override_rule(deck, name, change)
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

    def _deck_changed(self, item: QTableWidgetItem) -> None:
        deck = self._deck_names[item.row()]
        use = self.document.project.process.decks[deck]

        def apply() -> None:
            given = dict(parse_assignments(item.text()))
            for name in use.deck.parameters:
                if name not in given:
                    value = None  # left out: the deck's own
                else:
                    value = parse_value(given[name])
                    if value == use.deck.parameters[name]:
                        value = None
                if value != use.parameters.get(name):
                    self.document.process.set_deck_parameter(deck, name, value)
            unknown = sorted(set(given) - set(use.deck.parameters))
            if unknown:
                raise ValueError(f"rule deck '{deck}' has no parameter '{unknown[0]}'")

        if not self._guard(apply):
            self.refresh()

    def _selected_rows(self, table) -> list[int]:
        return sorted({i.row() for i in table.selectedItems()}, reverse=True)

    def _remove_rules(self) -> None:
        for row in self._selected_rows(self.rules):
            deck, name = self._rows[row]
            if deck is not None:
                self.error.emit(f"{deck}.{name} belongs to the rule deck: untick it to turn it off")
                continue
            self._guard(lambda n=name: self.document.process.remove_rule(n))

    def _reset_rules(self) -> None:
        for row in self._selected_rows(self.rules):
            deck, name = self._rows[row]
            if deck is not None:
                self._guard(lambda d=deck, n=name: self.document.process.reset_rule(d, n))

    def _reload_decks(self) -> None:
        for row in self._selected_rows(self.decks):
            self._guard(lambda n=self._deck_names[row]: self.document.process.reload_deck(n))

    def _remove_decks(self) -> None:
        for row in self._selected_rows(self.decks):
            self._guard(lambda n=self._deck_names[row]: self.document.process.remove_deck(n))
