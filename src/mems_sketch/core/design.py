"""The design model: process layers, global variables, user-defined components
and component instances.

Parameter values and positions may be numbers or expressions over the global
variables, e.g. ``{"gap": "min_gap * 1.5"}``. Geometry is always regenerated
from this model; it is never stored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mems_sketch.core.component import (
    Component,
    Geometry,
    Params,
    get_component,
    is_builtin,
    placement,
    resolve_params,
)
from mems_sketch.core.expressions import evaluate, resolve_variables
from mems_sketch.core.user_component import ComponentDef, UserComponent

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
    components: dict[str, ComponentDef] = field(default_factory=dict)
    instances: list[Instance] = field(default_factory=list)

    # -- editing -----------------------------------------------------------

    def add_layer(self, layer: Layer) -> Layer:
        self.layers[layer.name] = layer
        return layer

    def set_variable(self, name: str, value: Value) -> None:
        if not name.isidentifier():
            raise ValueError(f"'{name}' is not a valid variable name")
        self.variables[name] = value

    def define_component(self, definition: ComponentDef) -> ComponentDef:
        """Add or replace a user-defined component."""
        if is_builtin(definition.name):
            raise ValueError(f"'{definition.name}' is a built-in component name")
        previous = self.components
        self.components = {**previous, definition.name: definition}
        try:
            _check_references(self.components)
            trial = self.component(definition.name)
            trial.build(trial.Params())  # the defaults must produce valid geometry
        except Exception:
            self.components = previous
            raise
        return definition

    def remove_component(self, name: str) -> None:
        users = [i.name for i in self.instances if i.component == name]
        users += [d.name for d in self.components.values() if name in d.references()]
        if users:
            raise ValueError(f"component '{name}' is still used by: {', '.join(users)}")
        del self.components[name]

    def component(self, name: str) -> Component:
        """Look up a user-defined component first, then the built-in library."""
        if name in self.components:
            return UserComponent(self.components[name], self.component)
        return get_component(name)

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
        return resolve_params(self.component(instance.component), instance.params, variables)

    def render_instance(
        self, instance: Instance, variables: dict[str, float] | None = None
    ) -> Geometry:
        variables = self.resolved_variables() if variables is None else variables
        params = self.resolve_params(instance, variables)
        local = self.component(instance.component).build(params)
        transform = placement(
            evaluate(instance.x, variables),
            evaluate(instance.y, variables),
            evaluate(instance.rotation, variables),
            instance.mirror_x,
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


def _check_references(components: dict[str, ComponentDef]) -> None:
    """Every ``ref`` must name a known component, and references must not form a cycle."""
    state: dict[str, str] = {}  # name -> "visiting" | "done"

    def visit(name: str, path: list[str]) -> None:
        if state.get(name) == "done":
            return
        if state.get(name) == "visiting":
            raise ValueError(f"circular component reference: {' -> '.join([*path, name])}")
        state[name] = "visiting"
        for ref in components[name].references():
            if ref in components:
                visit(ref, [*path, name])
            elif not is_builtin(ref):
                raise ValueError(f"component '{name}' references unknown component '{ref}'")
        state[name] = "done"

    for name in components:
        visit(name, [])
