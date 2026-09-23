"""Components: creating, renaming, deleting, copying, making and unpacking them,
the top component, libraries, and which components place which."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mems_sketch.core.component import is_builtin
from mems_sketch.core.expressions import ExpressionError, names_in
from mems_sketch.core.project import Project
from mems_sketch.core.shapes import (
    NodePath,
    RefShape,
    Shape,
    TransformShape,
    Value,
    container_of,
    map_expressions,
    rename_node_references,
    rewrite,
    walk,
)
from mems_sketch.core.user_component import ComponentDef
from mems_sketch.editing.commands import Commands
from mems_sketch.editing.naming import fresh_name
from mems_sketch.storage.project_files import load_library


class ComponentEdits(Commands):
    """Components of the project, its top component and its libraries."""

    # -- what places what --------------------------------------------------

    def placed(self, component: str) -> list[tuple[str, int]]:
        """The components that ``component`` places, as ``(name, count)`` in order of first use.

        Names are written as the project would write them (``lib.name`` for a
        library component), so they can be opened, placed or explored further.
        Built-ins place nothing. Repeated references count every copy.
        """
        project = self.session.project
        found = project.definition(project.qualify(component))
        if found is None:
            return []
        definition, namespace = found
        counts: dict[str, int] = {}
        for shape in walk(definition.shapes):
            if isinstance(shape, RefShape):
                try:
                    target = project.qualify(shape.component, namespace)
                except KeyError:
                    continue  # the messages panel reports it
                copies = 1
                if shape.repeat is not None:
                    try:
                        copies = int(float(shape.repeat.columns) * float(shape.repeat.rows))
                    except (TypeError, ValueError):  # expressions: counted once
                        copies = 1
                counts[target] = counts.get(target, 0) + copies
        return list(counts.items())

    def users(self, component: str) -> list[str]:
        """The local components that place ``component`` directly."""
        return [
            name
            for name in self.session.project.components
            if any(target == component for target, _ in self.placed(name))
        ]

    def new(self, name: str) -> str:
        self._check_name(name)
        self.session.edit(
            f"New component {name}",
            lambda p: p.components.__setitem__(name, ComponentDef(name=name)),
        )
        self.session.set_active(name)
        return name

    def delete(self, name: str) -> None:
        if name not in self.session.project.components:
            raise ValueError(f"'{name}' is not a component of this project")
        if len(self.session.project.components) == 1:
            raise ValueError("a project needs at least one component")
        self.session.edit(f"Delete component {name}", lambda p: p.remove_component(name))

    def copy(self, component: str, name: str | None = None) -> str:
        """Copy a library (or local) component into the project, to edit it there.

        The copy keeps using what the original used: references inside it are
        written as seen from the project (``lib.plate``). Built-ins have no
        shapes to copy; place them instead.
        """
        project = self.session.project
        found = project.definition(project.qualify(component))
        if found is None:
            raise ValueError(f"'{component}' is built in: it has no shapes to copy; place it")
        definition, namespace = found
        taken = set(project.components)
        stem = component.rpartition(".")[2]
        name = name or (stem if stem not in taken and not is_builtin(stem) else None)
        name = name or fresh_name(f"{stem}_copy", taken)
        self._check_name(name)
        copied = definition.model_copy(deep=True, update={"name": name})
        if namespace is not None:
            for shape in walk(copied.shapes):
                if isinstance(shape, RefShape):
                    shape.component = project.qualify(shape.component, namespace)
        self.session.edit(
            f"Copy {component} into the project",
            lambda p: p.components.__setitem__(name, copied),
        )
        self.session.set_active(name)
        return name

    # -- libraries ---------------------------------------------------------

    def add_library(self, folder: str | Path, name: str | None = None) -> str:
        """Load the components in ``folder`` (a project or library folder) as ``name.*``."""
        folder = Path(folder)
        name = name or re.sub(r"\W+", "_", folder.name).strip("_").lower() or "lib"
        if not name.isidentifier():
            name = f"lib_{name}"
        if name in self.session.project.libraries:
            raise ValueError(f"a library named '{name}' is already loaded")
        library = load_library(name, folder)
        if not library.components:
            raise ValueError(f"{folder} has no components")
        self.session.edit(f"Add library {name}", lambda p: p.libraries.__setitem__(name, library))
        return name

    def remove_library(self, name: str) -> None:
        project = self.session.project
        if name not in project.libraries:
            raise ValueError(f"no library named '{name}'")
        users = [
            component
            for component, definition in project.components.items()
            if any(ref.startswith(f"{name}.") for ref in definition.references())
        ]
        if users:
            raise ValueError(f"library '{name}' is still used by: {', '.join(users)}")
        self.session.edit(f"Remove library {name}", lambda p: p.libraries.pop(name))

    def rename(self, old: str, new: str) -> None:
        self._check_name(new)

        def change(project: Project) -> None:
            project.rename_component(old, new)
            if self.session.active == old:
                self.session.active = new

        self.session.edit(
            f"Rename component {old}", change, lambda: self.session.component_renamed.emit(old, new)
        )

    def set_top(self, name: str | None) -> None:
        """Make a local component the top one; ``None`` makes the project a library."""

        def change(project: Project) -> None:
            if name is not None and name not in project.components:
                raise ValueError(f"'{name}' is not a local component")
            project.top = name

        description = f"Make {name} the top component" if name else "Make the project a library"
        self.session.edit(description, change)

    def _check_name(self, name: str) -> None:
        if not name.isidentifier():
            raise ValueError(f"'{name}' is not a valid component name")
        if name in self.session.project.components or is_builtin(name):
            raise ValueError(f"a component named '{name}' already exists")

    def make(self, paths: list[NodePath], name: str) -> NodePath:
        """Move sibling nodes into a new component and put a reference in their place.

        A single transform becomes a component of its children, placed where
        the transform was (with its alignment, repeat and name). Parameters of the active component that the nodes use become parameters
        of the new component (with the same defaults and limits) and are passed
        through by the reference, so the geometry is unchanged.
        """
        self._check_name(name)
        ordered, first, nodes = self.session.nodes.siblings(paths)
        placement: dict[str, Any] = {"name": self.session.unique_name(name)}
        if len(nodes) == 1 and isinstance(nodes[0], TransformShape):
            # A transform becomes a component placed where the transform was.
            transform = nodes[0]
            if transform.scale != 1:
                raise ValueError("a scaled transform cannot become a component; set scale to 1")
            nodes = transform.children
            placement = {
                "name": transform.name or placement["name"],
                "x": transform.x,
                "y": transform.y,
                "rotation": transform.rotation,
                "mirror_x": transform.mirror_x,
                "align": transform.align,
                "enabled": transform.enabled,
                "repeat": transform.repeat,
            }
        definition_params = {p.name: p for p in self.session.active_definition.parameters}
        used = _names_used(nodes) & definition_params.keys()
        pending = list(used)
        while pending:  # defaults may depend on further parameters
            default = definition_params[pending.pop()].default
            if isinstance(default, str):
                for dep in names_in(default) & definition_params.keys():
                    if dep not in used:
                        used.add(dep)
                        pending.append(dep)
        parameters = [
            p.model_copy() for p in self.session.active_definition.parameters if p.name in used
        ]
        definition = ComponentDef(
            name=name, parameters=parameters, shapes=[n.model_copy(deep=True) for n in nodes]
        )
        reference = RefShape(component=name, params={p: p for p in sorted(used)}, **placement)
        indices = {p[-1][1] for p in ordered}

        def change(project: Project) -> None:
            shapes = self.session.local(project).shapes
            project.components[name] = definition
            target, _ = container_of(shapes, ordered[0])
            kept = [s for i, s in enumerate(target) if i not in indices]
            target[:] = kept[:first] + [reference] + kept[first:]

        self.session.edit(f"Make component {name}", change)
        return (*ordered[0][:-1], (ordered[0][-1][0], first))

    def unpack(self, path: NodePath) -> NodePath:
        """Replace a component reference by a transform holding a copy of its shapes.

        The component's parameters are replaced by the values the reference
        passed (or the defaults), so the geometry is unchanged. Shapes whose
        names are taken are renamed. The component's own declared points are
        not kept: a transform only has bounding-box points.
        """
        node = self.session.node(path)
        if not isinstance(node, RefShape):
            raise ValueError("select a component reference to unpack")  # noqa: TRY004
        found = self.session.project.definition(self.session.project.qualify(node.component))
        if found is None:
            raise ValueError(f"'{node.component}' is a built-in component: it has no shapes")
        definition, namespace = found
        values = _parameter_values(definition, node.params)
        shapes = map_expressions(
            definition.shapes, lambda n: _as_expression(values[n]) if n in values else None
        )
        for shape in walk(shapes):
            if isinstance(shape, RefShape) and namespace is not None:
                shape.component = self.session.project.qualify(shape.component, namespace)
        taken = {s.name for s in walk(self.session.shapes) if s.name}
        for old in [s.name for s in walk(shapes) if s.name in taken]:
            new = fresh_name(old, taken | {s.name for s in walk(shapes) if s.name})
            shapes = rename_node_references(shapes, old, new)
            next(s for s in walk(shapes) if s.name == old).name = new
            taken.add(new)
        unpacked = TransformShape(
            name=node.name,
            children=shapes,
            x=node.x,
            y=node.y,
            rotation=node.rotation,
            mirror_x=node.mirror_x,
            align=node.align,
            enabled=node.enabled,
            repeat=node.repeat,
        )

        def change(project: Project) -> None:
            container, index = container_of(self.session.shapes_in(project), path)
            container[index] = unpacked

        self.session.edit(f"Unpack {node.name or node.component}", change)
        return path


_NOT_EXPRESSIONS = {"kind", "name", "layer", "component", "op", "ends", "corners", "mapping"}


def _parameter_values(definition: ComponentDef, given: dict[str, Value]) -> dict[str, Value]:
    """Each parameter as a value in the caller's scope: the given one, else the default.

    Defaults may use other parameters; those are replaced by their values too.
    """
    names = {p.name for p in definition.parameters}
    values: dict[str, Value] = {}

    def value(name: str) -> Value:
        if name not in values:
            if name in given:
                values[name] = given[name]
            else:
                default = definition.parameter(name).default
                values[name] = rewrite(
                    default, lambda n: _as_expression(value(n)) if n in names else None
                )
        return values[name]

    for name in names:
        value(name)
    return values


def _as_expression(value: Value) -> str:
    return repr(float(value)) if isinstance(value, int | float) else str(value)


def _names_used(nodes: list[Shape]) -> set[str]:
    """Every name referenced by the expressions in ``nodes`` (and their subtrees)."""
    found: set[str] = set()

    def visit(value: Any, key: str | None = None) -> None:
        if key in _NOT_EXPRESSIONS:
            return
        if isinstance(value, dict):
            for k, v in value.items():
                visit(v, k if key != "params" else None)
        elif isinstance(value, list | tuple):
            for v in value:
                visit(v)
        elif isinstance(value, str):
            try:
                found.update(names_in(value))
            except ExpressionError:
                pass

    for node in nodes:
        visit(node.model_dump())
    return found
