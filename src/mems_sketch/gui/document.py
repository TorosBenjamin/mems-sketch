"""The open project, as the GUI sees it: the frontend's only way into the backend.

The GUI never builds geometry or touches files itself; it asks the document,
which uses the backend (project model, compiler, storage, rules, export).

* One component is **active**: the one in the current tab. Shape edits,
  parameters and points refer to it. Any component can be active; library
  and built-in components are read-only.
* **Trial values** per component override parameter defaults for viewing
  only: they are not part of the project, not saved and not undone.
* Every edit runs as a transaction: the change is applied, the project is
  recompiled, and if a project that compiled before no longer does, the change
  is rolled back exactly.
* Undo/redo keep whole-project snapshots (libraries are read-only and shared)
  and remember which component each change was made in, so the GUI can go
  back to it, like a code editor.
* A long-lived :class:`Compiler` caches built components by fingerprint, so
  recompiling after an edit only rebuilds what changed.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import klayout.db as kdb
from PySide6.QtCore import QObject, Signal

from mems_sketch.core.compiler import Compiler, Session
from mems_sketch.core.component import Component, Geometry, is_builtin
from mems_sketch.core.expressions import ExpressionError, names_in
from mems_sketch.core.process import Layer, default_process
from mems_sketch.core.project import Project, check_shape_names
from mems_sketch.core.shapes import (
    Align,
    ArcShape,
    BooleanShape,
    CircleShape,
    FilletShape,
    LayerMapShape,
    NodePath,
    NodeRecord,
    OffsetShape,
    PathShape,
    PolygonShape,
    RectShape,
    RefShape,
    Shape,
    TransformShape,
    Value,
    child_lists,
    container_of,
    frame_of,
    map_expressions,
    node_at,
    rename_node_references,
    rewrite,
    to_ictrans,
    visible_from,
    walk,
)
from mems_sketch.core.user_component import ComponentDef, ParamDef, PointDef
from mems_sketch.export.base import export
from mems_sketch.process import etch, rules
from mems_sketch.storage import load, save
from mems_sketch.storage.project_files import project_folder

UNDO_LIMIT = 200
VIEW_MODES = {"drawn": "Drawn", "etched": "As etched", "compensated": "Etch compensated"}
# Node fields that hold names or choices, not expressions.
_NOT_EXPRESSIONS = {"kind", "name", "layer", "component", "op", "ends", "corners", "mapping"}


def new_project(name: str = "untitled") -> Project:
    return Project(name=name, process=default_process())


class ProjectDocument(QObject):
    changed = Signal()  # the model changed (or trial values); views must refresh
    active_changed = Signal()  # another component became active; nothing else changed
    file_changed = Signal()  # path or dirty flag changed
    component_renamed = Signal(str, str)  # old, new

    def __init__(self, project: Project | None = None) -> None:
        super().__init__()
        self.project = project or new_project()
        self.active = self.project.top
        self.path: Path | None = None
        self.dirty = False
        self.compiler = Compiler()
        # (description, other project state, active before the change, active after it)
        self._undo: list[tuple[str, Project, str, str]] = []
        self._redo: list[tuple[str, Project, str, str]] = []
        self.trials: dict[str, dict[str, Value]] = {}
        self._saved: dict[str, str] = self._fingerprints()
        self._inspection: dict[str, dict[NodePath, NodeRecord]] = {}
        self.changed.connect(self._inspection.clear)

    # -- transactions ------------------------------------------------------

    def edit(
        self,
        description: str,
        change: Callable[[Project], object],
        done: Callable[[], None] | None = None,
    ) -> object:
        """Apply ``change``; roll back and re-raise if a valid project would become invalid.

        ``done`` runs after a successful change, before views are told.
        """
        before, active = self._snapshot(), self.active
        was_valid = not self.problems(before)
        try:
            result = change(self.project)
            if was_valid:
                problems = self.problems()
                if problems:
                    raise ValueError(problems[0])
        except Exception:
            self.project, self.active = before, active
            raise
        if not self.exists(self.active):
            self.active = self.project.top
        self._undo.append((description, before, active, self.active))
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()
        if done is not None:
            done()
        self._set_dirty(True)
        self.changed.emit()
        return result

    def _snapshot(self) -> Project:
        # Libraries are read-only: share them instead of copying.
        return copy.deepcopy(self.project, {id(self.project.libraries): self.project.libraries})

    def problems(self, project: Project | None = None) -> list[str]:
        """Why the project does not compile (empty when it does)."""
        project = project or self.project
        try:
            project.check_references()
        except ValueError as exc:
            return [str(exc)]
        session = self.compiler.session(project)
        problems = []
        for name in project.components:
            try:
                session.render(name)
            except Exception as exc:  # noqa: BLE001 - collected for the caller
                problems.append(f"{name}: {exc}")
        return problems

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_text(self) -> str:
        return self._undo[-1][0] if self._undo else ""

    def redo_text(self) -> str:
        return self._redo[-1][0] if self._redo else ""

    def undo(self) -> None:
        """Undo the last change and make the component it was made in active."""
        if self._undo:
            description, state, before, after = self._undo.pop()
            self._redo.append((description, self.project, before, after))
            self._restore(state, before)

    def redo(self) -> None:
        if self._redo:
            description, state, before, after = self._redo.pop()
            self._undo.append((description, self.project, before, after))
            self._restore(state, after)

    def _restore(self, project: Project, active: str) -> None:
        self.project = project
        self.active = active if self.exists(active) else project.top
        self._set_dirty(self._fingerprints() != self._saved)
        self.changed.emit()

    # -- files -------------------------------------------------------------

    def new(self) -> None:
        self._reset(new_project(), None)

    def open(self, path: str | Path) -> None:
        """Open a project folder, its project.yaml, or a legacy .mems file."""
        path = Path(path)
        project = load(path)
        if path.suffix == ".mems":  # legacy import: must be saved as a project folder
            self._reset(project, None)
            self._set_dirty(True)
        else:
            self._reset(project, project_folder(path))

    def save(self, folder: str | Path | None = None) -> Path:
        target = Path(folder) if folder is not None else self.path
        if target is None:
            raise ValueError("choose a folder to save the project in")
        save(self.project, target)
        self.path = target
        self._saved = self._fingerprints()
        self._set_dirty(False)
        self.changed.emit()  # tabs drop their "modified" marks
        return target

    def export(self, path: str | Path, mode: str = "drawn") -> Path:
        return export(self.project, path, geometry=self.geometry(mode))

    def _reset(self, project: Project, path: Path | None) -> None:
        self.project = project
        self.active = project.top
        self.path = path
        self._undo.clear()
        self._redo.clear()
        self.trials.clear()
        self._saved = self._fingerprints()
        self._set_dirty(False)
        self.changed.emit()

    def _fingerprints(self) -> dict[str, str]:
        return {n: d.model_dump_json() for n, d in self.project.components.items()}

    def modified(self, component: str) -> bool:
        """Whether a local component differs from the saved (or opened) project."""
        if component not in self.project.components:
            return False
        return self._saved.get(component) != self.project.components[component].model_dump_json()

    def _set_dirty(self, dirty: bool) -> None:
        self.dirty = dirty
        self.file_changed.emit()

    # -- queries -----------------------------------------------------------

    def session(self) -> Session:
        return self.compiler.session(self.project)

    def exists(self, component: str) -> bool:
        """Whether ``component`` (as written at project level) can be opened."""
        try:
            self.project.qualify(component)
            return True
        except KeyError:
            return False

    @property
    def read_only(self) -> bool:
        """Library and built-in components can be viewed but not edited."""
        return self.active not in self.project.components

    def definition_of(self, component: str) -> ComponentDef:
        """A component's definition; for a built-in, one listing its numeric parameters."""
        found = self.project.definition(self.project.qualify(component))
        if found is not None:
            return found[0]
        schema = self.session().component(component).Params.model_fields
        return ComponentDef(
            name=component,
            parameters=[
                ParamDef(name=name, default=info.default, description=info.description or "")
                for name, info in schema.items()
                if info.annotation in (int, float)
            ],
        )

    @property
    def shapes(self) -> list[Shape]:
        return self.active_definition.shapes

    @property
    def active_definition(self) -> ComponentDef:
        return self.definition_of(self.active)

    def _local(self, project: Project) -> ComponentDef:
        """The active component inside ``project``, for an edit; refuses read-only ones."""
        if self.active not in project.components:
            raise ValueError(
                f"'{self.active}' is read-only: library and built-in components cannot be "
                "edited here"
            )
        return project.components[self.active]

    # -- trial values ------------------------------------------------------

    def set_trial(self, name: str, value: Value | None, component: str | None = None) -> None:
        """Try a parameter value for viewing only (None goes back to the default)."""
        component = component or self.active
        trials = self.trials.setdefault(component, {})
        if value is None:
            trials.pop(name, None)
        else:
            previous = trials.get(name)
            trials[name] = value
            try:
                self.session().variables(component, self._trials(component))
            except Exception:
                if previous is None:
                    trials.pop(name)
                else:
                    trials[name] = previous
                raise
        self.changed.emit()

    def _trials(self, component: str) -> dict[str, Value]:
        """Trial values that still apply (parameters may have been renamed or removed)."""
        trials = self.trials.get(component)
        if not trials:
            return {}
        try:
            schema = self.session().component(component).Params.model_fields
        except KeyError:
            return {}
        return {k: v for k, v in trials.items() if k in schema}

    # -- evaluated results (of the active component unless another is named) --

    def scope(self, path: NodePath | None = None, component: str | None = None) -> dict[str, float]:
        """Resolved parameters (trial values included) plus ``process.*`` constants.

        With a ``path``, also the point coordinates (``node.point.x``) the node
        there can use, in its own frame, e.g. to preview expressions.
        """
        component = component or self.active
        variables = self.session().variables(component, self._trials(component))
        if path is None:
            return variables
        record = self.inspection(component)
        if path[:-1] and path[:-1] not in record:
            return variables
        into = frame_of(record, path).inverted() if path[:-1] else None
        for name, _, x, y in self.align_targets(path, component):
            if into is not None:
                p = into * kdb.DPoint(x, y)
                x, y = p.x, p.y
            variables[f"{name}.x"], variables[f"{name}.y"] = x, y
        return variables

    def inspection(self, component: str | None = None) -> dict[NodePath, NodeRecord]:
        """Every node of a component as evaluated (cached until the next change)."""
        component = component or self.active
        if component not in self._inspection:
            self._inspection[component] = self.session().inspect(component, self._trials(component))
        return self._inspection[component]

    def geometry(self, mode: str = "drawn", component: str | None = None) -> Geometry:
        component = component or self.active
        drawn = self.session().render(component, self._trials(component))
        if mode == "etched":
            return etch.etched(self.project, drawn)
        if mode == "compensated":
            return etch.compensated(self.project, drawn)
        return drawn

    def check(
        self, drawn: Geometry | None = None, component: str | None = None
    ) -> list[rules.Violation]:
        geometry = self.geometry(component=component) if drawn is None else drawn
        return rules.check(self.project, geometry)

    def component_names(self) -> list[str]:
        return self.project.component_names()

    def component(self, name: str) -> Component:
        return self.session().component(name)

    def parameter_defaults(self, component: str) -> dict[str, Any]:
        """Declared defaults (numbers or expressions) of a component's parameters."""
        built = self.component(component)
        definition = getattr(built, "definition", None)
        if definition is not None:
            return {p.name: p.default for p in definition.parameters}
        return {name: info.default for name, info in built.Params.model_fields.items()}

    def node(self, path: NodePath) -> Shape:
        return node_at(self.shapes, path)

    def unique_name(self, stem: str) -> str:
        return _fresh(stem, {s.name for s in walk(self.shapes) if s.name})

    def node_regions(
        self, visible: dict[str, bool], component: str | None = None
    ) -> list[tuple[NodePath, kdb.Region]]:
        """Merged geometry of each top-level node of a component, for click selection."""
        result = []
        for path, record in sorted(self.inspection(component).items()):
            if len(path) != 1:
                continue
            region = kdb.Region()
            for layer, r in record.geometry.layers.items():
                if visible.get(layer, True):
                    region.insert(r)
            result.append((path, region))
        return result

    def highlight(self, paths: list[NodePath], component: str | None = None) -> Geometry | None:
        """Outline geometry of the given nodes, placed as they appear in the component."""
        record = self.inspection(component)
        result = Geometry()
        for path in paths:
            if path not in record:
                continue
            result.merge(record[path].geometry, to_ictrans(frame_of(record, path)))
        return result.merged() if result.layers else None

    def node_points(
        self, path: NodePath, component: str | None = None
    ) -> list[tuple[str, float, float]]:
        """The alignment points of a node, in its component's frame."""
        record = self.inspection(component)
        if path not in record:
            return []
        frame = frame_of(record, path)
        result = []
        for name in record[path].points.names():
            try:
                x, y = record[path].points.point(name)
            except ValueError:  # e.g. no geometry, so no bounding box
                continue
            p = frame * kdb.DPoint(x, y)
            result.append((name, p.x, p.y))
        return result

    def align_targets(
        self, path: NodePath, component: str | None = None
    ) -> list[tuple[str, NodePath, float, float]]:
        """Points the node at ``path`` can align to: ``(node.point, node path, x, y)``.

        Coordinates are in the component's frame.
        """
        component = component or self.active
        shapes = self.definition_of(component).shapes
        result = []
        for other in sorted(self.inspection(component)):
            if not visible_from(path, other):
                continue
            name = node_at(shapes, other).name
            if name is None:
                continue
            result += [
                (f"{name}.{p}", other, x, y) for p, x, y in self.node_points(other, component)
            ]
        return result

    def declared_points(self, component: str | None = None) -> dict[str, tuple[float, float]]:
        """Positions of a component's declared points (trial values included)."""
        component = component or self.active
        return self.session().points(component, self._trials(component))

    def reference_target(self, path: NodePath, component: str | None = None) -> str | None:
        """The component a ``ref`` node places, named as the project sees it, else None."""
        component = component or self.active
        node = node_at(self.definition_of(component).shapes, path)
        if not isinstance(node, RefShape):
            return None
        namespace = component.partition(".")[0] if "." in component else None
        return self.project.qualify(node.component, namespace)

    # -- components --------------------------------------------------------

    def set_active(self, name: str) -> None:
        """Make a component active: a local one to edit, or a library/built-in one to view."""
        if not self.exists(name):
            raise ValueError(f"unknown component '{name}'")
        if name != self.active:
            self.active = name
            self.active_changed.emit()

    def new_component(self, name: str) -> str:
        self._check_component_name(name)
        self.edit(
            f"New component {name}",
            lambda p: p.components.__setitem__(name, ComponentDef(name=name)),
        )
        self.set_active(name)
        return name

    def delete_component(self, name: str) -> None:
        self.edit(f"Delete component {name}", lambda p: p.remove_component(name))

    def rename_component(self, old: str, new: str) -> None:
        self._check_component_name(new)

        def change(project: Project) -> None:
            project.rename_component(old, new)
            if self.active == old:
                self.active = new

        self.edit(f"Rename component {old}", change, lambda: self.component_renamed.emit(old, new))

    def set_top(self, name: str) -> None:
        def change(project: Project) -> None:
            if name not in project.components:
                raise ValueError(f"'{name}' is not a local component")
            project.top = name

        self.edit(f"Make {name} the top component", change)

    def _check_component_name(self, name: str) -> None:
        if not name.isidentifier():
            raise ValueError(f"'{name}' is not a valid component name")
        if name in self.project.components or is_builtin(name):
            raise ValueError(f"a component named '{name}' already exists")

    def make_component(self, paths: list[NodePath], name: str) -> NodePath:
        """Move sibling nodes into a new component and put a reference in their place.

        A single transform becomes a component of its children, placed where
        the transform was (with its alignment, repeat and name). Parameters of the active component that the nodes use become parameters
        of the new component (with the same defaults and limits) and are passed
        through by the reference, so the geometry is unchanged.
        """
        self._check_component_name(name)
        ordered, first, nodes = self._siblings(paths)
        placement: dict[str, Any] = {"name": self.unique_name(name)}
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
        definition_params = {p.name: p for p in self.active_definition.parameters}
        used = _names_used(nodes) & definition_params.keys()
        pending = list(used)
        while pending:  # defaults may depend on further parameters
            default = definition_params[pending.pop()].default
            if isinstance(default, str):
                for dep in names_in(default) & definition_params.keys():
                    if dep not in used:
                        used.add(dep)
                        pending.append(dep)
        parameters = [p.model_copy() for p in self.active_definition.parameters if p.name in used]
        definition = ComponentDef(
            name=name, parameters=parameters, shapes=[n.model_copy(deep=True) for n in nodes]
        )
        reference = RefShape(component=name, params={p: p for p in sorted(used)}, **placement)
        indices = {p[-1][1] for p in ordered}

        def change(project: Project) -> None:
            shapes = self._local(project).shapes
            project.components[name] = definition
            target, _ = container_of(shapes, ordered[0])
            kept = [s for i, s in enumerate(target) if i not in indices]
            target[:] = kept[:first] + [reference] + kept[first:]

        self.edit(f"Make component {name}", change)
        return (*ordered[0][:-1], (ordered[0][-1][0], first))

    def unpack(self, path: NodePath) -> NodePath:
        """Replace a component reference by a transform holding a copy of its shapes.

        The component's parameters are replaced by the values the reference
        passed (or the defaults), so the geometry is unchanged. Shapes whose
        names are taken are renamed. The component's own declared points are
        not kept: a transform only has bounding-box points.
        """
        node = self.node(path)
        if not isinstance(node, RefShape):
            raise ValueError("select a component reference to unpack")  # noqa: TRY004
        found = self.project.definition(self.project.qualify(node.component))
        if found is None:
            raise ValueError(f"'{node.component}' is a built-in component: it has no shapes")
        definition, namespace = found
        values = _parameter_values(definition, node.params)
        shapes = map_expressions(
            definition.shapes, lambda n: _as_expression(values[n]) if n in values else None
        )
        for shape in walk(shapes):
            if isinstance(shape, RefShape) and namespace is not None:
                shape.component = self.project.qualify(shape.component, namespace)
        taken = {s.name for s in walk(self.shapes) if s.name}
        for old in [s.name for s in walk(shapes) if s.name in taken]:
            new = _fresh(old, taken | {s.name for s in walk(shapes) if s.name})
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
            container, index = container_of(self._shapes_in(project), path)
            container[index] = unpacked

        self.edit(f"Unpack {node.name or node.component}", change)
        return path

    def set_align(self, path: NodePath, align: Align | None) -> None:
        """Align a node (``None`` removes its alignment)."""
        node = self.node(path)
        self.replace_node(path, node.model_copy(update={"align": align}))

    # -- declared points of the active component ---------------------------

    def add_point(self) -> str:
        name = _fresh("point", {p.name for p in self.active_definition.points})
        self.edit(
            f"Add point {name}",
            lambda p: self._local(p).points.append(PointDef(name=name)),
        )
        return name

    def update_point(self, name: str, /, **fields: Any) -> None:
        """Change any field of a declared point (name, at, x, y, description)."""

        def change(project: Project) -> None:
            points = self._local(project).points
            for index, existing in enumerate(points):
                if existing.name == name:
                    updated = PointDef.model_validate({**existing.model_dump(), **fields})
                    if updated.name != name and any(p.name == updated.name for p in points):
                        raise ValueError(f"point '{updated.name}' already exists")
                    points[index] = updated
                    return
            raise KeyError(name)

        self.edit(f"Edit point {name}", change)

    def remove_point(self, name: str) -> None:
        def change(project: Project) -> None:
            definition = self._local(project)
            definition.points = [p for p in definition.points if p.name != name]

        self.edit(f"Delete point {name}", change)

    # -- shape edits (on the active component) -----------------------------

    def _shapes_in(self, project: Project) -> list[Shape]:
        return self._local(project).shapes

    def add_shape(self, shape: Shape) -> NodePath:
        if shape.name is None:
            shape = shape.model_copy(update={"name": self.unique_name(shape.kind)})
        self.edit(f"Add {shape.name}", lambda p: self._shapes_in(p).append(shape))
        return ((0, len(self.shapes) - 1),)

    def add_primitive(self, kind: str, layer: str = "device") -> NodePath:
        return self.add_shape(default_primitive(kind, layer))

    def add_component(self, component: str) -> NodePath:
        stem = component.rsplit(".", 1)[-1].split("_")[0]
        return self.add_shape(RefShape(name=self.unique_name(stem), component=component))

    def replace_node(self, path: NodePath, new: Shape) -> None:
        """Replace a node. A new name is also used by alignments and point expressions."""
        old_name = self.node(path).name

        def change(project: Project) -> None:
            if old_name and new.name and new.name != old_name:
                self._local(project).rename_shape(old_name, new.name)
            container, index = container_of(self._shapes_in(project), path)
            container[index] = new
            check_shape_names(self._shapes_in(project))

        self.edit(f"Edit {new.name or new.kind}", change)

    def remove_nodes(self, paths: list[NodePath]) -> None:
        def change(project: Project) -> None:
            # Resolve all targets before removing anything, so indices stay valid.
            targets = [container_of(self._shapes_in(project), p) for p in paths]
            doomed = {(id(c), i) for c, i in targets}
            for container in {id(c): c for c, _ in targets}.values():
                container[:] = [
                    s for i, s in enumerate(container) if (id(container), i) not in doomed
                ]

        self.edit("Delete", change)

    def duplicate(self, path: NodePath) -> NodePath:
        copy_ = self.node(path).model_copy(deep=True)
        taken = {s.name for s in walk(self.shapes) if s.name}
        for node in walk([copy_]):
            if node.name:
                node.name = _fresh(node.name, taken)
                taken.add(node.name)
        return self.add_shape(copy_)

    def set_enabled(self, path: NodePath, enabled: bool) -> None:
        node = self.node(path)
        self.replace_node(path, node.model_copy(update={"enabled": enabled}))

    def _siblings(self, paths: list[NodePath]) -> tuple[list[NodePath], int, list[Shape]]:
        if not paths:
            raise ValueError("select one or more shapes first")
        if len({p[:-1] + ((p[-1][0], -1),) for p in paths}) != 1:
            raise ValueError("the selected shapes must be siblings (same parent)")
        ordered = sorted(paths, key=lambda p: p[-1][1])
        container, _ = container_of(self.shapes, ordered[0])
        return ordered, ordered[0][-1][1], [container[p[-1][1]] for p in ordered]

    def wrap(self, paths: list[NodePath], operation: str) -> NodePath:
        """Replace sibling nodes with a new operation node that contains them.

        ``operation`` is ``transform``, ``offset``, ``fillet``, ``layer_map`` or one of
        the boolean ops; for booleans the first node becomes ``a`` and the rest ``b``.
        """
        ordered, first, nodes = self._siblings(paths)
        name = self.unique_name("transform" if operation == "group" else operation)
        match operation:
            case "union" | "subtract" | "intersect" | "xor":
                if len(nodes) < 2:
                    raise ValueError(f"{operation} needs at least two selected shapes")
                wrapper: Shape = BooleanShape(name=name, op=operation, a=nodes[:1], b=nodes[1:])
            case "transform" | "group":
                wrapper = TransformShape(name=name, children=nodes)
            case "offset":
                wrapper = OffsetShape(name=name, distance=1.0, children=nodes)
            case "fillet":
                wrapper = FilletShape(name=name, radius=1.0, children=nodes)
            case "layer_map":
                layers = sorted({getattr(n, "layer", None) for n in walk(nodes)} - {None})
                wrapper = LayerMapShape(
                    name=name,
                    mapping={layer: layer for layer in layers} or {"device": "device"},
                    children=nodes,
                )
            case _:
                raise ValueError(f"unknown operation '{operation}'")
        indices = {p[-1][1] for p in ordered}

        def change(project: Project) -> None:
            target, _ = container_of(self._shapes_in(project), ordered[0])
            kept = [s for i, s in enumerate(target) if i not in indices]
            target[:] = kept[:first] + [wrapper] + kept[first:]

        self.edit(f"{operation.replace('_', ' ').capitalize()} {len(nodes)} shape(s)", change)
        return (*ordered[0][:-1], (ordered[0][-1][0], first))

    def unwrap(self, path: NodePath) -> None:
        """Replace an operation node by its children (the inverse of :meth:`wrap`)."""
        node = self.node(path)
        children = [c for group in child_lists(node) for c in group]
        if not children:
            raise ValueError("only operations can be unwrapped")

        def change(project: Project) -> None:
            container, index = container_of(self._shapes_in(project), path)
            container[index : index + 1] = children

        self.edit(f"Unwrap {node.name or node.kind}", change)

    # -- parameters of the active component --------------------------------

    def set_parameter(self, name: str, default: float | str, **limits: Any) -> None:
        def change(project: Project) -> None:
            self._local(project)
            project.set_parameter(name, default, self.active, **limits)

        self.edit(f"Set {name}", change)

    def update_parameter(self, name: str, /, **fields: Any) -> None:
        """Change any field of a parameter (name, default, min, max, integer, description)."""

        def change(project: Project) -> None:
            parameters = self._local(project).parameters
            for index, existing in enumerate(parameters):
                if existing.name == name:
                    updated = ParamDef.model_validate({**existing.model_dump(), **fields})
                    if updated.name != name and any(p.name == updated.name for p in parameters):
                        raise ValueError(f"parameter '{updated.name}' already exists")
                    parameters[index] = updated
                    return
            raise KeyError(name)

        self.edit(f"Edit parameter {name}", change)

    def add_parameter(self) -> str:
        taken = {p.name for p in self.active_definition.parameters}
        name = _fresh("param", taken)
        self.set_parameter(name, 0.0)
        return name

    def remove_parameter(self, name: str) -> None:
        def change(project: Project) -> None:
            self._local(project)
            project.remove_parameter(name, self.active)

        self.edit(f"Delete {name}", change)

    # -- process -----------------------------------------------------------

    def set_constant(self, name: str, value: float | str) -> None:
        def change(project: Project) -> None:
            if not name.isidentifier():
                raise ValueError(f"'{name}' is not a valid constant name")
            project.process.constants[name] = value
            project.process.scope()

        self.edit(f"Set process.{name}", change)

    def rename_constant(self, old: str, new: str) -> None:
        def change(project: Project) -> None:
            if not new.isidentifier():
                raise ValueError(f"'{new}' is not a valid constant name")
            if new in project.process.constants:
                raise ValueError(f"constant '{new}' already exists")
            project.process.constants = {
                (new if k == old else k): v for k, v in project.process.constants.items()
            }

        self.edit(f"Rename process.{old}", change)

    def remove_constant(self, name: str) -> None:
        self.edit(f"Delete process.{name}", lambda p: p.process.constants.pop(name))

    def add_constant(self) -> str:
        name = _fresh("constant", set(self.project.process.constants))
        self.set_constant(name, 0.0)
        return name

    def set_layer(self, name: str, layer: Layer) -> None:
        def change(project: Project) -> None:
            if layer.name != name and layer.name in project.layers:
                raise ValueError(f"layer '{layer.name}' already exists")
            project.layers = {
                (layer.name if k == name else k): (layer if k == name else v)
                for k, v in project.layers.items()
            }

        self.edit(f"Edit layer {name}", change)

    def add_layer(self) -> str:
        existing = self.project.layers
        name = _fresh("layer", set(existing))
        gds = max((ly.gds_layer for ly in existing.values()), default=0) + 1
        self.edit(f"Add layer {name}", lambda p: p.add_layer(Layer(name, gds)))
        return name

    def remove_layer(self, name: str) -> None:
        self.edit(f"Delete layer {name}", lambda p: p.layers.pop(name))


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


def _fresh(name: str, taken: set[str]) -> str:
    """``name`` with its numeric suffix replaced by the lowest one not in ``taken``."""
    stem = re.sub(r"\d+$", "", name) or "shape"
    n = 1
    while f"{stem}{n}" in taken:
        n += 1
    return f"{stem}{n}"


def default_primitive(kind: str, layer: str = "device") -> Shape:
    match kind:
        case "rect":
            return RectShape(layer=layer, x0=0, y0=0, x1=100, y1=50)
        case "circle":
            return CircleShape(layer=layer, radius=25)
        case "arc":
            return ArcShape(layer=layer, inner_radius=20, outer_radius=30, end_angle=180)
        case "polygon":
            return PolygonShape(layer=layer, points=[(0, 0), (60, 0), (30, 50)])
        case "path":
            return PathShape(layer=layer, points=[(0, 0), (100, 0), (100, 60)], width=4)
    raise ValueError(f"unknown primitive '{kind}'")
