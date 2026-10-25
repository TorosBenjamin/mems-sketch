"""Components: creating, renaming, deleting, copying, making and unpacking them,
the top component, libraries, and which components place which."""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any

from mems_sketch.core.expressions import ExpressionError, names_in
from mems_sketch.core.levels import LEVEL, Stack, is_relative, offset, shifted
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
from mems_sketch.core.shapes.modifiers import total_copies
from mems_sketch.core.shapes.points import BBOX_POINTS
from mems_sketch.core.user_component import ComponentDef, ParamDef
from mems_sketch.editing.commands import Commands
from mems_sketch.editing.naming import fresh_name
from mems_sketch.storage.project_files import load_library

LIBRARIES_DIR = "libraries"  # where a library copied into a project goes


class ComponentEdits(Commands):
    """Components of the project, its top component and its libraries."""

    # -- what places what --------------------------------------------------

    def placed(self, component: str) -> list[tuple[str, int]]:
        """The components that ``component`` places, as ``(name, count)`` in order of first use.

        Names are written as the project would write them (``lib.name`` for a
        library component), so they can be opened, placed or explored further.
        References with array or mirror modifiers count every copy.
        """
        project = self.session.project
        counts: dict[str, int] = {}
        # unresolved references are left out: the messages panel reports them
        for shape, target in project.references_of(project.qualify(component)):
            # copies whose count is an expression are counted once
            counts[target] = counts.get(target, 0) + total_copies(shape.modifiers)
        return list(counts.items())

    def users(self, component: str) -> list[str]:
        """The local components that place ``component`` directly."""
        return self.session.project.users(component)

    def new(self, name: str, owner: str | None = None) -> str:
        """A new empty component: shared, or private to ``owner``."""
        name = self._check_name(name, owner)
        self.session.edit(
            f"New component {name}",
            lambda p: p.components.__setitem__(name, ComponentDef(name=name)),
        )
        self.session.set_active(name)
        return name

    def delete(self, name: str) -> None:
        """Delete a component and its private components (none may be in use elsewhere)."""
        project = self.session.project
        if name not in project.components:
            raise ValueError(f"'{name}' is not a component of this project")
        if all(k == name or k.startswith(f"{name}/") for k in project.components):
            raise ValueError("a project needs at least one component")
        self.session.edit(f"Delete component {name}", lambda p: p.remove_component(name))

    def copy(self, component: str, name: str | None = None) -> str:
        """Copy a library (or local) component into the project, to edit
        it there.

        The copy keeps using what the original used: references inside it are
        written as seen from the project (``lib.plate``). Imported cells have no
        shapes to copy; place them instead.
        """
        project = self.session.project
        qualified = project.qualify(component)
        if project.definition(qualified) is None:
            raise ValueError(f"'{component}' is imported: it has no shapes to copy; place it")
        library, _, path = qualified.rpartition(".")
        # A local copy stays with its owner; a library's becomes shared.
        owner = path.rpartition("/")[0] if not library else ""
        stem = path.rpartition("/")[2]
        taken = {k.rpartition("/")[2] for k in project.components if k.rpartition("/")[0] == owner}
        name = name or (stem if stem not in taken else None)
        name = name or fresh_name(f"{stem}_copy", taken)
        root = self._check_name(name, owner or None)
        prefix = f"{library}." if library else ""
        pool = project.libraries[library].components if library else project.components
        # The component and its private components, each with where it goes.
        subtree = {
            prefix + key: root + key[len(path) :]
            for key in pool
            if key == path or key.startswith(f"{path}/")
        }

        def change(p: Project) -> None:
            copies = {
                key: p.definition(source)[0].model_copy(deep=True, update={"name": key})
                for source, key in subtree.items()
            }
            p.components.update(copies)  # first: references may name each other
            for source, key in subtree.items():
                for shape in walk(copies[key].shapes):
                    if isinstance(shape, RefShape):  # written as seen from the copy
                        target = p.qualify(shape.component, source)
                        shape.component = p.reference_name(subtree.get(target, target), key)

        self.session.edit(f"Copy {component} into the project", change)
        self.session.set_active(root)
        return root

    # -- libraries ---------------------------------------------------------

    def add_library(self, folder: str | Path, name: str | None = None) -> str:
        """Load the components in ``folder`` (a project or library folder) as ``name.*``."""
        folder = Path(folder)
        name = name or library_name(folder)
        if name in self.session.project.libraries:
            raise ValueError(f"a library named '{name}' is already loaded")
        library = load_library(name, folder)
        if not library.components and not library.processes:
            raise ValueError(f"{folder} has no components or processes")
        self.session.edit(f"Add library {name}", lambda p: p.libraries.__setitem__(name, library))
        return name

    def outside_repository(self) -> list[tuple[str, Path]]:
        """The libraries kept in folders outside the project's git repository, with
        their folders: a clone of the repository will not have them (#310)."""
        from mems_sketch.storage import git

        if self.session.path is None:
            return []
        root = git.repository(self.session.path)
        if root is None:
            return []
        root = root.resolve()
        found = []
        for name, library in self.session.project.libraries.items():
            if library.bundled or library.path is None:
                continue
            folder = Path(library.path).resolve()
            if root != folder and root not in folder.parents:
                found.append((name, folder))
        return found

    def copy_library_in(self, name: str) -> Path:
        """Copy the folder of the library ``name`` into the project (``libraries/name``)
        and use the copy: it is then in the project's repository (#310). Saving
        writes where it is now; undo goes back to the folder outside."""
        import shutil

        project = self.session.project
        if self.session.path is None:
            raise ValueError("save the project first: the library is copied into its folder")
        library = project.libraries.get(name)
        if library is None or library.bundled or library.path is None:
            raise ValueError(f"'{name}' is not a library kept in a folder")
        target = Path(self.session.path) / LIBRARIES_DIR / name
        if target.exists():
            raise ValueError(f"{target} already exists: move it away or use it as the library")
        shutil.copytree(library.path, target, ignore=shutil.ignore_patterns(".git", ".mems-sketch"))
        copied = load_library(name, target)
        self.session.edit(
            f"Copy library {name} into the project",
            lambda p: p.libraries.__setitem__(name, copied),
        )
        return target

    def remove_library(self, name: str) -> None:
        project = self.session.project
        if name not in project.libraries:
            raise ValueError(f"no library named '{name}'")
        if project.libraries[name].bundled:
            raise ValueError(f"'{name}' is a bundled library: every project has it")
        users = [
            component
            for component, definition in project.components.items()
            if any(ref.startswith(f"{name}.") for ref in definition.references())
        ]
        if users:
            raise ValueError(f"library '{name}' is still used by: {', '.join(users)}")
        self.session.edit(f"Remove library {name}", lambda p: p.libraries.pop(name))

    def rename(self, old: str, new: str) -> None:
        """Give a component a new (short) name; its private components follow."""
        owner = old.rpartition("/")[0] or None
        self._move(old, self._check_name(new, owner, old), f"Rename component {old}")

    def move(self, name: str, owner: str | None) -> str:
        """Make a component private to ``owner``, or shared (``None``); its own
        private components come along. Refused if a component that places it could
        no longer see it."""
        short = name.rpartition("/")[2]
        where = f"private to {owner}" if owner else "shared"
        new = self._check_name(short, owner, name)
        self._move(name, new, f"Make {short} {where}")
        return new

    def _move(self, old: str, new: str, description: str) -> None:
        moved = {
            k: new + k[len(old) :]
            for k in self.session.project.components
            if k == old or k.startswith(f"{old}/")
        }

        def change(project: Project) -> None:
            project.move_component(old, new)
            self.session.active = moved.get(self.session.active, self.session.active)

        def done() -> None:
            for before, after in moved.items():
                self.session.component_renamed.emit(before, after)

        self.session.edit(description, change, done, renames=moved)

    def set_top(self, name: str | None) -> None:
        """Make a local component the top one; ``None`` makes the project a library."""

        def change(project: Project) -> None:
            if name is not None and name not in project.components:
                raise ValueError(f"'{name}' is not a local component")
            if name is not None and "/" in name:
                raise ValueError(f"'{name}' is private: make it shared to use it as the top")
            project.top = name

        description = f"Make {name} the top component" if name else "Make the project a library"
        self.session.edit(description, change)

    def set_level(self, name: str, level: str | None) -> None:
        """The level of the layer stack a local component is on unless placed
        elsewhere; ``None``: the level of whatever places it."""

        def change(project: Project) -> None:
            if level is not None and level not in {lv.layer for lv in project.process.levels}:
                raise ValueError(f"'{level}' is not a level of the layer stack")
            project.components[name].level = level

        self.session.edit(f"Put {name} on {level or 'the level it is placed on'}", change)

    def set_description(self, name: str, text: str) -> None:
        """What a project component is, in a sentence or two: shown where it is
        placed from (the Place popup, Search Everywhere) and by its parameters.
        Paragraphs (split by blank lines) are kept; other runs of space are one."""
        paragraphs = (" ".join(p.split()) for p in re.split(r"\n\s*\n", text))
        text = "\n\n".join(p for p in paragraphs if p)

        def change(project: Project) -> None:
            project.components[name].description = text

        self.session.edit(f"Describe {name}", change)

    def _check_name(self, name: str, owner: str | None = None, moving: str | None = None) -> str:
        """The path of a new component ``name`` (private to ``owner``), if it is free
        (``moving``: the component that will have it)."""
        if not name.isidentifier():
            raise ValueError(f"'{name}' is not a valid component name")
        if owner is not None and owner not in self.session.project.components:
            raise ValueError(f"'{owner}' is not a component of this project")
        path = f"{owner}/{name}" if owner else name
        project = self.session.project
        if path in project.components or name in project.imports:
            where = f" in {owner}" if owner else ""
            raise ValueError(f"a component named '{name}' already exists{where}")
        if owner is not None:  # inner names hide outer ones, so not one placed in there
            inside = [k for k in project.components if k == owner or k.startswith(f"{owner}/")]
            if any(name in project.components[k].references() for k in inside) and (
                _resolved(project, name, owner) != moving
            ):
                raise ValueError(
                    f"'{name}' would hide the component {name} placed in {owner}: "
                    "choose another name"
                )
        return path

    def make(self, paths: list[NodePath], name: str) -> NodePath:
        """Move sibling nodes into a new component and put a reference in their place.

        A single transform becomes a component of its children, placed where
        the transform was (with its alignment, modifiers and name). Parameters of
        the active component that the nodes use become parameters of the new
        component (with the same defaults and limits) and are passed through by
        the reference, so the geometry is unchanged.

        The new component is private to the active one (it was part of it);
        :meth:`move` makes it shared. Returns the reference's path.
        """
        short = name
        name = self._check_name(short, self.session.active)
        ordered, first, nodes = self.session.nodes.siblings(paths)
        placement: dict[str, Any] = {"name": self.session.unique_name(short)}
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
                "modifiers": transform.modifiers,
            }
        nodes, placement = _alignment_outside(nodes, placement)
        parameters = [  # passed in by the reference, so public in the new component
            p.model_copy(update={"internal": False})
            for p in parameters_used(self.session.active_definition, nodes)
        ]
        used = {p.name for p in parameters}
        definition = ComponentDef(
            name=name, parameters=parameters, shapes=[n.model_copy(deep=True) for n in nodes]
        )
        reference = RefShape(component=short, params={p: p for p in sorted(used)}, **placement)
        indices = {p[-1][1] for p in ordered}

        def change(project: Project) -> None:
            shapes = self.session.local(project).shapes
            project.components[name] = definition
            target, _ = container_of(shapes, ordered[0])
            kept = [s for i, s in enumerate(target) if i not in indices]
            target[:] = kept[:first] + [reference] + kept[first:]

        self.session.edit(f"Make component {short}", change)
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
        project, active = self.session.project, self.session.active
        found = project.definition(project.qualify(node.component, active))
        if found is None:
            raise ValueError(f"'{node.component}' is an imported cell: it has no shapes")
        definition, context = found
        values = _parameter_values(definition, node.params)
        shapes = map_expressions(
            definition.shapes, lambda n: _as_expression(values[n]) if n in values else None
        )
        for shape in walk(shapes):
            if isinstance(shape, RefShape):  # written as seen from the active component
                target = project.qualify(shape.component, context)
                try:
                    project.qualify(project.reference_name(target, active), active)
                except KeyError:
                    raise ValueError(
                        f"'{node.component}' places its private component '{shape.component}': "
                        "make that shared first, then unpack"
                    ) from None
                shape.component = project.reference_name(target, active)
        own_level = lambda ref: _own_level(project, ref, context)
        shapes = _on_level(shapes, node.level or definition.level, project, own_level)
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
            modifiers=node.modifiers,
        )

        def change(project: Project) -> None:
            container, index = container_of(self.session.shapes_in(project), path)
            container[index] = unpacked

        self.session.edit(f"Unpack {node.name or node.component}", change)
        return path


_NOT_EXPRESSIONS = {
    "kind",
    "name",
    "layer",
    "level",
    "component",
    "op",
    "ends",
    "corners",
    "mapping",
    "grid",
    "keep_out",
}


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


def parameters_used(definition: ComponentDef, nodes: list[Shape]) -> list[ParamDef]:
    """The parameters of ``definition`` that ``nodes`` use, with those their
    defaults and limits use in turn, in the definition's order."""
    by_name = {p.name: p for p in definition.parameters}
    used = _names_used(nodes) & by_name.keys()
    pending = list(used)
    while pending:  # defaults and limits may depend on further parameters
        parameter = by_name[pending.pop()]
        for value in (parameter.default, parameter.min, parameter.max):
            if not isinstance(value, str):
                continue
            for dep in names_in(value) & by_name.keys():
                if dep not in used:
                    used.add(dep)
                    pending.append(dep)
    return [p for p in definition.parameters if p.name in used]


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


def _alignment_outside(
    nodes: list[Shape], placement: dict[str, Any]
) -> tuple[list[Shape], dict[str, Any]]:
    """Nodes aligned to a shape that stays outside the new component (#248): one
    node on its own hands its alignment (and its name, which shapes aligned to it
    use) to the placement, its point being one of the box's, as the component's
    box is the node's; otherwise it cannot be made."""
    inside = {name for node in nodes for name in _names_in_tree(node)}
    outside = [n for n in nodes if n.align is not None and n.align.to.split(".")[0] not in inside]
    if not outside:
        return nodes, placement
    node = outside[0]
    if len(nodes) == 1 and "align" not in placement and node.align.point in BBOX_POINTS:
        moved = {"align": node.align, "name": node.name or placement["name"]}
        return [node.model_copy(update={"align": None})], {**placement, **moved}
    raise ValueError(
        f"'{node.name}' is aligned to '{node.align.to}', which stays outside: choose "
        f"that shape too, or make a component of '{node.name}' on its own"
    )


def _names_in_tree(node: Shape) -> list[str]:
    names = [node.name] if node.name else []
    for field in type(node).child_fields:
        for child in getattr(node, field):
            names += _names_in_tree(child)
    return names


def library_name(folder: str | Path) -> str:
    """The name a library folder is loaded under by default: ``mems-std-lib`` → ``mems_std_lib``."""
    name = re.sub(r"\W+", "_", Path(folder).name).strip("_").lower() or "lib"
    return name if name.isidentifier() else f"lib_{name}"


def _resolved(project: Project, name: str, context: str) -> str | None:
    try:
        return project.qualify(name, context)
    except KeyError:
        return None


def _own_level(project: Project, ref: RefShape, context: str) -> bool:
    """Whether the component ``ref`` places declares its own default level."""
    found = project.definition(project.qualify(ref.component, context))
    return found is not None and found[0].level is not None


def _on_level(shapes: list[Shape], spec: str | None, project: Project, own_level) -> list[Shape]:
    """A placed component's shapes, written as seen from the component placing
    it, where the placement put them on the level ``spec`` (relative, or by
    name): so unpacking keeps every shape on its layer. ``own_level(ref)`` says
    whether a placed component inside has its own level (which then stays)."""
    if spec is None:  # on the placing component's level already
        return shapes
    if is_relative(spec):  # so many levels up: relative names shift
        layer = place = functools.partial(shifted, by=offset(spec))
    else:  # on a level by name: relative names become names
        stack = Stack.of(project.process.levels, project.process.default_level)
        layer = functools.partial(stack.layer, current=spec)
        place = functools.partial(stack.place, own=None, current=spec)

    def level(ref: RefShape) -> str | None:
        if ref.level is None:  # it was on the placed component's level
            return None if own_level(ref) else place(LEVEL)
        return place(ref.level)

    for shape in walk(shapes):
        if isinstance(shape, RefShape):
            shape.level = level(shape)
        else:
            shape.map_layers(layer)
    return shapes
