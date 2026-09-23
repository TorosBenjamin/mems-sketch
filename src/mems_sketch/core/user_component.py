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
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator, model_validator

from mems_sketch.core.component import Component, Geometry, Params
from mems_sketch.core.expressions import RESERVED_NAMES
from mems_sketch.core.shapes import INDEX_NAMES, Evaluator, Shape, references


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
        return references(self.shapes)


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
        variables = {name: float(value) for name, value in params.model_dump().items()}
        return Evaluator(self._lookup).render(self.definition.shapes, variables)


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
