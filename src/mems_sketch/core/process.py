"""The fabrication process: layers, shared process constants and design rules.

Constants are available in every expression of every component as
``process.<name>`` (e.g. ``"2 * process.min_gap"``), so values that describe
the process do not have to be passed down through component parameters.
Constants may reference each other by their bare names.

Design rules are data: each names a rule *kind* (a plugin that knows how to
check, see :mod:`mems_sketch.process.rules`), the layers it applies to and its
values, which are numbers or expressions over the process constants.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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

    def add_rule(self, rule: Rule) -> Rule:
        if rule.name in self.rules:
            raise ValueError(f"rule '{rule.name}' already exists")
        self.rules[rule.name] = rule
        return rule

    def scope(self) -> dict[str, float]:
        """Resolved constants keyed as they appear in expressions (``process.name``)."""
        return {PROCESS_PREFIX + k: v for k, v in resolve_variables(self.constants).items()}


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
    )
