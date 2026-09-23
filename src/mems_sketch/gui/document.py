"""The open project, as the GUI sees it: the frontend's only way into the backend.

The GUI never builds geometry or touches files itself; it asks the document,
which uses the backend (project model, compiler, storage, rules, export).

* One component is **active**: the one being edited and displayed. Shape
  edits, parameters and the canvas all refer to it.
* Every edit runs as a transaction: the change is applied, the project is
  recompiled, and if a project that compiled before no longer does, the change
  is rolled back exactly.
* Undo/redo keep whole-project snapshots (libraries are read-only and shared).
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
    ArcShape,
    BooleanShape,
    CircleShape,
    FilletShape,
    GroupShape,
    LayerMapShape,
    NodePath,
    OffsetShape,
    PathShape,
    PolygonShape,
    RectShape,
    RefShape,
    Shape,
    child_lists,
    container_of,
    node_at,
    placement_of,
    walk,
)
from mems_sketch.core.user_component import ComponentDef, ParamDef
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
    changed = Signal()  # the model or the active component changed; views must refresh
    file_changed = Signal()  # path or dirty flag changed

    def __init__(self, project: Project | None = None) -> None:
        super().__init__()
        self.project = project or new_project()
        self.active = self.project.top
        self.path: Path | None = None
        self.dirty = False
        self.compiler = Compiler()
        self._undo: list[tuple[str, Project, str]] = []
        self._redo: list[tuple[str, Project, str]] = []

    # -- transactions ------------------------------------------------------

    def edit(self, description: str, change: Callable[[Project], object]) -> object:
        """Apply ``change``; roll back and re-raise if a valid project would become invalid."""
        before = self._snapshot()
        was_valid = not self.problems(before)
        try:
            result = change(self.project)
            if was_valid:
                problems = self.problems()
                if problems:
                    raise ValueError(problems[0])
        except Exception:
            self.project = before
            raise
        if self.active not in self.project.components:
            self.active = self.project.top
        self._undo.append((description, before, self.active))
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()
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
        if self._undo:
            description, state, active = self._undo.pop()
            self._redo.append((description, self.project, self.active))
            self._restore(state, active)

    def redo(self) -> None:
        if self._redo:
            description, state, active = self._redo.pop()
            self._undo.append((description, self.project, self.active))
            self._restore(state, active)

    def _restore(self, project: Project, active: str) -> None:
        self.project = project
        self.active = active if active in project.components else project.top
        self._set_dirty(True)
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
        self._set_dirty(False)
        return target

    def export(self, path: str | Path, mode: str = "drawn") -> Path:
        return export(self.project, path, geometry=self.geometry(mode))

    def _reset(self, project: Project, path: Path | None) -> None:
        self.project = project
        self.active = project.top
        self.path = path
        self._undo.clear()
        self._redo.clear()
        self._set_dirty(False)
        self.changed.emit()

    def _set_dirty(self, dirty: bool) -> None:
        self.dirty = dirty
        self.file_changed.emit()

    # -- queries -----------------------------------------------------------

    def session(self) -> Session:
        return self.compiler.session(self.project)

    @property
    def shapes(self) -> list[Shape]:
        return self.project.components[self.active].shapes

    @property
    def active_definition(self) -> ComponentDef:
        return self.project.components[self.active]

    def scope(self) -> dict[str, float]:
        """Resolved parameters of the active component plus ``process.*`` constants."""
        return self.session().variables(self.active)

    def geometry(self, mode: str = "drawn") -> Geometry:
        drawn = self.session().render(self.active)
        if mode == "etched":
            return etch.etched(self.project, drawn)
        if mode == "compensated":
            return etch.compensated(self.project, drawn)
        return drawn

    def check(self, drawn: Geometry | None = None) -> list[rules.Violation]:
        return rules.check(self.project, self.geometry() if drawn is None else drawn)

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

    def node_regions(self, visible: dict[str, bool]) -> list[tuple[NodePath, kdb.Region]]:
        """Merged geometry of each top-level node of the active component, for click selection."""
        session = self.session()
        try:
            variables = session.variables(self.active)
        except Exception:  # noqa: BLE001 - nothing selectable while broken
            return []
        result = []
        for index, shape in enumerate(self.shapes):
            if not shape.enabled:
                continue
            try:
                geometry = session.render_shapes([shape], variables)
            except Exception:  # noqa: BLE001
                continue
            region = kdb.Region()
            for layer, r in geometry.layers.items():
                if visible.get(layer, True):
                    region.insert(r)
            result.append((((0, index),), region))
        return result

    def highlight(self, paths: list[NodePath]) -> Geometry | None:
        """Outline geometry of the given nodes, placed as they appear in the component."""
        session = self.session()
        result = Geometry()
        try:
            variables = session.variables(self.active)
            for path in paths:
                node = self.node(path)
                geometry = session.render_shapes([node], {**variables, "i": 0.0, "j": 0.0})
                result.merge(geometry, placement_of(self.shapes, path, variables))
        except Exception:  # noqa: BLE001 - nothing to highlight
            return None
        return result.merged()

    # -- components --------------------------------------------------------

    def set_active(self, name: str) -> None:
        if name not in self.project.components:
            raise ValueError(
                f"'{name}' is not a component of this project (library components are read-only)"
            )
        if name != self.active:
            self.active = name
            self.changed.emit()

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
        self.edit(f"Rename component {old}", lambda p: p.rename_component(old, new))
        if self.active == old:
            self.active = new
            self.changed.emit()

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

        Parameters of the active component that the nodes use become parameters
        of the new component (with the same defaults and limits) and are passed
        through by the reference, so the geometry is unchanged.
        """
        self._check_component_name(name)
        ordered, first, nodes = self._siblings(paths)
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
        reference = RefShape(
            name=self.unique_name(name), component=name, params={p: p for p in sorted(used)}
        )
        indices = {p[-1][1] for p in ordered}
        active = self.active

        def change(project: Project) -> None:
            project.components[name] = definition
            target, _ = container_of(project.components[active].shapes, ordered[0])
            kept = [s for i, s in enumerate(target) if i not in indices]
            target[:] = kept[:first] + [reference] + kept[first:]

        self.edit(f"Make component {name}", change)
        return (*ordered[0][:-1], (ordered[0][-1][0], first))

    # -- shape edits (on the active component) -----------------------------

    def _shapes_in(self, project: Project) -> list[Shape]:
        return project.components[self.active].shapes

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
        def change(project: Project) -> None:
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

        ``operation`` is ``group``, ``offset``, ``fillet``, ``layer_map`` or one of
        the boolean ops; for booleans the first node becomes ``a`` and the rest ``b``.
        """
        ordered, first, nodes = self._siblings(paths)
        name = self.unique_name(operation)
        match operation:
            case "union" | "subtract" | "intersect" | "xor":
                if len(nodes) < 2:
                    raise ValueError(f"{operation} needs at least two selected shapes")
                wrapper: Shape = BooleanShape(name=name, op=operation, a=nodes[:1], b=nodes[1:])
            case "group":
                wrapper = GroupShape(name=name, children=nodes)
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
        self.edit(f"Set {name}", lambda p: p.set_parameter(name, default, self.active, **limits))

    def update_parameter(self, name: str, **fields: Any) -> None:
        """Change any field of a parameter (name, default, min, max, integer, description)."""

        def change(project: Project) -> None:
            parameters = project.components[self.active].parameters
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
        self.edit(f"Delete {name}", lambda p: p.remove_parameter(name, self.active))

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
