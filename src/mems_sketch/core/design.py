"""The design model: process layers, global variables and component instances.

Parameter values and positions may be numbers or expressions over the global
variables, e.g. ``{"gap": "min_gap * 1.5"}``. Geometry is always regenerated
from this model; it is never stored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import klayout.db as kdb

from mems_sketch.core.component import Geometry, Params, get_component, to_dbu
from mems_sketch.core.expressions import evaluate, resolve_variables

Value = float | str  # a number or an expression


@dataclass
class Layer:
    """A process layer. ``undercut`` is the lateral etch loss per edge, in µm."""

    name: str
    gds_layer: int
    gds_datatype: int = 0
    undercut: float = 0.0
    min_width: float | None = None
    min_space: float | None = None


@dataclass
class Instance:
    name: str
    component: str
    params: dict[str, Any] = field(default_factory=dict)
    x: Value = 0.0
    y: Value = 0.0
    rotation: Value = 0.0  # degrees, counter-clockwise
    mirror_x: bool = False


@dataclass
class Design:
    name: str = "untitled"
    layers: dict[str, Layer] = field(default_factory=dict)
    variables: dict[str, Value] = field(default_factory=dict)
    instances: list[Instance] = field(default_factory=list)

    # -- editing -----------------------------------------------------------

    def add_layer(self, layer: Layer) -> Layer:
        self.layers[layer.name] = layer
        return layer

    def set_variable(self, name: str, value: Value) -> None:
        if not name.isidentifier():
            raise ValueError(f"'{name}' is not a valid variable name")
        self.variables[name] = value

    def add_instance(self, instance: Instance) -> Instance:
        if any(existing.name == instance.name for existing in self.instances):
            raise ValueError(f"an instance named '{instance.name}' already exists")
        self.resolve_params(instance)  # fail early on bad parameters
        self.instances.append(instance)
        return instance

    def instance(self, name: str) -> Instance:
        for inst in self.instances:
            if inst.name == name:
                return inst
        raise KeyError(name)

    # -- evaluation --------------------------------------------------------

    def resolved_variables(self) -> dict[str, float]:
        return resolve_variables(self.variables)

    def resolve_params(
        self, instance: Instance, variables: dict[str, float] | None = None
    ) -> Params:
        """Evaluate an instance's expressions and validate them against the component schema."""
        variables = self.resolved_variables() if variables is None else variables
        component = get_component(instance.component)
        fields = component.Params.model_fields
        values: dict[str, Any] = {}
        for key, raw in instance.params.items():
            is_text = key in fields and fields[key].annotation is str
            values[key] = raw if is_text or not isinstance(raw, str) else evaluate(raw, variables)
        return component.Params(**values)

    def render_instance(
        self, instance: Instance, variables: dict[str, float] | None = None
    ) -> Geometry:
        variables = self.resolved_variables() if variables is None else variables
        params = self.resolve_params(instance, variables)
        local = get_component(instance.component).build(params)
        transform = kdb.ICplxTrans(
            1.0,
            evaluate(instance.rotation, variables),
            instance.mirror_x,
            to_dbu(evaluate(instance.x, variables)),
            to_dbu(evaluate(instance.y, variables)),
        )
        placed = Geometry()
        placed.merge(local, transform)
        return placed

    def render(self) -> Geometry:
        """Drawn geometry of the whole design, merged per layer."""
        variables = self.resolved_variables()
        geometry = Geometry()
        for inst in self.instances:
            geometry.merge(self.render_instance(inst, variables))
        return geometry.merged()
