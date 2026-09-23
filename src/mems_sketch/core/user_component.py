"""User-defined components built from parametric primitives.

A :class:`ComponentDef` is plain data (so it can be saved in the design file
and edited in the GUI). It declares its own parameters and a list of shapes:

* ``polygon``: a layer and a list of points
* ``rect``: a layer and two corners
* ``ref``: another component (built-in or user-defined) with its parameters

Every coordinate and parameter may be an expression over the component's own
parameters. Any shape can be repeated on a grid; inside a repeated shape the
column and row indices are available as ``i`` and ``j``, so e.g. a tapered
finger array is ``{"kind": "rect", ..., "x1": "w + i * taper"}``.

Example (a T-shaped anchor)::

    ComponentDef(
        name="t_anchor",
        parameters=[ParamDef(name="w", default=10), ParamDef(name="h", default=30)],
        shapes=[
            RectShape(layer="device", x0="-w/2", y0=0, x1="w/2", y1="h"),
            RectShape(layer="device", x0="-h/2", y0="h", x1="h/2", y1="h + w"),
        ],
    )
"""

from __future__ import annotations

import keyword
from collections.abc import Callable
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator, model_validator

from mems_sketch.core.component import Component, Geometry, Params, placement, resolve_params
from mems_sketch.core.expressions import RESERVED_NAMES, evaluate

Value = float | str  # a number or an expression over the component parameters
INDEX_NAMES = ("i", "j")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ParamDef(_Model):
    name: str
    default: float
    min: float | None = None
    max: float | None = None
    integer: bool = False
    description: str = ""

    @field_validator("name")
    @classmethod
    def _valid_name(cls, name: str) -> str:
        if not name.isidentifier() or keyword.iskeyword(name):
            raise ValueError(f"'{name}' is not a valid parameter name")
        if name in INDEX_NAMES or name in RESERVED_NAMES:
            raise ValueError(f"'{name}' is reserved")
        return name

    @model_validator(mode="after")
    def _default_in_range(self):
        if self.min is not None and self.default < self.min:
            raise ValueError(f"default of '{self.name}' is below its minimum")
        if self.max is not None and self.default > self.max:
            raise ValueError(f"default of '{self.name}' is above its maximum")
        if self.integer and self.default != int(self.default):
            raise ValueError(f"default of integer parameter '{self.name}' is not an integer")
        return self


class Repeat(_Model):
    columns: Value = 1
    rows: Value = 1
    dx: Value = 0.0
    dy: Value = 0.0


class PolygonShape(_Model):
    kind: Literal["polygon"] = "polygon"
    layer: str
    points: list[tuple[Value, Value]] = Field(min_length=3)
    repeat: Repeat | None = None


class RectShape(_Model):
    kind: Literal["rect"] = "rect"
    layer: str
    x0: Value
    y0: Value
    x1: Value
    y1: Value
    repeat: Repeat | None = None


class RefShape(_Model):
    kind: Literal["ref"] = "ref"
    component: str
    params: dict[str, Value] = Field(default_factory=dict)
    x: Value = 0.0
    y: Value = 0.0
    rotation: Value = 0.0
    mirror_x: bool = False
    repeat: Repeat | None = None


Shape = Annotated[PolygonShape | RectShape | RefShape, Field(discriminator="kind")]


class ComponentDef(_Model):
    name: str
    description: str = ""
    parameters: list[ParamDef] = Field(default_factory=list)
    shapes: list[Shape] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, name: str) -> str:
        if not name.isidentifier():
            raise ValueError(f"'{name}' is not a valid component name")
        return name

    @model_validator(mode="after")
    def _unique_params(self):
        names = [p.name for p in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("parameter names must be unique")
        return self

    def references(self) -> set[str]:
        return {shape.component for shape in self.shapes if isinstance(shape, RefShape)}


class UserComponent(Component):
    """Adapts a :class:`ComponentDef` to the :class:`Component` interface.

    ``lookup`` resolves the names used by ``ref`` shapes (normally
    :meth:`Design.component`).
    """

    def __init__(self, definition: ComponentDef, lookup: Callable[[str], Component]) -> None:
        self.definition = definition
        self.type_name = definition.name
        self.Params = _params_model(definition)
        self._lookup = lookup

    def build(self, params: Params) -> Geometry:
        base = {name: float(value) for name, value in params.model_dump().items()}
        geometry = Geometry()
        for shape in self.definition.shapes:
            for variables in _grid(shape.repeat, base):
                self._add_shape(geometry, shape, variables)
        return geometry

    def _add_shape(self, geometry: Geometry, shape: Shape, v: dict[str, float]) -> None:
        dx, dy = v["_dx"], v["_dy"]
        match shape:
            case RectShape():
                x0, y0, x1, y1 = (evaluate(c, v) for c in (shape.x0, shape.y0, shape.x1, shape.y1))
                geometry.add_rect(shape.layer, x0 + dx, y0 + dy, x1 + dx, y1 + dy)
            case PolygonShape():
                points = [(evaluate(x, v) + dx, evaluate(y, v) + dy) for x, y in shape.points]
                geometry.add_polygon(shape.layer, points)
            case RefShape():
                child = self._lookup(shape.component)
                built = child.build(resolve_params(child, shape.params, v))
                transform = placement(
                    evaluate(shape.x, v) + dx,
                    evaluate(shape.y, v) + dy,
                    evaluate(shape.rotation, v),
                    shape.mirror_x,
                )
                geometry.merge(built, transform)


def _params_model(definition: ComponentDef) -> type[Params]:
    fields = {
        p.name: (
            int if p.integer else float,
            Field(
                int(p.default) if p.integer else p.default,
                ge=p.min,
                le=p.max,
                description=p.description,
            ),
        )
        for p in definition.parameters
    }
    return create_model(f"{definition.name}_Params", __base__=Params, **fields)


def _grid(repeat: Repeat | None, base: dict[str, float]):
    """Yield the variable set for every grid position, with ``i``, ``j`` and the offset."""
    if repeat is None:
        yield {**base, "i": 0.0, "j": 0.0, "_dx": 0.0, "_dy": 0.0}
        return
    columns = evaluate(repeat.columns, base)
    rows = evaluate(repeat.rows, base)
    if columns != int(columns) or rows != int(rows) or columns < 0 or rows < 0:
        raise ValueError("repeat columns and rows must be non-negative integers")
    pitch_x, pitch_y = evaluate(repeat.dx, base), evaluate(repeat.dy, base)
    for j in range(int(rows)):
        for i in range(int(columns)):
            yield {**base, "i": float(i), "j": float(j), "_dx": i * pitch_x, "_dy": j * pitch_y}
