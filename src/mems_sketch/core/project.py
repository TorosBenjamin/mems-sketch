"""The project model: a process, local components, libraries and a top component.

Everything is a component. The design itself is the *top* component; what
used to be global variables are its parameters, so any project can be placed
inside another one. A project without a top component (``top=None``) is a
library: a set of components meant to be placed elsewhere. Components contain shape trees (see
:mod:`mems_sketch.core.shapes`) that may reference other components.

Components are *shared* (``plate``) or *private* to another component
(``comb/finger``: ``finger`` belongs to ``comb``), like nested classes: a
private component can be placed only inside its owner (in the owner itself or
in the owner's other private components). The components form one flat set;
their paths give the hierarchy. A library exports its shared components.

Component names are resolved like this, from the component holding the reference:

* ``name``: looked up from the inside out: the component's own private
  components, its owner's, and so on, then the shared ones, then the
  built-ins (shared names may not shadow built-ins)
* ``comb/finger``: a private component by its path (only from inside ``comb``)
* ``lib.name``: a shared component of the library loaded as ``lib``
* inside a library, names are looked up in that library; libraries never see
  the project's local components

The project is plain data. Turning it into geometry is the job of
:mod:`mems_sketch.core.compiler`; the ``render`` helpers here are shortcuts.
"""

from __future__ import annotations

import contextlib
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

    def _pool(self, library: str | None) -> dict[str, ComponentDef]:
        return self.libraries[library].components if library else self.components

    def qualify(self, name: str, context: str | None = None) -> str:
        """The unique name of the component that ``name`` refers to, written in
        the component ``context`` (a unique name; None: from outside any component,
        e.g. to open one, where every component can be named by its unique name).

        A bare name is looked up from the inside out: the context's own private
        components, then its owner's, up to the shared ones, then the built-ins.
        A private component can only be named from inside its owner.
        """
        # None: from outside (anything may be named); "": at project level
        library, where = (None, None) if context is None else _split(context)
        if "." in name:
            target, path = _split(name)
            if target not in self.libraries or path not in self._pool(target):
                raise KeyError(f"unknown component '{name}'")
            _check_visible(name, path, where if library == target or context is None else "")
            return name
        pool = self._pool(library)
        prefix = f"{library}." if library else ""
        if "/" in name:
            if name not in pool:
                raise KeyError(f"unknown component '{name}'")
            _check_visible(prefix + name, name, where)
            return prefix + name
        scope = where or ""
        while True:
            candidate = f"{scope}/{name}" if scope else name
            if candidate in pool:
                return prefix + candidate
            if not scope:
                break
            scope = scope.rpartition("/")[0]
        if is_builtin(name):
            return name
        raise KeyError(f"unknown component '{name}'")

    def reference_name(self, qualified: str, context: str | None) -> str:
        """How the component ``context`` writes a reference to ``qualified``: its
        short name when that finds it, else its path (or ``lib.path``)."""
        library, path = _split(qualified)
        context_library = _split(context)[0] if context else None
        if library is not None and library != context_library:
            return qualified
        short = path.rpartition("/")[2]
        with contextlib.suppress(KeyError):
            if self.qualify(short, context) == qualified:
                return short
        return path

    def definition(self, qualified: str) -> tuple[ComponentDef, str] | None:
        """The definition of a user component and the context its references are
        written in (its unique name), or None for a built-in."""
        library, path = _split(qualified)
        pool = self._pool(library)
        if library is not None and library not in self.libraries:
            raise KeyError(f"unknown component '{qualified}'")
        if path in pool:
            return pool[path], qualified
        return None

    def references_of(self, qualified: str) -> list[tuple[Shape, str]]:
        """The ``ref`` shapes of a user component with what each places (unique
        names); references that do not resolve are left out."""
        found = self.definition(qualified)
        if found is None:
            return []
        definition, context = found
        result = []
        for shape in walk(definition.shapes):
            if isinstance(shape, RefShape):
                with contextlib.suppress(KeyError):
                    result.append((shape, self.qualify(shape.component, context)))
        return result

    def users(self, qualified: str) -> list[str]:
        """The project's components that place ``qualified`` directly."""
        return [
            name
            for name in self.components
            if any(target == qualified for _, target in self.references_of(name))
        ]

    def private_components(self, owner: str) -> list[str]:
        """The components private to ``owner`` (a unique name), directly."""
        library, path = _split(owner)
        prefix = f"{library}." if library else ""
        return [prefix + name for name in self._pool(library) if name.rpartition("/")[0] == path]

    def component_names(self, context: str | None = None) -> list[str]:
        """Every component that can be placed in ``context``, written as it would
        write them: its own and its owners' private components, the shared ones,
        the libraries' shared ones and the built-ins."""
        from mems_sketch.core.component import component_types

        names = []
        for name in self.components:
            with contextlib.suppress(KeyError):
                self.qualify(name, context or "")
                names.append(self.reference_name(name, context))
        for library in self.libraries.values():
            for name in library.components:
                qualified = f"{library.name}.{name}"
                with contextlib.suppress(KeyError):
                    self.qualify(qualified, context or "")
                    names.append(self.reference_name(qualified, context))
        return [*dict.fromkeys(names), *component_types()]

    def define_component(self, definition: ComponentDef) -> ComponentDef:
        """Add or replace a local component; the project is unchanged if it is invalid."""
        if is_builtin(definition.short_name):
            raise ValueError(f"'{definition.short_name}' is a built-in component name")
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
        """Remove a local component with its private ones, if nothing else uses them
        (removing the top one leaves none)."""
        doomed = {k for k in self.components if k == name or k.startswith(f"{name}/")}
        users = sorted(
            {user for target in doomed for user in self.users(target) if user not in doomed}
        )
        if users:
            raise ValueError(f"component '{name}' is still used by: {', '.join(users)}")
        self.components = {k: v for k, v in self.components.items() if k not in doomed}
        if self.top in doomed:
            self.top = None

    def rename_point(self, component: str, old: str, new: str) -> None:
        """Rename a declared point of a local component, and update the components
        that place it: alignments to it (and the placing node's own ``point``),
        expressions using its coordinates and their points measured from it."""
        self.components[component].rename_point(old, new)
        for user in self.users(component):
            placing = [shape for shape, target in self.references_of(user) if target == component]
            self.components[user].follow_point_rename(
                {shape.name for shape in placing if shape.name}, old, new
            )
            for shape, target in self.references_of(user):  # the rewritten copies
                if target == component and shape.align and shape.align.point == old:
                    shape.align.point = new

    def rename_component(self, old: str, new: str) -> None:
        """Rename a local component (``new`` is its new short name) and update
        every reference to it."""
        owner = old.rpartition("/")[0]
        self.move_component(old, f"{owner}/{new}" if owner else new)

    def move_component(self, old: str, new: str) -> None:
        """Give a local component (with its private ones) a new path: rename it,
        make it private to another component, or shared (``new`` without owner).

        References to it are rewritten; a reference that could no longer see it
        (placed outside its new owner) is an error.
        """
        if new == old:
            return
        if new in self.components or is_builtin(new.rpartition("/")[2]):
            raise ValueError(f"a component named '{new}' already exists")
        if new.startswith(f"{old}/"):
            raise ValueError(f"'{old}' cannot be private to itself")
        owner = new.rpartition("/")[0]
        if owner and owner not in self.components:
            raise ValueError(f"unknown component '{owner}'")
        if self.top == old and owner:
            raise ValueError("the top component cannot be private to another one")
        ComponentDef.model_validate({"name": new})  # a valid path
        moved = {
            k: new + k[len(old) :] for k in self.components if k == old or k.startswith(f"{old}/")
        }
        references = [
            (name, shape, target)
            for name in self.components
            for shape, target in self.references_of(name)
        ]
        previous, top = self.components, self.top
        written = [(shape, shape.component) for _, shape, _ in references]
        try:
            self.components = {
                moved.get(k, k): (v.model_copy(update={"name": moved[k]}) if k in moved else v)
                for k, v in self.components.items()
            }
            for name, shape, target in references:
                context = moved.get(name, name)
                shape.component = self.reference_name(moved.get(target, target), context)
            self.top = moved.get(self.top, self.top)
            self.check_references()
        except Exception:
            self.components, self.top = previous, top
            for shape, component in written:
                shape.component = component
            raise

    def check_references(self) -> None:
        """Every ``ref`` must resolve (and see what it places), private components
        need their owner, and references must not form a cycle."""
        state: dict[str, str] = {}

        def visit(qualified: str, path: list[str]) -> None:
            if state.get(qualified) == "done":
                return
            if state.get(qualified) == "visiting":
                raise ValueError(f"circular component reference: {' -> '.join([*path, qualified])}")
            found = self.definition(qualified)
            if found is None:
                return  # built-in
            definition, context = found
            state[qualified] = "visiting"
            for ref in definition.references():
                try:
                    target = self.qualify(ref, context)
                except PrivateComponentError as exc:
                    raise ValueError(f"component '{qualified}': {exc}") from None
                except KeyError:
                    raise ValueError(
                        f"component '{qualified}' references unknown component '{ref}'"
                    ) from None
                visit(target, [*path, qualified])
            state[qualified] = "done"

        for library in (None, *self.libraries):
            prefix = f"{library}." if library else ""
            for name in self._pool(library):
                owner = name.rpartition("/")[0]
                if owner and owner not in self._pool(library):
                    raise ValueError(
                        f"component '{prefix}{name}' is private to a missing '{owner}'"
                    )
                visit(prefix + name, [])

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
        return session.render_shapes([shape], variables, name)

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


class PrivateComponentError(KeyError):
    """A component named from outside the component it is private to."""

    def __str__(self) -> str:
        return str(self.args[0])


def _split(qualified: str) -> tuple[str | None, str]:
    """``lib.comb/finger`` -> ``("lib", "comb/finger")``; no library: ``(None, path)``."""
    library, dot, path = qualified.partition(".")
    return (library, path) if dot else (None, qualified)


def _check_visible(qualified: str, path: str, where: str | None) -> None:
    """A private component is seen only from inside its owner (``where`` None: from
    outside every component, where anything may be named)."""
    owner = path.rpartition("/")[0]
    if not owner or where is None or where == owner or where.startswith(f"{owner}/"):
        return
    raise PrivateComponentError(
        f"'{qualified}' is private to '{owner}': it can only be placed inside '{owner}'"
    )


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
