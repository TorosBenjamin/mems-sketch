"""The fabrication process: layers, shared process constants and design rules.

Constants are available in every expression of every component as
``process.<name>`` (e.g. ``"2 * process.min_gap"``), so values that describe
the process do not have to be passed down through component parameters.
Constants may reference each other by their bare names.

Design rules are data: each names a rule *kind* (a plugin that knows how to
check, see :mod:`mems_sketch.process.rules`), the layers it applies to and its
values, which are numbers or expressions over the process constants.

A process can be shared in a library (requirement PRJ-8). A project using a
library's process may change its constants and rules and add rules, but not
its layers or layer stack: :func:`with_changes` applies such changes, and
:func:`changes_between` finds them again.
"""

from __future__ import annotations

import copy
import dataclasses
from dataclasses import dataclass, field
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
class Process:
    layers: dict[str, Layer] = field(default_factory=dict)
    constants: dict[str, Value] = field(default_factory=dict)
    rules: dict[str, Rule] = field(default_factory=dict)
    levels: list[Level] = field(default_factory=list)  # the layer stack, bottom to top
    default_level: str | None = None  # where a top component is; None: the first level
    description: str = ""

    def add_rule(self, rule: Rule) -> Rule:
        if rule.name in self.rules:
            raise ValueError(f"rule '{rule.name}' already exists")
        self.rules[rule.name] = rule
        return rule

    def scope(self) -> dict[str, float]:
        """Resolved constants keyed as they appear in expressions (``process.name``)."""
        return {PROCESS_PREFIX + k: v for k, v in resolve_variables(self.constants).items()}


# The layer stack of a process that declares none (and of a new project's).
DEFAULT_LEVELS = (Level("device", {"anchor": "anchor"}), Level("metal"))


def changed_rule(rule: Rule, changes: dict[str, Any]) -> Rule:
    """``rule`` with some of its fields (``layers``, ``severity``...) and values changed."""
    if "kind" in changes:
        raise ValueError(f"rule '{rule.name}': a change cannot give a rule another kind")
    fields = {k: v for k, v in changes.items() if k in RULE_FIELDS}
    values = {k: v for k, v in changes.items() if k not in RULE_FIELDS}
    return dataclasses.replace(rule, **fields, values={**rule.values, **values})


def rule_changes(base: Rule, rule: Rule) -> dict[str, Any]:
    """The fields and values in which ``rule`` differs from ``base``."""
    if {rule.kind, base.kind} != {base.kind}:  # (rule kinds, not shape kinds)
        raise ValueError(f"rule '{rule.name}' is a {base.kind} rule in the process: keep its kind")
    missing = sorted(set(base.values) - set(rule.values))
    if missing:
        raise ValueError(f"rule '{rule.name}' lost its value '{missing[0]}'")
    changes = {
        name: getattr(rule, name)
        for name in ("layers", "severity", "enabled", "message")
        if getattr(rule, name) != getattr(base, name)
    }
    changes.update({k: v for k, v in rule.values.items() if base.values.get(k, None) != v})
    return changes


def with_changes(
    base: Process,
    constants: dict[str, Value] | None = None,
    changed: dict[str, dict[str, Any]] | None = None,
    added: dict[str, Rule] | None = None,
) -> Process:
    """A copy of ``base`` with a project's changes: constants set or added,
    rules changed, and rules added."""
    process = copy.deepcopy(base)
    process.constants.update(constants or {})
    for name, changes in (changed or {}).items():
        if name not in process.rules:
            raise ValueError(f"the process has no rule '{name}' to change")
        process.rules[name] = changed_rule(process.rules[name], changes)
    for name, rule in (added or {}).items():
        if name in process.rules:
            raise ValueError(f"the process already has a rule '{name}'")
        process.rules[name] = rule
    return process


def changes_between(
    base: Process, process: Process
) -> tuple[dict[str, Value], dict[str, dict[str, Any]], dict[str, Rule]]:
    """What a project changed in a library's process: constants, changed rules and
    added rules (:func:`with_changes` undone). Its layers and stack must be the
    process's, and none of its constants or rules may be gone."""
    if process.layers != base.layers or process.levels != base.levels:
        raise ValueError("the layers and layer stack belong to the process: they cannot change")
    if process.default_level != base.default_level:
        raise ValueError("the default level belongs to the process: it cannot change")
    for kind, mine, theirs in (
        ("constant", process.constants, base.constants),
        ("rule", process.rules, base.rules),
    ):
        gone = [name for name in theirs if name not in mine]
        if gone:
            raise ValueError(f"the process's {kind} '{gone[0]}' cannot be removed")
    constants = {k: v for k, v in process.constants.items() if base.constants.get(k) != v}
    changed = {}
    for name, rule in process.rules.items():
        if name in base.rules and (changes := rule_changes(base.rules[name], rule)):
            changed[name] = changes
    added = {name: rule for name, rule in process.rules.items() if name not in base.rules}
    return constants, changed, added


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
        levels=[dataclasses.replace(level, roles=dict(level.roles)) for level in DEFAULT_LEVELS],
    )
