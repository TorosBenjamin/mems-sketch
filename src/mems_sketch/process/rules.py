"""Design-rule checks: the project's rules (:class:`~mems_sketch.core.process.Rule`)
checked on its geometry.

A rule only names a *rule kind* and gives it values; the kind is a plugin that
knows how to check (requirements DRC-9, DRC-10). A kind is a class with:

- ``name`` (as rules refer to it) and ``title``;
- ``roles``: the layers it takes, by role (``("layer",)``, ``("outer",
  "inner")``); a rule lists that many layers, in that order;
- ``parameters``: the values it takes, as :class:`~mems_sketch.options.Option`\\ s;
- ``check(regions, dbu, **values)``: ``regions`` are the layers' geometry in
  database units of ``dbu`` µm, one per role; it returns :class:`Finding`\\ s.

Kinds are found through the ``mems_sketch.rules`` entry-point group, or
registered at runtime with :func:`register_rule_kind`; the built-in ones are in
:mod:`mems_sketch.process.rule_kinds`. Rule values are numbers or expressions
over the process constants (``process.<name>``), checked against the kind's
declaration once evaluated.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any, ClassVar, Protocol

import klayout.db as kdb

from mems_sketch.core.component import DBU_UM, Geometry
from mems_sketch.core.expressions import evaluate
from mems_sketch.core.process import ERROR, WARNING, Rule
from mems_sketch.core.project import Project
from mems_sketch.options import Option

ENTRY_POINT_GROUP = "mems_sketch.rules"


@dataclass(frozen=True)
class Finding:
    """One place where a kind's check fails."""

    message: str
    bbox_um: tuple[float, float, float, float] | None = None


class RuleKind(Protocol):
    name: ClassVar[str]
    title: ClassVar[str]
    roles: ClassVar[tuple[str, ...]]
    parameters: ClassVar[tuple[Option, ...]]

    def check(self, regions: Sequence[kdb.Region], dbu: float, **values: Any) -> list[Finding]: ...


@dataclass(frozen=True)
class Violation:
    rule: str  # the rule's name; "layer" for geometry on an undefined layer
    layer: str  # the rule's layers, comma-separated
    message: str
    bbox_um: tuple[float, float, float, float] | None = None  # for highlighting in the GUI
    kind: str = ""
    severity: str = ERROR
    values: dict[str, Any] = field(default_factory=dict)  # as the rule was checked

    @property
    def is_error(self) -> bool:
        return self.severity == ERROR


_runtime: dict[str, type[RuleKind]] = {}


def register_rule_kind(cls: type[RuleKind]) -> type[RuleKind]:
    _runtime[cls.name] = cls
    return cls


def available_rule_kinds() -> dict[str, type[RuleKind]]:
    from mems_sketch.process import rule_kinds

    found: dict[str, type[RuleKind]] = {cls.name: cls for cls in rule_kinds.BUILTIN}
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        try:
            found[ep.name] = ep.load()
        except (ImportError, AttributeError):  # a stale or broken plugin: skip it
            continue
    found.update(_runtime)
    return found


def rule_values(
    kind: type[RuleKind], rule: Rule, scope: Mapping[str, float] | None = None
) -> dict[str, Any]:
    """The rule's values evaluated (expressions over ``scope``) and checked
    against the kind's parameters; defaults for those not given."""
    declared = {p.name: p for p in kind.parameters}
    unknown = sorted(set(rule.values) - set(declared))
    if unknown:
        known = ", ".join(declared) or "none"
        raise ValueError(
            f"'{kind.name}' has no parameter {', '.join(repr(n) for n in unknown)} "
            f"(its parameters: {known})"
        )
    values: dict[str, Any] = {}
    for name, option in declared.items():
        value = rule.values.get(name, option.default)
        if option.value_type in (int, float) and isinstance(value, str):
            value = evaluate(value, dict(scope or {}))
        values[name] = option.check(value)
    return values


def check(project: Project, geometry: Geometry | None = None) -> list[Violation]:
    """Check ``geometry`` (default: the drawn project) against the project's rules."""
    geometry = project.render() if geometry is None else geometry
    violations: list[Violation] = []
    for name in geometry.layers:
        if name not in project.layers:
            violations.append(Violation("layer", name, f"layer '{name}' is not defined"))
    kinds = available_rule_kinds()
    try:
        scope = project.process.scope()
    except Exception as exc:  # noqa: BLE001 - reported as a violation of every rule
        scope, scope_error = {}, exc
    else:
        scope_error = None
    for rule in project.process.rules.values():
        if rule.enabled:
            violations += _check_rule(rule, kinds, geometry, project, scope, scope_error)
    return violations


def _check_rule(rule, kinds, geometry, project, scope, scope_error) -> list[Violation]:
    layers = ", ".join(rule.layers)

    def problem(message: str) -> list[Violation]:
        # A rule that cannot be checked never passes (requirement DRC-10).
        return [Violation(rule.name, layers, message, kind=rule.kind, severity=ERROR)]

    kind = kinds.get(rule.kind)
    if kind is None:
        return problem(f"rule kind '{rule.kind}' is not installed: not checked")
    if len(rule.layers) != len(kind.roles):
        return problem(
            f"'{rule.kind}' takes {len(kind.roles)} layer(s) ({', '.join(kind.roles)}), "
            f"not {len(rule.layers)}"
        )
    undefined = [n for n in rule.layers if n not in project.layers]
    if undefined:
        return problem(f"layer '{undefined[0]}' is not defined: not checked")
    if scope_error is not None:
        return problem(f"the process constants have an error: {scope_error}")
    try:
        values = rule_values(kind, rule, scope)
    except Exception as exc:  # noqa: BLE001 - a bad value is the rule's problem
        return problem(f"{exc}: not checked")
    regions = [geometry.layers.get(n, kdb.Region()) for n in rule.layers]
    findings = kind().check(regions, DBU_UM, **values)
    return [
        Violation(
            rule.name,
            layers,
            f"{f.message}{' (' + rule.message + ')' if rule.message else ''}",
            f.bbox_um,
            kind=rule.kind,
            severity=rule.severity,
            values=values,
        )
        for f in findings
    ]


def errors(violations: Sequence[Violation]) -> list[Violation]:
    return [v for v in violations if v.is_error]


def warnings(violations: Sequence[Violation]) -> list[Violation]:
    return [v for v in violations if v.severity == WARNING]
