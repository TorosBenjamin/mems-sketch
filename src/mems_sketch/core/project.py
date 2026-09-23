"""The project model: a process, local components, libraries and a top component.

Everything is a component. The design itself is the *top* component; what
used to be global variables are its parameters, so any project can be placed
inside another one. A project without a top component (``top=None``) is a
library: a set of components meant to be placed elsewhere. Components contain shape trees (see
:mod:`mems_sketch.core.shapes`) that may reference other components.

Component names are resolved like this:

* ``name``: a local component or a built-in (local names may not shadow built-ins)
* ``lib.name``: a component from the library loaded as ``lib``
* inside a library, a bare ``name`` refers to that library's own component
  first, then to a built-in; libraries never see the project's local components

The project is plain data. Turning it into geometry is the job of
:mod:`mems_sketch.core.compiler`; the ``render`` helpers here are shortcuts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mems_sketch.core.component import Component, Geometry, is_builtin
from mems_sketch.core.process import Layer, Process, Value, default_process
from mems_sketch.core.shapes import RefShape, Shape, child_lists, find, walk
from mems_sketch.core.user_component import ComponentDef, ParamDef

if TYPE_CHECKING:
    from mems_sketch.core.compiler import Compiler

__all__ = ["Instance", "Layer", "Library", "Process", "Project", "new_project"]

DEFAULT_TOP = "top"


def new_project(name: str = "untitled", library: bool = False) -> Project:
    """An empty design (with a ``top`` component) or library (one component, no top)."""
    if library:
        return Project(
            name=name,
            process=default_process(),
            top=None,
            components={"component1": ComponentDef(name="component1")},
        )
    return Project(name=name, process=default_process())


def Instance(
    name: str, component: str, params: dict[str, Any] | None = None, **placement
) -> RefShape:
    """Shorthand for a named component reference: ``Instance("pad", "anchor", {"size": 50}, x=10)``."""
    return RefShape(name=name, component=component, params=params or {}, **placement)


@dataclass
class Library:
    """A read-only set of components loaded from a folder and referenced as ``name.component``."""

    name: str
    components: dict[str, ComponentDef] = field(default_factory=dict)
    path: Path | None = None


@dataclass
class Project:
    name: str = "untitled"
    process: Process = field(default_factory=Process)
    components: dict[str, ComponentDef] = field(default_factory=dict)
    top: str | None = DEFAULT_TOP  # None: a library, with no design of its own
    libraries: dict[str, Library] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.top is not None and self.top not in self.components:
            self.components[self.top] = ComponentDef(name=self.top)

    # -- process -----------------------------------------------------------

    @property
    def layers(self) -> dict[str, Layer]:
        return self.process.layers

    @layers.setter
    def layers(self, layers: dict[str, Layer]) -> None:
        self.process.layers = layers

    def add_layer(self, layer: Layer) -> Layer:
        self.process.layers[layer.name] = layer
        return layer

    # -- components and name resolution ------------------------------------

    @property
    def top_component(self) -> ComponentDef:
        return self.components[self._target(None)]

    @property
    def is_library(self) -> bool:
        """A project without a top component: only components to be placed elsewhere."""
        return self.top is None

    def _target(self, component: str | None) -> str:
        """``component``, or the top component when none is named."""
        if component is not None:
            return component
        if self.top is None:
            raise ValueError("this project has no top component: name the component")
        return self.top

    def default_component(self) -> str | None:
        """The component to show first: the top one, else the first local one."""
        if self.top is not None:
            return self.top
        return next(iter(self.components), None)

    def qualify(self, name: str, namespace: str | None = None) -> str:
        """The unique name of the component that ``name`` refers to from ``namespace``.

        ``namespace`` is ``None`` for the project and a library name inside a library.
        """
        if "." in name:
            library, _, local = name.partition(".")
            if library not in self.libraries or local not in self.libraries[library].components:
                raise KeyError(f"unknown component '{name}'")
            return name
        if namespace is None and name in self.components:
            return name
        if namespace is not None and name in self.libraries[namespace].components:
            return f"{namespace}.{name}"
        if is_builtin(name):
            return name
        raise KeyError(f"unknown component '{name}'")

    def definition(self, qualified: str) -> tuple[ComponentDef, str | None] | None:
        """The definition and namespace of a user component, or None for a built-in."""
        if "." in qualified:
            library, _, local = qualified.partition(".")
            return self.libraries[library].components[local], library
        if qualified in self.components:
            return self.components[qualified], None
        return None

    def component_names(self) -> list[str]:
        """Every component that can be placed from the project: local, library, built-in."""
        from mems_sketch.core.component import component_types

        library_names = [
            f"{lib.name}.{c}" for lib in self.libraries.values() for c in lib.components
        ]
        return [*self.components, *library_names, *component_types()]

    def define_component(self, definition: ComponentDef) -> ComponentDef:
        """Add or replace a local component; the project is unchanged if it is invalid."""
        if is_builtin(definition.name):
            raise ValueError(f"'{definition.name}' is a built-in component name")
        previous = self.components
        self.components = {**previous, definition.name: definition}
        try:
            self.check_references()
            self.render(definition.name)  # its defaults must produce valid geometry
        except Exception:
            self.components = previous
            raise
        return definition

    def remove_component(self, name: str) -> None:
        """Remove a local component that nothing uses (removing the top one leaves none)."""
        users = [d.name for d in self.components.values() if name in d.references()]
        if users:
            raise ValueError(f"component '{name}' is still used by: {', '.join(users)}")
        del self.components[name]
        if self.top == name:
            self.top = None

    def rename_component(self, old: str, new: str) -> None:
        """Rename a local component and update every reference to it."""
        if new in self.components or is_builtin(new):
            raise ValueError(f"a component named '{new}' already exists")
        definition = self.components[old].model_copy(update={"name": new})
        definition = ComponentDef.model_validate(definition.model_dump())  # validates the name
        self.components = {
            (new if k == old else k): (definition if k == old else v)
            for k, v in self.components.items()
        }
        for component in self.components.values():
            for shape in walk(component.shapes):
                if isinstance(shape, RefShape) and shape.component == old:
                    shape.component = new
        if self.top == old:
            self.top = new

    def check_references(self) -> None:
        """Every ``ref`` must resolve, and references must not form a cycle."""
        state: dict[str, str] = {}

        def visit(qualified: str, path: list[str]) -> None:
            if state.get(qualified) == "done":
                return
            if state.get(qualified) == "visiting":
                raise ValueError(f"circular component reference: {' -> '.join([*path, qualified])}")
            found = self.definition(qualified)
            if found is None:
                return  # built-in
            definition, namespace = found
            state[qualified] = "visiting"
            for ref in definition.references():
                try:
                    target = self.qualify(ref, namespace)
                except KeyError:
                    raise ValueError(
                        f"component '{qualified}' references unknown component '{ref}'"
                    ) from None
                visit(target, [*path, qualified])
            state[qualified] = "done"

        for name in self.components:
            visit(name, [])
        for library in self.libraries.values():
            for name in library.components:
                visit(f"{library.name}.{name}", [])

    # -- parameters (the top component's parameters act as the design's variables)
    # Methods taking ``component=None`` use the top component; a library must name one.

    def parameters(self, component: str | None = None) -> list[ParamDef]:
        return self.components[self._target(component)].parameters

    @property
    def variables(self) -> dict[str, Value]:
        """The top component's parameter defaults (read-only view)."""
        return {p.name: p.default for p in self.top_component.parameters}

    def set_parameter(
        self, name: str, default: Value, component: str | None = None, **limits: Any
    ) -> ParamDef:
        """Create or update a parameter's default (and optionally min/max/integer/description)."""
        definition = self.components[self._target(component)]
        for index, existing in enumerate(definition.parameters):
            if existing.name == name:
                updated = ParamDef.model_validate(
                    {**existing.model_dump(), "default": default, **limits}
                )
                definition.parameters[index] = updated
                return updated
        created = ParamDef(name=name, default=default, **limits)
        definition.parameters.append(created)
        return created

    def set_variable(self, name: str, value: Value) -> None:
        """Set a top-level parameter; the scripting equivalent of a global variable."""
        self.set_parameter(name, value)

    def remove_parameter(self, name: str, component: str | None = None) -> None:
        definition = self.components[self._target(component)]
        definition.parameters = [p for p in definition.parameters if p.name != name]

    def resolved_parameters(
        self, component: str | None = None, params: dict[str, Any] | None = None
    ) -> dict[str, float]:
        """Parameter values of a local component (defaults unless given) plus ``process.*``."""
        from mems_sketch.core.compiler import Compiler

        return Compiler().session(self).variables(self._target(component), params)

    resolved_variables = resolved_parameters

    # -- shape editing (on the top component unless another is named) ------

    def shapes_of(self, component: str | None = None) -> list[Shape]:
        return self.components[self._target(component)].shapes

    @property
    def shapes(self) -> list[Shape]:
        return self.top_component.shapes

    def add(self, shape: Shape, component: str | None = None) -> Shape:
        """Append a shape to a component (default: top); fails early on bad input."""
        shapes = self.shapes_of(component)
        check_shape_names([*shapes, shape])
        shapes.append(shape)
        try:
            self.render(component)  # in context: it may align to its siblings
        except Exception:
            shapes.pop()
            raise
        return shape

    def find(self, name: str, component: str | None = None) -> Shape:
        return find(self.shapes_of(component), name)

    def replace(self, name: str, new: Shape, component: str | None = None) -> Shape:
        """Swap the named shape (at any depth) for ``new``; unchanged on error.

        ``new`` keeps ``name`` unless it has a name of its own.
        """
        if new.name is None:
            new = new.model_copy(update={"name": name})
        container, index = _locate(self.shapes_of(component), name)
        old = container[index]
        container[index] = new
        try:
            check_shape_names(self.shapes_of(component))
            self.render(component)
        except Exception:
            container[index] = old
            raise
        return new

    def remove(self, name: str, component: str | None = None) -> Shape:
        container, index = _locate(self.shapes_of(component), name)
        return container.pop(index)

    # -- rendering shortcuts -----------------------------------------------

    def render(
        self,
        component: str | None = None,
        params: dict[str, Any] | None = None,
        compiler: Compiler | None = None,
    ) -> Geometry:
        """Merged geometry of a component (default: top) with the given or default parameters."""
        from mems_sketch.core.compiler import Compiler

        return (compiler or Compiler()).session(self).render(self._target(component), params)

    def render_shape(
        self,
        shape: Shape,
        component: str | None = None,
        variables: dict[str, float] | None = None,
        compiler: Compiler | None = None,
    ) -> Geometry:
        """Geometry of one shape evaluated in a component's scope (default: top)."""
        from mems_sketch.core.compiler import Compiler

        session = (compiler or Compiler()).session(self)
        name = self._target(component)
        variables = session.variables(name) if variables is None else variables
        return session.render_shapes([shape], variables)

    def component(self, name: str) -> Component:
        """A buildable component, resolved from the project namespace."""
        from mems_sketch.core.compiler import Compiler

        return Compiler().session(self).component(name)

    def validate(self) -> list[str]:
        """Problems that stop components from building with their defaults (empty if none)."""
        problems = []
        try:
            self.check_references()
        except ValueError as exc:
            return [str(exc)]
        from mems_sketch.core.compiler import Compiler

        session = Compiler().session(self)
        for name in self.components:
            try:
                session.render(name)
            except Exception as exc:  # noqa: BLE001 - collected for the caller
                problems.append(f"{name}: {exc}")
        return problems


def check_shape_names(shapes: list[Shape]) -> None:
    """Named shapes must be unique within one component."""
    seen: set[str] = set()
    for shape in walk(shapes):
        if shape.name is None:
            continue
        if shape.name in seen:
            raise ValueError(f"a shape named '{shape.name}' already exists")
        seen.add(shape.name)


def _locate(shapes: list[Shape], name: str) -> tuple[list[Shape], int]:
    def search(items: list[Shape]):
        for index, shape in enumerate(items):
            if shape.name == name:
                return items, index
            for children in child_lists(shape):
                if found := search(children):
                    return found
        return None

    if found := search(shapes):
        return found
    raise KeyError(f"no shape named '{name}'")
