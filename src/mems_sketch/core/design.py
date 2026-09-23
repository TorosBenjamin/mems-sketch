"""The design model: process layers, global variables, user-defined components
and the top-level shape tree.

The top level is the same kind of shape tree as a component body (see
:mod:`mems_sketch.core.shapes`), evaluated with the global variables, so
booleans and other operations work between placed components as well as inside
them. Geometry is always regenerated from this model; it is never stored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mems_sketch.core.component import Component, Geometry, get_component, is_builtin
from mems_sketch.core.expressions import resolve_variables
from mems_sketch.core.shapes import (
    Evaluator,
    RefShape,
    Shape,
    child_lists,
    find,
    references,
    walk,
)
from mems_sketch.core.user_component import ComponentDef, UserComponent

Value = float | str  # a number or an expression


def Instance(
    name: str, component: str, params: dict[str, Any] | None = None, **placement
) -> RefShape:  # noqa: N802
    """Shorthand for a named component reference, e.g. ``Instance("pad", "anchor", {"size": 50}, x=10)``."""
    return RefShape(name=name, component=component, params=params or {}, **placement)


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
class Design:
    name: str = "untitled"
    layers: dict[str, Layer] = field(default_factory=dict)
    variables: dict[str, Value] = field(default_factory=dict)
    components: dict[str, ComponentDef] = field(default_factory=dict)
    shapes: list[Shape] = field(default_factory=list)

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
        users = ["the design"] if name in references(self.shapes) else []
        users += [d.name for d in self.components.values() if name in d.references()]
        if users:
            raise ValueError(f"component '{name}' is still used by: {', '.join(users)}")
        del self.components[name]

    def component(self, name: str) -> Component:
        """Look up a user-defined component first, then the built-in library."""
        if name in self.components:
            return UserComponent(self.components[name], self.component)
        return get_component(name)

    def add(self, shape: Shape) -> Shape:
        """Append a top-level shape (typically an :func:`Instance` or an operation)."""
        self._check_names([*self.shapes, shape])
        self.render_shape(shape)  # fail early on bad parameters or references
        self.shapes.append(shape)
        return shape

    def find(self, name: str) -> Shape:
        return find(self.shapes, name)

    def replace(self, name: str, new: Shape) -> Shape:
        """Swap the named shape (at any depth) for ``new``; the design is unchanged on error.

        ``new`` keeps ``name`` unless it has a name of its own.
        """
        if new.name is None:
            new = new.model_copy(update={"name": name})
        container, index = self._locate(name)
        old = container[index]
        container[index] = new
        try:
            self._check_names(self.shapes)
            self.render()
        except Exception:
            container[index] = old
            raise
        return new

    def remove(self, name: str) -> Shape:
        container, index = self._locate(name)
        return container.pop(index)

    def _locate(self, name: str) -> tuple[list[Shape], int]:
        def search(shapes: list[Shape]):
            for index, shape in enumerate(shapes):
                if shape.name == name:
                    return shapes, index
                for children in child_lists(shape):
                    if found := search(children):
                        return found
            return None

        if found := search(self.shapes):
            return found
        raise KeyError(f"no shape named '{name}'")

    @staticmethod
    def _check_names(shapes: list[Shape]) -> None:
        seen: set[str] = set()
        for shape in walk(shapes):
            if shape.name is None:
                continue
            if shape.name in seen:
                raise ValueError(f"a shape named '{shape.name}' already exists")
            seen.add(shape.name)

    # -- evaluation --------------------------------------------------------

    def resolved_variables(self) -> dict[str, float]:
        return resolve_variables(self.variables)

    def render_shape(self, shape: Shape, variables: dict[str, float] | None = None) -> Geometry:
        """Geometry of one top-level shape, e.g. to highlight a selection in the GUI."""
        variables = self.resolved_variables() if variables is None else variables
        return Evaluator(self.component).render([shape], variables)

    def render(self) -> Geometry:
        """Drawn geometry of the whole design, merged per layer."""
        return Evaluator(self.component).render(self.shapes, self.resolved_variables()).merged()


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
