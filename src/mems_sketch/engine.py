"""The engine: what turns a project into geometry, behind one small interface.

The GUI, editing, the command line and the exporters ask the engine for
builds; the C++ engine (``mems_sketch._core``, ``docs/developer/core-architecture.md``)
builds them::

    engine = Engine()
    engine.load(project)  # the project builds come from (a snapshot)
    build = engine.build("resonator", {"length": 120})
    build.geometry  # merged, per layer (Geometry), on the 1 nm grid
    build.points()  # declared alignment points, µm
    build.records()  # every node as evaluated, for the editor

An engine keeps a cache of built components across loads: loading the next
version of a project after an edit rebuilds only what the edit changed.
Parameters and their checks stay with the project model (Python): the
engine is asked for geometry.
"""

from __future__ import annotations

import dataclasses
import json
from functools import cached_property
from typing import TYPE_CHECKING, Any

from mems_sketch.core.component import DBU_UM, Geometry, builtin_definitions, resolve_params
from mems_sketch.core.imports import ImportedCell, ImportedComponent, cell_geometry
from mems_sketch.core.levels import Stack
from mems_sketch.core.region import Region
from mems_sketch.core.shapes import NodePoints, NodeRecord
from mems_sketch.core.transform import Transform
from mems_sketch.core.user_component import ComponentDef, ParamDef, UserComponent

if TYPE_CHECKING:
    from mems_sketch.core.component import Component, Polygon
    from mems_sketch.core.project import Project
    from mems_sketch.core.shapes import NodePath, Point, Shape

GRID_UM = 0.001
CHORD_UM = 0.005
# What snapping to an output's grid can change in the shape of the geometry.
SNAP_CHANGES = {
    "vanished": "a piece smaller than the grid vanished",
    "split": "a piece came apart (a neck narrower than the grid)",
    "merged": "pieces joined (a gap narrower than the grid closed)",
    "hole_closed": "a hole smaller than the grid filled up",
    "hole_joined": "a hole joined another, or opened to the outside",
    "hole_formed": "a new hole formed (a notch's mouth closed)",
}
PREVIEW = "_preview"  # the component loose shapes are built in


def versions() -> dict[str, str]:
    """The versions of the engine and the geometry library it is built on."""
    from mems_sketch import _core, _geom

    return {"engine": _core.__version__, "geometry library": _geom.__version__}


def _core():
    from mems_sketch import _core

    return _core


class Engine:
    def __init__(self) -> None:
        self._root = None  # the first engine loaded: its cache is shared with every later one
        self._core = None
        self._project: Project | None = None
        self._components: dict[str, Component] = {}
        # Built components as geometry, by the engine's key for them: shared with
        # the trials, so a component that did not change is the same geometry
        # in every version of the project (what draws it can keep its drawing).
        self._cells: dict[str, Geometry] = {}

    def load(self, project: Project) -> Engine:
        """Build from ``project`` from now on. It must not change while it is loaded:
        load the changed project again instead."""
        data = json.dumps(project_data(project))
        if self._root is None:
            self._root = self._core = _core().Engine(data)
        else:
            self._core = self._root.trial(data)
        self._project, self._components = project, {}
        return self

    def trial(self, project: Project) -> Engine:
        """Another engine for a variant of the project (e.g. an edit being tried),
        sharing this one's cache; this engine keeps its project."""
        other = Engine()
        other._root, other._cells = self._root, self._cells
        return other.load(project)

    @property
    def project(self) -> Project:
        if self._project is None:
            raise RuntimeError("no project loaded: call load() first")
        return self._project

    @property
    def constants(self) -> dict[str, float]:
        """The process constants, as expressions see them (``process.<name>``)."""
        return self.project.process.scope()

    def build(self, component: str, params: dict[str, Any] | None = None) -> Build:
        """``component`` (as written at project level) with the given parameter values
        (defaults for the others). Building happens when the result is first used."""
        self.project  # noqa: B018 - a project must be loaded
        return Build(self, component, dict(params or {}))

    def component(self, name: str, context: str | None = None) -> Component:
        """The component ``name`` as written in ``context`` (None: at project level):
        its parameters, and what a placement may set."""
        project = self.project
        qualified = project.qualify(name, context)
        if qualified not in self._components:
            if qualified in project.imports:
                found: Component = ImportedComponent(project.imports[qualified])
            else:
                definition = project.definition(qualified)
                if definition is None:
                    raise KeyError(f"unknown component '{qualified}'")
                stack = Stack.of(project.process.levels, project.process.default_level)
                found = UserComponent(definition[0], project.process.scope(), stack)
            self._components[qualified] = found
        return self._components[qualified]

    def variables(self, component: str, params: dict[str, Any] | None = None) -> dict[str, float]:
        """A component's resolved parameters (defaults unless given) and the process
        constants: what its expressions can use."""
        scope = self.constants
        resolved = resolve_params(self.component(component), params or {}, scope)
        return {**scope, **{k: float(v) for k, v in resolved.model_dump().items()}}

    def shapes(
        self, shapes: list[Shape], variables: dict[str, float], context: str | None = None
    ) -> Geometry:
        """Loose shapes evaluated with ``variables``, e.g. one node of a component
        (``context``: that component, for the names its references use and its level)."""
        project = self.project
        owner = context if context in project.components else None
        parameters = [
            ParamDef(name=name, default=value)
            for name, value in variables.items()
            if name.isidentifier() and name not in ("i", "j")
        ]
        level = None
        if context is not None:
            found = project.definition(project.qualify(context))
            level = found[0].level if found else None
        name = f"{owner}/{PREVIEW}" if owner else PREVIEW
        preview = ComponentDef(name=name, parameters=parameters, shapes=shapes, level=level)
        variant = dataclasses.replace(project, components={**project.components, name: preview})
        return self.trial(variant).build(name).geometry

    def problems(self) -> list[str]:
        """Why the project's own components do not build with their defaults (empty
        when they all do)."""
        project = self.project
        try:
            project.check_references()
        except ValueError as exc:
            return [str(exc)]
        problems = []
        for name in project.components:
            try:
                self.build(name).geometry  # noqa: B018 - building is the check
            except Exception as exc:  # noqa: BLE001 - collected for the caller
                problems.append(f"{name}: {exc}")
        return problems

    @property
    def cached(self) -> int:
        """How many component builds the cache holds (shared with its trials)."""
        return 0 if self._root is None else self._root.cached

    def clear(self) -> None:
        """Forget every cached build."""
        self._cells.clear()
        if self._root is not None:
            self._root.clear()


@dataclasses.dataclass(frozen=True)
class SnapEvent:
    """A change snapping made in the shape of the geometry, and where (µm)."""

    change: str  # one of SNAP_CHANGES
    box: tuple[float, float, float, float]

    def describe(self) -> str:
        x0, y0, x1, y1 = self.box
        return f"{SNAP_CHANGES.get(self.change, self.change)} at ({(x0 + x1) / 2:g}, {(y0 + y1) / 2:g})"


@dataclasses.dataclass(frozen=True)
class LayerSnapping:
    """What snapping one layer to an output's grid changed (areas in µm²)."""

    area_exact: float
    area_snapped: float
    events: tuple[SnapEvent, ...] = ()


@dataclasses.dataclass(frozen=True)
class Output:
    """A component as an output (a layout file, the rule checks) gets it: everything
    merged in and rounded once, from the exact geometry, onto the output's grid with
    curves within its chord (requirements QP-3, QP-4), and what that changed."""

    grid: float  # µm
    chord: float  # µm
    geometry: Geometry  # flat, in the 1 nm units of Geometry, on multiples of the grid
    snapping: dict[str, LayerSnapping]

    @property
    def changed_shape(self) -> bool:
        """Whether snapping changed the shape of anything (beyond moving edges)."""
        return any(s.events for s in self.snapping.values())

    def describe(self) -> list[str]:
        """What snapping changed, a line each: per layer, its events and the area."""
        lines = []
        for layer, snapping in sorted(self.snapping.items()):
            for event in snapping.events:
                lines.append(f"{layer}: {event.describe()}")
            if snapping.events:
                change = snapping.area_snapped - snapping.area_exact
                lines.append(
                    f"{layer}: area {snapping.area_exact:.6g} µm² exact, "
                    f"{snapping.area_snapped:.6g} µm² on the grid ({change:+.3g} µm²)"
                )
        return lines


class Build:
    """One component built with some parameter values. Results are computed when
    first asked for and kept; they are shared, so treat them as read-only."""

    def __init__(self, engine: Engine, component: str, params: dict[str, Any]) -> None:
        self._engine = engine
        self.component = component
        self.params = params

    @cached_property
    def _built(self) -> tuple[Geometry, dict[str, Point]]:
        engine = self._engine
        engine.variables(self.component, self.params)  # the model's checks, with its messages
        key, cells, points = engine._core.tree(self.component, self.params, GRID_UM, CHORD_UM)
        return _cell(engine, cells, key), {name: tuple(p) for name, p in points.items()}

    @property
    def geometry(self) -> Geometry:
        """The geometry, with the components it places as instances (its ``layers``
        flatten them). Raises if the component does not build."""
        return self._built[0]

    @cached_property
    def built(self):
        """The engine's own build of the component (``_core.Built``): outputs are made
        from it. Get it where the engine builds (the main thread); ``output`` may then
        run on another thread, since it only reads what was built."""
        engine = self._engine
        engine.variables(self.component, self.params)  # the model's checks, with its messages
        return engine._core.built(self.component, self.params)

    def output(self, grid: float = GRID_UM, chord: float = CHORD_UM) -> Output:
        """The component for an output with this grid and chord tolerance (µm): see
        :class:`Output`. The grid is a whole multiple of 1 nm. Kept per grid and chord;
        once ``built`` is there, it may be made on another thread."""
        steps = round(grid / DBU_UM)
        if steps < 1 or abs(steps * DBU_UM - grid) > 1e-9 * DBU_UM:
            raise ValueError(f"the grid must be a whole multiple of 1 nm, not {grid:g} µm")
        if not chord > 0:
            raise ValueError(f"the chord tolerance must be positive, not {chord:g} µm")
        outputs = self.__dict__.setdefault("_outputs", {})
        if (grid, chord) not in outputs:
            layers, reports = self.built.output(grid, chord)
            snapping = {
                layer: LayerSnapping(
                    exact, snapped, tuple(SnapEvent(change, tuple(box)) for change, box in events)
                )
                for layer, (exact, snapped, events) in reports.items()
            }
            outputs[(grid, chord)] = Output(grid, chord, _geometry(layers, steps), snapping)
        return outputs[(grid, chord)]

    def layers(self) -> list[str]:
        """The layers with geometry."""
        return self.geometry.layer_names()

    def outlines(self, layer: str) -> list[Polygon]:
        """A layer's polygons as outlines in µm (with their holes), for drawing."""
        return self.geometry.polygons(layer)

    def bbox(self) -> tuple[float, float, float, float] | None:
        return self.geometry.bbox()

    def points(self) -> dict[str, Point]:
        """The component's declared alignment points, µm."""
        return self._built[1]

    def variables(self) -> dict[str, float]:
        """The resolved parameters and process constants the component is built with."""
        return self._engine.variables(self.component, self.params)

    @cached_property
    def _records(self) -> dict[NodePath, NodeRecord]:
        project = self._engine.project
        if project.qualify(self.component) in project.imports:
            return {}
        try:
            self._engine.variables(self.component, self.params)
        except Exception:  # noqa: BLE001 - nothing evaluates; nothing to show
            return {}
        records, cells, _error = self._engine._core.records(
            self.component, self.params, GRID_UM, CHORD_UM
        )
        result = {}
        for path, (layers, instances, name, declared, inner, shift) in records.items():
            geometry = _geometry(layers)
            for key, placement in instances:
                geometry.place(_cell(self._engine, cells, key), _transform(placement))
            points = NodePoints(name, geometry, {k: tuple(p) for k, p in declared.items()})
            result[path] = NodeRecord(geometry, points, _transform(inner), _transform(shift))
        return result

    def records(self) -> dict[NodePath, NodeRecord]:
        """Every node of a project component's shape tree as evaluated (geometry,
        points, frame), by path; empty for other components. When evaluating fails,
        the nodes evaluated so far."""
        return self._records


def _geometry(layers: dict[str, tuple], steps: int = 1) -> Geometry:
    """The engine's layers: ([(hull, [holes])], box) each, in grid units (``steps``
    of the 1 nm units of Geometry each)."""
    geometry = Geometry()
    for layer, (polygons, box) in layers.items():
        if steps != 1:
            polygons = [(hull * steps, [h * steps for h in holes]) for hull, holes in polygons]
            box = tuple(v * steps for v in box) if box is not None else None
        geometry.layers[layer] = Region.from_polygons(polygons, merged=True, box=box)
    return geometry


def _cell(engine: Engine, cells: dict[str, tuple], key: str) -> Geometry:
    """The geometry of the engine's cell ``key`` (``cells`` as its ``tree`` gives
    them), with the cells it places as instances; one geometry per key."""
    known = engine._cells
    if key not in known:
        if len(known) >= 4096:
            known.clear()
        layers, placed = cells[key]
        geometry = _geometry(layers)
        for child, placement in placed:
            geometry.place(_cell(engine, cells, child), _transform(placement))
        known[key] = geometry
    return known[key]


def _transform(t: tuple[float, float, float, bool, float]) -> Transform:
    dx, dy, angle, mirror, scale = t
    return Transform(dx, dy, angle, bool(mirror), scale)


def project_data(project: Project) -> dict[str, Any]:
    """The project as the C++ engine reads it (``mems_sketch._core.Project``): plain
    data, validated by the pydantic model it comes from."""

    def component(definition) -> dict[str, Any]:
        return definition.model_dump(mode="json", exclude={"waivers"})

    return {
        "name": project.name,
        "top": project.top,
        "process": {
            "constants": dict(project.process.constants),
            "levels": [
                {"layer": level.layer, "roles": dict(level.roles)}
                for level in project.process.levels
            ],
            "default_level": project.process.default_level,
        },
        "components": {name: component(d) for name, d in project.components.items()},
        "libraries": {
            name: {n: component(d) for n, d in library.components.items()}
            for name, library in project.libraries.items()
        },
        "imports": {name: _imported_data(cell) for name, cell in project.imports.items()},
        "builtins": {name: component(d) for name, d in builtin_definitions().items()},
    }


_IMPORTED: dict[tuple, dict[str, Any]] = {}  # by file, cell and layer mapping: read once


def _imported_data(cell: ImportedCell) -> dict[str, Any]:
    """An imported cell as the engine reads it: its geometry, or why it has none."""
    key = (cell.digest, cell.cell, tuple(sorted(cell.layers.items())))
    if key not in _IMPORTED:
        entry: dict[str, Any] = {
            "digest": cell.digest,
            "cell": cell.cell,
            "layers": dict(cell.layers),
        }
        try:
            geometry = cell_geometry(cell)
        except ValueError as error:
            entry["error"] = str(error)
        else:
            entry["geometry"] = {
                layer: [
                    (hull.tolist(), [h.tolist() for h in holes])
                    for hull, holes in region.points_um()
                ]
                for layer, region in geometry.layers.items()
            }
        if len(_IMPORTED) > 64:
            _IMPORTED.clear()
        _IMPORTED[key] = entry
    return _IMPORTED[key]


def build_shapes(shapes: list[Shape], variables: dict[str, float] | None = None) -> Geometry:
    """Shapes that place no components, evaluated on their own (e.g. one being drawn)."""
    from mems_sketch.core.project import Project

    preview = ComponentDef(
        name=PREVIEW,
        parameters=[ParamDef(name=n, default=v) for n, v in (variables or {}).items()],
        shapes=shapes,
    )
    project = Project(name="preview", top=None, components={PREVIEW: preview})
    return Engine().load(project).build(PREVIEW).geometry
