"""The engine: what turns a project into geometry, behind one small interface.

The GUI, editing, the command line and the exporters ask the engine for
builds and never use the compiler or the shape evaluator themselves, so the
backend behind it can change: today it is the Python compiler
(:mod:`mems_sketch.core.compiler`), later the C++ engine
(``docs/developer/core-architecture.md``)::

    engine = Engine()
    engine.load(project)  # the project builds come from (a snapshot)
    build = engine.build("resonator", {"length": 120})
    build.geometry  # merged, per layer (Geometry)
    build.points()  # declared alignment points, µm
    build.records()  # every node as evaluated, for the editor

An engine keeps a cache of built components across loads: loading the next
version of a project after an edit rebuilds only what the edit changed.
"""

from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING, Any

from mems_sketch.core.compiler import (
    DEFAULT_CACHE_ENTRIES,
    Compiler,
    Session,
    _builtin_fingerprint,
)
from mems_sketch.core.component import component_types, get_component
from mems_sketch.core.shapes.render import Evaluator

if TYPE_CHECKING:
    from mems_sketch.core.component import Component, Geometry, Polygon
    from mems_sketch.core.project import Project
    from mems_sketch.core.shapes import NodePath, NodeRecord, Point, Shape


class Engine:
    def __init__(self, max_entries: int = DEFAULT_CACHE_ENTRIES) -> None:
        self._compiler = Compiler(max_entries)
        self._session: Session | None = None

    def load(self, project: Project) -> Engine:
        """Build from ``project`` from now on. It must not change while it is loaded:
        load the changed project again instead."""
        self._session = self._compiler.session(project)
        return self

    def trial(self, project: Project) -> Engine:
        """Another engine for a variant of the project (e.g. an edit being tried),
        sharing this one's cache; this engine keeps its project."""
        other = Engine.__new__(Engine)
        other._compiler = self._compiler
        other._session = self._compiler.session(project)
        return other

    @property
    def project(self) -> Project:
        return self._loaded.project

    @property
    def constants(self) -> dict[str, float]:
        """The process constants, as expressions see them (``process.<name>``)."""
        return self._loaded.scope

    def build(self, component: str, params: dict[str, Any] | None = None) -> Build:
        """``component`` (as written at project level) with the given parameter values
        (defaults for the others). Building happens when the result is first used."""
        return Build(self._loaded, component, dict(params or {}))

    def component(self, name: str, context: str | None = None) -> Component:
        """The buildable component ``name`` as written in ``context`` (None: at
        project level): its parameters, and what it places."""
        return self._loaded.component(name, context)

    def variables(self, component: str, params: dict[str, Any] | None = None) -> dict[str, float]:
        """A component's resolved parameters (defaults unless given) and the process
        constants: what its expressions can use."""
        return self._loaded.variables(component, params)

    def shapes(
        self, shapes: list[Shape], variables: dict[str, float], context: str | None = None
    ) -> Geometry:
        """Loose shapes evaluated with ``variables``, e.g. one node of a component
        (``context``: that component, for the names its references use)."""
        return self._loaded.render_shapes(shapes, variables, context)

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

    def clear(self) -> None:
        """Forget every cached build."""
        self._compiler.clear()

    @property
    def _loaded(self) -> Session:
        if self._session is None:
            raise RuntimeError("no project loaded: call load() first")
        return self._session


class Build:
    """One component built with some parameter values. Results are computed when
    first asked for and kept; they are shared, so treat them as read-only."""

    def __init__(self, session: Session, component: str, params: dict[str, Any]) -> None:
        self._session = session
        self.component = component
        self.params = params

    @cached_property
    def geometry(self) -> Geometry:
        """The merged geometry, per layer. Raises if the component does not build."""
        return self._session.render(self.component, self.params)

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
        return self._session.points(self.component, self.params)

    def variables(self) -> dict[str, float]:
        """The resolved parameters and process constants the component is built with."""
        return self._session.variables(self.component, self.params)

    @cached_property
    def _records(self) -> dict[NodePath, NodeRecord]:
        return self._session.inspect(self.component, self.params)

    def records(self) -> dict[NodePath, NodeRecord]:
        """Every node of a project component's shape tree as evaluated (geometry,
        points, frame), by path; empty for other components. When evaluating fails,
        the nodes evaluated so far."""
        return self._records


def project_data(project: Project) -> dict[str, Any]:
    """The project as the C++ engine reads it (``mems_sketch._core.Project``): plain
    data, validated by the pydantic model it comes from."""

    def component(definition) -> dict[str, Any]:
        return definition.model_dump(mode="json", exclude={"waivers"})

    return {
        "name": project.name,
        "top": project.top,
        "process": {"constants": dict(project.process.constants)},
        "components": {name: component(d) for name, d in project.components.items()},
        "libraries": {
            name: {n: component(d) for n, d in library.components.items()}
            for name, library in project.libraries.items()
        },
        "imports": {
            name: {"digest": cell.digest, "cell": cell.cell, "layers": dict(cell.layers)}
            for name, cell in project.imports.items()
        },
        "builtins": {
            name: _builtin_fingerprint(type(get_component(name))) for name in component_types()
        },
    }


def build_shapes(shapes: list[Shape], variables: dict[str, float] | None = None) -> Geometry:
    """Shapes that place no components, evaluated on their own (e.g. one being drawn)."""

    def no_components(name: str):
        raise KeyError(name)

    return Evaluator(no_components).render(shapes, dict(variables or {}))
