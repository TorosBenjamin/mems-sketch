"""The fabrication process: layers, shared process constants and design rules.

Constants are available in every expression of every component as
``process.<name>`` (e.g. ``"2 * process.min_gap"``), so values that describe
the process do not have to be passed down through component parameters.
Constants may reference each other by their bare names.

Design rules are data: each names a rule *kind* (a plugin that knows how to
check, see :mod:`mems_sketch.process.rules`), the layers it applies to and its
values, which are numbers or expressions over the process constants.

A *rule deck* is a set of rules with parameters of its own, in a file of its
own, shared between projects (requirement DRC-8). A project uses decks: it can
set a deck parameter for itself, override fields of a deck rule (with a
reason) and turn deck rules off; everything else follows the deck file.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mems_sketch.core.expressions import resolve_variables

Value = float | str
RuleValue = bool | float | str

ERROR = "error"
WARNING = "warning"
SEVERITIES = (ERROR, WARNING)
RULE_FIELDS = ("kind", "layers", "severity", "enabled", "message")  # not parameter names

PROCESS_PREFIX = "process."


@dataclass
class Layer:
    """A process layer and its GDS numbers."""

    name: str
    gds_layer: int
    gds_datatype: int = 0


@dataclass
class Level:
    """A level of the layer stack: its main layer, and the layers that belong to
    it by role (``anchor``: the layer anchoring this level to the one below)."""

    layer: str
    roles: dict[str, str] = field(default_factory=dict)


@dataclass
class Rule:
    """A design rule: a rule kind checked on ``layers`` (in the order the
    kind names them) with ``values`` for its parameters. A rule that is turned
    off stays in the list, so it is visibly off rather than gone."""

    name: str
    kind: str
    layers: list[str]
    values: dict[str, RuleValue] = field(default_factory=dict)
    severity: str = ERROR
    enabled: bool = True
    message: str = ""  # shown with its violations, e.g. why the rule exists

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"a rule's severity is error or warning, not '{self.severity}'")


def layer_rules(layer: str, min_width: float | None, min_space: float | None) -> list[Rule]:
    """The rules that a layer's minimum width and spacing, as earlier files
    set them on the layer, now are."""
    rules = []
    if min_width:
        rules.append(Rule(f"{layer}_min_width", "min_width", [layer], {"value": min_width}))
    if min_space:
        rules.append(Rule(f"{layer}_min_space", "min_space", [layer], {"value": min_space}))
    return rules


@dataclass
class RuleDeck:
    """A shared set of rules with parameters of its own. Its rules' values can
    use the parameters by their bare names (``value: min_feature``), and the
    parameters can use the process constants (``process.undercut``)."""

    name: str
    parameters: dict[str, Value] = field(default_factory=dict)
    rules: dict[str, Rule] = field(default_factory=dict)
    description: str = ""
    path: Path | None = None


@dataclass
class RuleOverride:
    """A project's change to one deck rule: rule fields (``layers``,
    ``severity``, ``enabled``, ``message``) and parameter values, by name."""

    changes: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass
class DeckUse:
    """A deck as a project uses it. ``error`` says why its file could not be
    read; its rules are then reported as not checked, never passed."""

    deck: RuleDeck
    parameters: dict[str, Value] = field(default_factory=dict)  # set by the project
    overrides: dict[str, RuleOverride] = field(default_factory=dict)
    error: str = ""

    def rule(self, name: str) -> Rule:
        """The deck's rule ``name`` with the project's override applied."""
        rule = self.deck.rules[name]
        override = self.overrides.get(name)
        if override is None:
            return rule
        fields = {k: v for k, v in override.changes.items() if k in RULE_FIELDS}
        values = {k: v for k, v in override.changes.items() if k not in RULE_FIELDS}
        return dataclasses.replace(rule, **fields, values={**rule.values, **values})

    def variables(self, scope: dict[str, float]) -> dict[str, float]:
        """The deck's parameters, resolved: the deck's values, those the project
        sets instead, over the process constants in ``scope``."""
        return resolve_variables({**self.deck.parameters, **self.parameters}, scope)


@dataclass
class Process:
    layers: dict[str, Layer] = field(default_factory=dict)
    constants: dict[str, Value] = field(default_factory=dict)
    rules: dict[str, Rule] = field(default_factory=dict)
    decks: dict[str, DeckUse] = field(default_factory=dict)
    levels: list[Level] = field(default_factory=list)  # the layer stack, bottom to top
    default_level: str | None = None  # where a top component is; None: the first level

    def add_rule(self, rule: Rule) -> Rule:
        if rule.name in self.rules:
            raise ValueError(f"rule '{rule.name}' already exists")
        self.rules[rule.name] = rule
        return rule

    def scope(self) -> dict[str, float]:
        """Resolved constants keyed as they appear in expressions (``process.name``)."""
        return {PROCESS_PREFIX + k: v for k, v in resolve_variables(self.constants).items()}


def check_levels(levels: list[Level], default: str | None, layers: dict[str, Layer]) -> None:
    """A layer stack names layers that exist, each at most once, and its default
    level is one of its levels."""
    seen: set[str] = set()
    for level in levels:
        for role, name in [("", level.layer), *level.roles.items()]:
            if name not in layers:
                raise ValueError(f"the layer stack names layer '{name}', which does not exist")
            if name in seen:
                raise ValueError(f"layer '{name}' is in the layer stack twice")
            seen.add(name)
            if role and not role.isidentifier():
                raise ValueError(f"'{role}' is not a valid role name")
    if default is not None and default not in {level.layer for level in levels}:
        raise ValueError(f"the default level '{default}' is not a level of the layer stack")


def default_process() -> Process:
    """Starting point for new projects: a device, anchor and metal layer, and
    default rules: minimum width and spacing of the device layer, and that
    every device piece is anchored and is released by the etch's undercut."""
    rules = [
        Rule("device_min_width", "min_width", ["device"], {"value": 2.0}),
        Rule("device_min_space", "min_space", ["device"], {"value": 2.0}),
        Rule("device_anchored", "anchored", ["device", "anchor"], severity=WARNING),
        Rule(
            "device_release",
            "release",
            ["device", "anchor"],
            {"undercut": "process.undercut"},
            severity=WARNING,
        ),
    ]
    return Process(
        layers={
            "device": Layer("device", 1, 0),
            "anchor": Layer("anchor", 2, 0),
            "metal": Layer("metal", 3, 0),
        },
        constants={"undercut": 2.0},
        rules={rule.name: rule for rule in rules},
        levels=[Level("device", {"anchor": "anchor"}), Level("metal")],
    )
