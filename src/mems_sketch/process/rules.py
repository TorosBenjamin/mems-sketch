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

import dataclasses
import hashlib
import inspect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any, ClassVar, Protocol

from mems_sketch.core.component import DBU_UM, Geometry
from mems_sketch.core.expressions import evaluate
from mems_sketch.core.process import ERROR, WARNING, Rule
from mems_sketch.core.project import Project
from mems_sketch.core.region import Box, IntPolygon, Region
from mems_sketch.core.transform import Transform
from mems_sketch.options import Option

ENTRY_POINT_GROUP = "mems_sketch.rules"


@dataclass(frozen=True)
class Finding:
    """One place where a kind's check fails."""

    message: str
    bbox_um: tuple[float, float, float, float] | None = None
    measured: float | None = None  # what was measured there (a width, a gap), in µm
    outline: tuple[tuple[int, int], ...] = ()  # where, exactly (database units)
    holes: tuple[tuple[tuple[int, int], ...], ...] = ()  # and the outline's holes


class RuleKind(Protocol):
    name: ClassVar[str]
    title: ClassVar[str]
    roles: ClassVar[tuple[str, ...]]
    parameters: ClassVar[tuple[Option, ...]]

    def check(self, regions: Sequence[Region], dbu: float, **values: Any) -> list[Finding]: ...


@dataclass(frozen=True)
class Violation:
    rule: str  # the rule's name; "layer" for geometry on an undefined layer
    layer: str  # the rule's layers, comma-separated
    message: str
    bbox_um: tuple[float, float, float, float] | None = None  # for highlighting in the GUI
    kind: str = ""
    severity: str = ERROR
    values: dict[str, Any] = field(default_factory=dict)  # as the rule was checked
    waived: str = ""  # the reason, when a waiver accepts this violation
    stale_waiver: bool = False  # a waiver that matches no violation any more
    # The waiver that accepts it (or the stale one): the component it is kept
    # with, and its box there.
    waiver: tuple[str, tuple[float, float, float, float]] | None = None
    measured: float | None = None  # what the kind measured there, in µm (see Finding)
    shapes: tuple[str, ...] = ()  # the shapes or placements it lies on (see name_shapes)
    outline: tuple[tuple[int, int], ...] = ()  # where, exactly (database units)
    holes: tuple[tuple[tuple[int, int], ...], ...] = ()  # and the outline's holes

    @property
    def is_error(self) -> bool:
        """An error that is not waived: what fails a check."""
        return self.severity == ERROR and not self.waived


_runtime: dict[str, type[RuleKind]] = {}


def register_rule_kind(cls: type[RuleKind]) -> type[RuleKind]:
    _runtime[cls.name] = cls
    return cls


def registered_at_runtime() -> bool:
    """Whether a rule kind was registered with :func:`register_rule_kind` (only
    this process knows it: rules are then checked here)."""
    return bool(_runtime)


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


def check(
    project: Project, geometry: Geometry | None = None, component: str | None = None
) -> list[Violation]:
    """Check ``geometry`` against the project's rules; by default the component as
    an export would write it, rounded onto the process's grid (requirement DRC-3).
    ``component`` is what the geometry is (default: the project's default
    component); its waivers mark the violations they accept."""
    component = component or project.default_component()
    if geometry is None:
        geometry = checked_geometry(project, component)
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
            violations += _check_rule(rule.name, rule, kinds, geometry, project, scope, scope_error)
    return _apply_waivers(violations, project, component, geometry)


def checked_geometry(
    project: Project, component: str | None = None, params: dict | None = None
) -> Geometry:
    """A component as the rule checks see it: rounded onto the process's grid, its
    curves within the process's tolerance, as an export would write it."""
    from mems_sketch.engine import Engine

    process = project.process
    build = Engine().load(project).build(project._target(component), params)
    return build.output(process.grid_um, process.chord_um).geometry


# -- waivers -------------------------------------------------------------------

BOX_TOLERANCE_UM = 0.0005  # a waiver's box matches a violation's to within this


def fingerprint(violation: Violation, geometry: Geometry) -> str:
    """A hash of the geometry around a violation, on its rule's layers: its box,
    grown by the largest distance the rule checks with, so that what the rule
    looked at is covered. It is the same wherever the violation is and however
    it is turned by quarter turns or mirrored, so that a waiver follows what it
    accepts when that moves, and into the components that place it."""
    found = _window(violation, geometry)
    if found is None:
        return ""
    regions, width, height = found
    texts = []
    for angle in (0, 90, 180, 270):
        for mirror in (False, True):
            turn = Transform(angle=angle, mirror=mirror)
            corners = [turn.apply_vector(x, y) for x in (0, width) for y in (0, height)]
            left = round(min(x for x, _ in corners))
            bottom = round(min(y for _, y in corners))
            texts.append(
                "|".join(
                    f"{layer}:{_shapes(region.transformed(turn).moved(-left, -bottom))}"
                    for layer, region in regions
                )
            )
    return hashlib.sha256(min(texts).encode()).hexdigest()[:16]


def _window(violation: Violation, geometry: Geometry):
    """The geometry around a violation on each of its layers, moved to start at
    (0, 0), and the window's width and height (database units)."""
    if violation.bbox_um is None:
        return None
    reach = max(
        (abs(v) for v in violation.values.values() if isinstance(v, int | float)), default=0.0
    )
    x0, y0, x1, y1 = (round(c / DBU_UM) for c in violation.bbox_um)
    grow = round(reach / DBU_UM) + 1
    left, bottom = x0 - grow, y0 - grow
    window = Region(Box(left, bottom, x1 + grow, y1 + grow))
    regions = [
        (layer, (geometry.layers.get(layer, Region()) & window).moved(-left, -bottom))
        for layer in violation.layer.split(", ")
    ]
    return regions, x1 - x0 + 2 * grow, y1 - y0 + 2 * grow


def _shapes(region: Region) -> str:
    """The polygons written out in full, the same however they were made: each
    ring from its lowest point, the polygons in order."""

    def ring(points) -> str:
        found = [tuple(q) for q in points.tolist()]
        first = found.index(min(found))
        return str(found[first:] + found[:first])

    return ";".join(
        sorted(ring(p.hull) + str(sorted(ring(h) for h in p.holes)) for p in region.each_merged())
    )


def _former_fingerprint(violation: Violation, geometry: Geometry) -> str:
    """The fingerprint waivers had before they followed what they accept: of the
    geometry where it is (waivers saved then still match there)."""
    if violation.bbox_um is None:
        return ""
    reach = max(
        (abs(v) for v in violation.values.values() if isinstance(v, int | float)), default=0.0
    )
    x0, y0, x1, y1 = (round(c / DBU_UM) for c in violation.bbox_um)
    grow = round(reach / DBU_UM) + 1
    window = Region(Box(x0 - grow, y0 - grow, x1 + grow, y1 + grow))
    digest = hashlib.sha256()
    for layer in violation.layer.split(", "):
        region = geometry.layers.get(layer, Region())
        shapes = sorted(_text(p) for p in (region & window).each_merged())
        digest.update(f"{layer}:{';'.join(shapes)}|".encode())
    return digest.hexdigest()[:16]


def _text(polygon) -> str:
    """A polygon written out in full (as its points, outline then holes)."""
    hull = [tuple(q) for q in polygon.hull.tolist()]
    return str((hull, [[tuple(q) for q in h.tolist()] for h in polygon.holes]))


def _same_box(a, b) -> bool:
    return all(abs(p - q) <= BOX_TOLERANCE_UM for p, q in zip(a, b, strict=True))


def _placed_components(project: Project, component: str) -> list[str]:
    """The user components ``component`` places, directly or further down."""
    found: list[str] = []
    pending = [component]
    while pending:
        for _, target in project.references_of(pending.pop()):
            if target not in found and target != component:
                found.append(target)
                pending.append(target)
    return found


def _apply_waivers(
    violations: list[Violation], project: Project, component: str | None, geometry: Geometry
) -> list[Violation]:
    """Mark the violations the waivers accept: the component's own, each for one
    violation (where it was waived, or where what it waived has moved to), and
    those of the components it places, for that violation in each placement.
    An own waiver whose place has a violation with other geometry around it
    has lapsed; one that matches nothing asks to be removed."""
    found = project.definition(component) if component else None
    own = list(found[0].waivers) if found else []
    placed = [
        (name, waiver)
        for name in (_placed_components(project, component) if component else [])
        if (inner := project.definition(name)) is not None
        for waiver in inner[0].waivers
    ]
    if not own and not placed:
        return violations
    rules = {w.rule for w in own} | {w.rule for _, w in placed}
    prints: dict[int, str] = {
        i: fingerprint(v, geometry)
        for i, v in enumerate(violations)
        if v.rule in rules and v.bbox_um is not None
    }
    accepted: dict[int, tuple[str, Any]] = {}  # violation -> (component, waiver)
    used: set[int] = set()  # own waivers

    def take(index: int, which: int) -> None:
        accepted[index] = (component, own[which])
        used.add(which)

    # Own waivers where they were made, then those whose violation moved.
    for index, violation in enumerate(violations):
        for which, waiver in enumerate(own):
            if (
                which not in used
                and index in prints
                and waiver.rule == violation.rule
                and _same_box(waiver.box, violation.bbox_um)
                and waiver.fingerprint in (prints[index], _former_fingerprint(violation, geometry))
            ):
                take(index, which)
                break
    for index, violation in enumerate(violations):
        if index in accepted or index not in prints:
            continue
        for which, waiver in enumerate(own):
            if (
                which not in used
                and waiver.rule == violation.rule
                and waiver.fingerprint == prints[index]
            ):
                take(index, which)
                break
        else:
            for name, waiver in placed:
                if waiver.rule == violation.rule and waiver.fingerprint == prints[index]:
                    accepted[index] = (name, waiver)
                    break

    result: list[Violation] = []
    for index, violation in enumerate(violations):
        if index in accepted:
            name, waiver = accepted[index]
            result.append(
                dataclasses.replace(violation, waived=waiver.reason, waiver=(name, waiver.box))
            )
            continue
        lapsed = next(
            (
                (which, w)
                for which, w in enumerate(own)
                if which not in used
                and violation.bbox_um is not None
                and w.rule == violation.rule
                and _same_box(w.box, violation.bbox_um)
            ),
            None,
        )
        if lapsed is None:
            result.append(violation)
            continue
        which, waiver = lapsed  # the geometry changed: the waiver no longer covers it
        used.add(which)
        message = f"{violation.message} (waiver lapsed: the geometry changed)"
        result.append(
            dataclasses.replace(violation, message=message, waiver=(component, waiver.box))
        )
    for which, waiver in enumerate(own):
        if which not in used:
            x0, y0, x1, y1 = waiver.box
            result.append(
                Violation(
                    waiver.rule,
                    "",
                    f"a waiver ({waiver.reason}) at ({(x0 + x1) / 2:.3f}, {(y0 + y1) / 2:.3f}) "
                    "µm matches no violation any more: remove it",
                    waiver.box,
                    severity=WARNING,
                    stale_waiver=True,
                    waiver=(component, waiver.box),
                )
            )
    return result


# What a rule kind found, by the kind, its values and the geometry it checked
# (a hash of it): a refresh that changed nothing on a rule's layers, or an
# undo, finds the result here instead of checking again.
_FINDINGS: dict[tuple, list[Finding]] = {}


def _check_rule(name, rule, kinds, geometry, project, scope, scope_error) -> list[Violation]:
    layers = ", ".join(rule.layers)

    def problem(message: str) -> list[Violation]:
        # A rule that cannot be checked never passes (requirement DRC-10).
        return [Violation(name, layers, message, kind=rule.kind, severity=ERROR)]

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
    if isinstance(scope_error, str):
        return problem(scope_error)
    if scope_error is not None:
        return problem(f"the process constants have an error: {scope_error}")
    try:
        values = rule_values(kind, rule, scope)
    except Exception as exc:  # noqa: BLE001 - a bad value is the rule's problem
        return problem(f"{exc}: not checked")
    regions = [geometry.layers.get(n, Region()) for n in rule.layers]
    # A kind that compares sizes may allow for rounding onto the process's grid.
    extra = {"grid": project.process.grid_um} if _takes(kind, "grid") else {}
    key = (
        rule.kind,
        type(kind),
        repr(sorted((values | extra).items())),
        tuple(r.digest() for r in regions),
    )
    findings = _FINDINGS.get(key)
    if findings is None:
        findings = kind().check(regions, DBU_UM, **values, **extra)
        if len(_FINDINGS) >= 256:
            _FINDINGS.clear()
        _FINDINGS[key] = findings
    return [
        Violation(
            name,
            layers,
            f"{f.message}{' (' + rule.message + ')' if rule.message else ''}",
            f.bbox_um,
            kind=rule.kind,
            severity=rule.severity,
            values=values,
            measured=f.measured,
            outline=f.outline,
            holes=f.holes,
        )
        for f in findings
    ]


def _takes(kind: type[RuleKind], name: str) -> bool:
    """Whether a kind's check takes the keyword ``name`` (an optional extra)."""
    try:
        return name in inspect.signature(kind.check).parameters
    except (TypeError, ValueError):
        return False


def records_of(project: Project, component: str, params: dict | None = None) -> dict:
    """A component's nodes as built (path -> NodeRecord), for :func:`name_shapes`;
    empty when it does not build (the violations are then reported unnamed)."""
    from mems_sketch.engine import Engine

    try:
        return Engine().load(project).build(project._target(component), params).records()
    except Exception:  # noqa: BLE001 - see above
        return {}


def name_shapes(
    violations: Sequence[Violation], project: Project, component: str, records: Mapping
) -> list[Violation]:
    """The violations, each with the top-level shapes or placements it lies on, by
    name (``records``: the component's nodes as built, path -> NodeRecord): which
    shape to change."""
    found = project.definition(component)
    if found is None:
        return list(violations)
    shapes = found[0].shapes
    nodes = []
    for path, record in records.items():
        if len(path) == 1 and path[0][0] == 0 and path[0][1] < len(shapes):
            node = shapes[path[0][1]]
            nodes.append((node.name or node.kind, record.geometry))
    named = []
    for violation in violations:
        if violation.bbox_um is None or violation.shapes:
            named.append(violation)
            continue
        if violation.outline:
            where = Region(IntPolygon(list(violation.outline), [list(h) for h in violation.holes]))
        else:
            x0, y0, x1, y1 = (round(c / DBU_UM) for c in violation.bbox_um)
            where = Region(Box(x0, y0, x1, y1))
        # Inside it, clear of rounding onto the grid (the shapes are as built, the
        # violation as checked): a shape that only touches it is not on it.
        inside = where.sized(-2)
        if inside.is_empty():  # a sliver: whatever it touches
            inside = where.sized(2)
        layers = violation.layer.split(", ")

        def lying_on(area: Region) -> list[str]:
            on = []
            for name, geometry in nodes:
                for layer in layers:
                    region = geometry.layers.get(layer)
                    if region is not None and not (region & area).is_empty():
                        on.append(name)
                        break
            return on

        # A gap lies on no shape: then the shapes on either side of it (#300).
        on = lying_on(inside) or lying_on(where.sized(2))
        named.append(dataclasses.replace(violation, shapes=tuple(dict.fromkeys(on))))
    return named


def errors(violations: Sequence[Violation]) -> list[Violation]:
    return [v for v in violations if v.is_error]


def warnings(violations: Sequence[Violation]) -> list[Violation]:
    return [v for v in violations if v.severity == WARNING and not v.waived]


def open_violations(violations: Sequence[Violation]) -> list[Violation]:
    """The violations no waiver accepts."""
    return [v for v in violations if not v.waived]
