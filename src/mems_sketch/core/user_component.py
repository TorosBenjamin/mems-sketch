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
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator, model_validator

from mems_sketch.core.component import Component, Geometry, Params
from mems_sketch.core.expressions import RESERVED_NAMES, evaluate, resolve_variables
from mems_sketch.core.shapes import (
    BBOX_POINTS,
    INDEX_NAMES,
    Evaluator,
    NodePoints,
    Point,
    Shape,
    check_point_reference,
    point_values,
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

    An ``internal`` parameter is used only by the component itself (often a
    derived value such as ``pitch = width + gap``): where the component is
    placed it is not offered and cannot be set. Public ones are its interface.
    """

    name: str
    default: Value = 0.0
    min: float | None = None
    max: float | None = None
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
        if isinstance(self.default, str):
            return self  # checked when the expression is resolved
        if self.min is not None and self.default < self.min:
            raise ValueError(f"default of '{self.name}' is below its minimum")
        if self.max is not None and self.default > self.max:
            raise ValueError(f"default of '{self.name}' is above its maximum")
        if self.integer and self.default != int(self.default):
            raise ValueError(f"default of integer parameter '{self.name}' is not an integer")
        return self


class PointDef(_Model):
    """A named alignment point of a component, e.g. where a spring attaches.

    The position is ``(x, y)``, measured from the point ``at`` (``node.point``,
    a point of one of the component's own shapes) when given, else from the
    origin. Both may be expressions over the component's parameters.
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
        return None if at is None else check_point_reference(at)


class ComponentDef(_Model):
    """A user component.

    ``name`` is its path: ``plate`` for a shared component, ``comb/finger`` for
    ``finger``, a *private* component of ``comb`` (see
    :mod:`mems_sketch.core.project` for what can place it).
    """

    name: str
    description: str = ""
    parameters: list[ParamDef] = Field(default_factory=list)
    points: list[PointDef] = Field(default_factory=list)
    shapes: list[Shape] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, name: str) -> str:
        if not all(part.isidentifier() for part in name.split("/")):
            raise ValueError(f"'{name}' is not a valid component name")
        return name

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
        return self

    def references(self) -> set[str]:
        return references(self.shapes)

    def parameter(self, name: str) -> ParamDef:
        for p in self.parameters:
            if p.name == name:
                return p
        raise KeyError(f"component '{self.name}' has no parameter '{name}'")

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

        self.points = [
            p.model_copy(
                update={
                    "at": p.at and (change(p.at) or p.at),
                    "x": rewrite(p.x, change),
                    "y": rewrite(p.y, change),
                }
            )
            for p in self.points
        ]


class UserComponent(Component):
    """Adapts a :class:`ComponentDef` to the :class:`Component` interface.

    ``lookup`` resolves the names used by ``ref`` shapes; ``scope`` holds the
    ``process.*`` constants visible to every expression.
    """

    def __init__(
        self,
        definition: ComponentDef,
        lookup: Callable[[str], Component],
        scope: Mapping[str, float] | None = None,
    ) -> None:
        self.definition = definition
        self.type_name = definition.name
        self.scope = dict(scope or {})
        self.Params = _params_model(definition, self.scope)
        self.internal = frozenset(p.name for p in definition.parameters if p.internal)
        self._lookup = lookup

    def build(self, params: Params) -> Geometry:
        return self.compile(params)[0]

    def points(self, params: Params) -> dict[str, Point]:
        return self.compile(params)[1]

    def compile(self, params: Params) -> tuple[Geometry, dict[str, Point]]:
        variables = {**self.scope, **{k: float(v) for k, v in params.model_dump().items()}}
        geometry, local = Evaluator(self._lookup).render_scoped(self.definition.shapes, variables)
        return geometry, declared_points(self.definition, variables, local)


def declared_points(
    definition: ComponentDef, variables: dict[str, float], shapes: dict[str, NodePoints]
) -> dict[str, Point]:
    """Positions of a component's declared points, given its evaluated top-level shapes."""
    result = {}
    for point in definition.points:
        v = {**variables, "i": 0.0, "j": 0.0}
        v.update(point_values([e for e in (point.x, point.y) if isinstance(e, str)], shapes))
        base = (0.0, 0.0)
        if point.at is not None:
            node, _, name = point.at.partition(".")
            if node not in shapes:
                raise ValueError(
                    f"point '{point.name}' is at '{point.at}', but there is no shape '{node}'"
                )
            base = shapes[node].point(name)
        result[point.name] = (base[0] + evaluate(point.x, v), base[1] + evaluate(point.y, v))
    return result


def _params_model(definition: ComponentDef, scope: Mapping[str, float]) -> type[Params]:
    fields: dict[str, Any] = {
        p.name: (
            int if p.integer else float,
            Field(ge=p.min, le=p.max, description=p.description),
        )
        for p in definition.parameters
    }
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

    ResolvedParams.__name__ = base.__name__
    return ResolvedParams
