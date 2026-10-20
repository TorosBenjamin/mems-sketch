"""User-defined components: a parameter list plus a parametric shape tree.

A :class:`ComponentDef` is plain data, so it can be saved in the design file
and edited in the GUI. Its shapes are the nodes from :mod:`mems_sketch.core.shapes`
(primitives, references and boolean/geometric operations); every value in them
may be an expression over the component's own parameters.

Example (a plate with a grid of release holes)::

    ComponentDef(
        name="perforated_plate",
        parameters=[
            ParamDef(name="size", default=100),
            ParamDef(name="hole", default=4),
            ParamDef(name="pitch", default=12),
        ],
        shapes=[
            BooleanShape(
                op="subtract",
                a=[RectShape(layer="device", x0=0, y0=0, x1="size", y1="size")],
                b=[RectShape(layer="device", x0="pitch/2", y0="pitch/2",
                             x1="pitch/2 + hole", y1="pitch/2 + hole",
                             repeat=Repeat(columns="floor(size/pitch)", rows="floor(size/pitch)",
                                           dx="pitch", dy="pitch"))],
            )
        ],
    )
"""

from __future__ import annotations

import keyword
import re
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator, model_validator

from mems_sketch.core.component import Component, Params
from mems_sketch.core.expressions import RESERVED_NAMES, evaluate, resolve_variables
from mems_sketch.core.levels import DEFAULT_STACK, Stack
from mems_sketch.core.shapes import (
    BBOX_POINTS,
    INDEX_NAMES,
    Shape,
    check_point_reference,
    map_expressions,
    point_renamer,
    references,
    rename_node_references,
    rewrite,
    walk,
)

Value = float | str
RESERVED_PARAM_NAMES = frozenset({*INDEX_NAMES, *RESERVED_NAMES, "process"})


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ParamDef(_Model):
    """A component parameter.

    ``default`` may be a number or an expression over the component's other
    parameters and ``process.*`` constants, e.g. ``gap`` defaulting to
    ``"1.5 * width"``. Defaults are resolved in dependency order, after the
    values given by the caller, and then checked against ``min``/``max``.
    Limits may be expressions too (``max="size / 2"``), over the other
    parameters and the process constants; an exclusive limit refuses the value
    equal to it.

    An ``internal`` parameter is used only by the component itself (often a
    derived value such as ``pitch = width + gap``): where the component is
    placed it is not offered and cannot be set. Public ones are its interface.
    """

    name: str
    default: Value = 0.0
    min: Value | None = None
    max: Value | None = None
    min_exclusive: bool = False
    max_exclusive: bool = False
    integer: bool = False
    internal: bool = False
    description: str = ""

    @field_validator("name")
    @classmethod
    def _valid_name(cls, name: str) -> str:
        if not name.isidentifier() or keyword.iskeyword(name):
            raise ValueError(f"'{name}' is not a valid parameter name")
        if name in RESERVED_PARAM_NAMES:
            raise ValueError(f"'{name}' is reserved")
        return name

    @model_validator(mode="after")
    def _numeric_default_in_range(self):
        low, high = self.min, self.max
        if isinstance(low, int | float) and isinstance(high, int | float) and low > high:
            raise ValueError(f"the min of '{self.name}' ({low:g}) is more than its max ({high:g})")
        if isinstance(self.default, str):
            return self  # checked when the expression is resolved
        if self.integer and self.default != int(self.default):
            raise ValueError(f"default of integer parameter '{self.name}' is not an integer")
        numbers = {
            k: v for k, v in (("min", self.min), ("max", self.max)) if not isinstance(v, str)
        }
        problem = self.out_of_range(self.default, numbers.get("min"), numbers.get("max"))
        if problem:
            raise ValueError(f"default of '{self.name}' {problem}")
        return self

    def limits(self, variables: Mapping[str, float]) -> tuple[float | None, float | None]:
        """``min`` and ``max`` evaluated with the component's values and constants."""
        return tuple(  # type: ignore[return-value]
            None if limit is None else evaluate(limit, variables) for limit in (self.min, self.max)
        )

    def out_of_range(self, value: float, low: float | None, high: float | None) -> str | None:
        """What is wrong with ``value`` against these limits, or nothing."""
        if low is not None and (value <= low if self.min_exclusive else value < low):
            return f"must be {'more than' if self.min_exclusive else 'at least'} {low:g}, not {value:g}"
        if high is not None and (value >= high if self.max_exclusive else value > high):
            return f"must be {'less than' if self.max_exclusive else 'at most'} {high:g}, not {value:g}"
        return None


class PointDef(_Model):
    """A named alignment point of a component, e.g. where a spring attaches.

    The position is ``(x, y)``, measured from the point ``at`` when given: a
    point of one of the component's own top-level shapes (``node.point``) or of
    the whole component (``center``, ``top_left``, …). Else from the origin. Both may be expressions over the component's parameters.
    """

    name: str
    at: str | None = None
    x: Value = 0.0
    y: Value = 0.0
    description: str = ""

    @field_validator("name")
    @classmethod
    def _valid_name(cls, name: str) -> str:
        if not name.isidentifier() or keyword.iskeyword(name):
            raise ValueError(f"'{name}' is not a valid point name")
        if name in BBOX_POINTS:
            raise ValueError(f"'{name}' is a bounding-box point every shape already has")
        return name

    @field_validator("at")
    @classmethod
    def _reference(cls, at: str | None) -> str | None:
        if at is None or at in BBOX_POINTS:  # e.g. "center": of the whole component
            return at
        return check_point_reference(at)


# The directions a port's wire can leave in by name, and their angles (degrees).
DIRECTIONS = {"right": 0.0, "up": 90.0, "left": 180.0, "down": 270.0}


class PortDef(_Model):
    """Where a wire attaches to a component, so a route can meet it cleanly.

    It is at a point measured like a :class:`PointDef`'s (``at``, ``x``, ``y``).
    ``direction`` is the way a wire leaves it: ``right``, ``up``, ``left``,
    ``down`` or an angle (degrees, counter-clockwise from +x; an expression);
    ``width`` the width of wire it expects; ``layer`` the layer the wire runs
    on, like a shape's (``level``, ``level+1``, ``metal``). At a placed
    component's port (``at: pad.port.signal``) it takes that port's direction,
    width and layer unless it gives its own; else it points right, takes any
    width (0) and is on the component's level.

    Where the component is placed, its port ``drive`` is also its point
    ``port.drive`` (``comb1.port.drive``), turned and mirrored with it; an
    array's copies have theirs by index (``comb1.port.drive[2]``).
    """

    name: str
    at: str | None = None
    x: Value = 0.0
    y: Value = 0.0
    direction: Value | None = None
    width: Value | None = None
    layer: str | None = None
    description: str = ""

    @field_validator("name")
    @classmethod
    def _valid_name(cls, name: str) -> str:
        if not name.isidentifier() or keyword.iskeyword(name):
            raise ValueError(f"'{name}' is not a valid port name")
        return name

    @field_validator("at")
    @classmethod
    def _reference(cls, at: str | None) -> str | None:
        if at is None or at in BBOX_POINTS:
            return at
        return check_point_reference(at)


class Waiver(_Model):
    """An accepted rule violation (requirement DRC-13): the rule, where its
    violation is (a box in µm), why it is accepted, and a fingerprint of the
    geometry there. When that geometry changes, the waiver lapses and the
    violation shows again."""

    rule: str
    box: tuple[float, float, float, float]
    reason: str
    fingerprint: str


class ComponentDef(_Model):
    """A user component.

    ``name`` is its path: ``plate`` for a shared component, ``comb/finger`` for
    ``finger``, a *private* component of ``comb`` (see
    :mod:`mems_sketch.core.project` for what can place it).

    ``level`` is the level of the layer stack it is on unless a placement says
    otherwise; without one it is on the level of whatever places it
    (:mod:`mems_sketch.core.levels`).

    ``category`` is where it is shown (``springs``, ``electrodes/comb``): the
    folder it is in, not part of its name. ``tags`` are words to find it by.
    """

    name: str
    description: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    level: str | None = None
    parameters: list[ParamDef] = Field(default_factory=list)
    points: list[PointDef] = Field(default_factory=list)
    ports: list[PortDef] = Field(default_factory=list)
    shapes: list[Shape] = Field(default_factory=list)
    waivers: list[Waiver] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, name: str) -> str:
        if not all(part.isidentifier() for part in name.split("/")):
            raise ValueError(f"'{name}' is not a valid component name")
        return name

    @field_validator("category")
    @classmethod
    def _valid_category(cls, category: str) -> str:
        if category and not all(part.isidentifier() for part in category.split("/")):
            raise ValueError(f"'{category}' is not a category: folder names, like 'springs'")
        return category

    @model_validator(mode="after")
    def _shapes_named(self):
        if any(shape.name is None for shape in walk(self.shapes)):
            self.shapes = [shape.model_copy(deep=True) for shape in self.shapes]  # not the caller's
            name_shapes(self.shapes)
        return self

    @property
    def short_name(self) -> str:
        """The name without its owners: ``finger`` for ``comb/finger``."""
        return self.name.rpartition("/")[2]

    @property
    def owner(self) -> str | None:
        """The component this one is private to, or None for a shared one."""
        return self.name.rpartition("/")[0] or None

    @model_validator(mode="after")
    def _unique_params(self):
        names = [p.name for p in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("parameter names must be unique")
        points = [p.name for p in self.points]
        if len(points) != len(set(points)):
            raise ValueError("point names must be unique")
        ports = [p.name for p in self.ports]
        if len(ports) != len(set(ports)):
            raise ValueError("port names must be unique")
        return self

    def references(self) -> set[str]:
        return references(self.shapes)

    def parameter(self, name: str) -> ParamDef:
        for p in self.parameters:
            if p.name == name:
                return p
        raise KeyError(f"component '{self.name}' has no parameter '{name}'")

    def rename_point(self, old: str, new: str) -> None:
        """Rename one of this component's declared points (see
        :meth:`Project.rename_point` for the components that place it)."""
        if any(p.name == new for p in self.points):
            raise ValueError(f"point '{new}' already exists")
        for index, point in enumerate(self.points):
            if point.name == old:
                self.points[index] = PointDef.model_validate({**point.model_dump(), "name": new})
                return
        raise KeyError(f"component '{self.name}' has no point '{old}'")

    def rename_port(self, old: str, new: str) -> None:
        """Rename one of this component's ports (see :meth:`Project.rename_port` for
        the components that place it)."""
        if any(p.name == new for p in self.ports):
            raise ValueError(f"port '{new}' already exists")
        for index, port in enumerate(self.ports):
            if port.name == old:
                self.ports[index] = PortDef.model_validate({**port.model_dump(), "name": new})
                return
        raise KeyError(f"component '{self.name}' has no port '{old}'")

    def rename_parameter(self, old: str, new: str) -> None:
        """Rename one of this component's parameters, and the expressions that use
        it: other parameters' defaults and limits, shapes, points and ports (see
        :meth:`Project.rename_parameter` for the components that place it)."""
        if any(p.name == new for p in self.parameters):
            raise ValueError(f"parameter '{new}' already exists")
        if all(p.name != old for p in self.parameters):
            raise KeyError(f"component '{self.name}' has no parameter '{old}'")

        def change(name: str) -> str | None:
            return new if name == old else None

        self.parameters = [
            ParamDef.model_validate(
                {
                    **p.model_dump(),
                    "name": new if p.name == old else p.name,
                    **{k: rewrite(getattr(p, k), change) for k in ("default", "min", "max")},
                }
            )
            for p in self.parameters
        ]
        self.shapes = map_expressions(self.shapes, change)
        self._rewrite_points(change)

    def follow_point_rename(self, nodes: set[str], old: str, new: str) -> None:
        """Update what uses point ``old`` of the nodes named in ``nodes``, which place
        a component whose point is now called ``new``: alignments, expressions and
        this component's own points."""
        change = point_renamer(nodes, old, new)
        self.shapes = map_expressions(self.shapes, change)
        self._rewrite_points(change)

    def rename_shape(self, old: str, new: str) -> None:
        """Rename a shape and update the alignments and expressions that use its points."""
        if any(s.name == new for s in walk(self.shapes)):
            raise ValueError(f"a shape named '{new}' already exists")
        shapes = rename_node_references(self.shapes, old, new)
        for node in walk(shapes):
            if node.name == old:
                node.name = new
        self.shapes = shapes

        def change(name: str) -> str | None:
            head, dot, rest = name.partition(".")
            return f"{new}.{rest}" if head == old and dot else None

        self._rewrite_points(change)

    def _rewrite_points(self, change: Callable[[str], str | None]) -> None:
        """Pass the names this component's points and ports measure from through ``change``."""

        def rewritten(p):
            update = {"at": p.at and (change(p.at) or p.at), "x": rewrite(p.x, change)}
            update["y"] = rewrite(p.y, change)
            for key in ("direction", "width"):
                if getattr(p, key, None) is not None:
                    update[key] = rewrite(getattr(p, key), change)
            return p.model_copy(update=update)

        self.points = [rewritten(p) for p in self.points]
        self.ports = [rewritten(p) for p in self.ports]


def name_shapes(shapes: list[Shape]) -> None:
    """Give every unnamed shape a name: its kind (a placement: its component's
    name) and the lowest number not taken, ``rect1``, ``anchor2``."""
    taken = {shape.name for shape in walk(shapes) if shape.name}
    for shape in walk(shapes):
        if shape.name is None:
            stem = shape.kind
            if isinstance(getattr(shape, "component", None), str):
                stem = re.split(r"[./]", shape.component)[-1]
            n = 1
            while f"{stem}{n}" in taken:
                n += 1
            shape.name = f"{stem}{n}"
            taken.add(shape.name)


class UserComponent(Component):
    """Adapts a :class:`ComponentDef` to the :class:`Component` interface: its
    parameters, as a placement sees them (the engine builds it). ``scope``
    holds the ``process.*`` constants visible to every expression.
    """

    def __init__(
        self,
        definition: ComponentDef,
        scope: Mapping[str, float] | None = None,
        stack: Stack = DEFAULT_STACK,
    ) -> None:
        self.definition = definition
        self.type_name = definition.name
        self.default_level = definition.level
        self.scope = dict(scope or {})
        self.stack = stack
        self.Params = _params_model(definition, self.scope)
        self.internal = frozenset(p.name for p in definition.parameters if p.internal)


def _number_limits(p: ParamDef) -> dict[str, float]:
    """A parameter's limits that are numbers, as pydantic field constraints (which
    the editor reads to clamp a typed value); expressions are checked later."""
    limits = {}
    if p.min is not None and not isinstance(p.min, str):
        limits["gt" if p.min_exclusive else "ge"] = p.min
    if p.max is not None and not isinstance(p.max, str):
        limits["lt" if p.max_exclusive else "le"] = p.max
    return limits


def _params_model(definition: ComponentDef, scope: Mapping[str, float]) -> type[Params]:
    fields: dict[str, Any] = {
        p.name: (
            int if p.integer else float,
            Field(description=p.description, **_number_limits(p)),
        )
        for p in definition.parameters
    }
    expression_limits = [
        p for p in definition.parameters if isinstance(p.min, str) or isinstance(p.max, str)
    ]
    base = create_model(f"{definition.name}_Params", __base__=Params, **fields)
    defaults = {p.name: p.default for p in definition.parameters}

    class ResolvedParams(base):  # type: ignore[valid-type, misc]
        @model_validator(mode="before")
        @classmethod
        def _fill_defaults(cls, data: Any) -> Any:
            data = dict(data or {})
            given = {k: float(v) for k, v in data.items() if isinstance(v, int | float)}
            missing = {k: v for k, v in defaults.items() if k not in data}
            data.update(resolve_variables(missing, {**scope, **given}))
            return data

        @model_validator(mode="after")
        def _limits_hold(self):
            if not expression_limits:
                return self
            variables = {**scope, **{k: float(v) for k, v in self.model_dump().items()}}
            for p in expression_limits:
                value = variables[p.name]
                problem = p.out_of_range(value, *p.limits(variables))
                if problem:
                    raise ValueError(f"parameter '{p.name}' of '{definition.name}' {problem}")
            return self

    ResolvedParams.__name__ = base.__name__
    return ResolvedParams
