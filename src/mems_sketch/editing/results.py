"""What the components evaluate to: geometry, rule checks, node positions and points."""

from __future__ import annotations

import dataclasses
import math
import threading
import time
from concurrent.futures import Executor, Future
from typing import TYPE_CHECKING

from mems_sketch import engine as engine_module
from mems_sketch.core.component import Geometry
from mems_sketch.core.shapes import (
    BBOX_POINTS,
    NodePath,
    NodePoints,
    NodeRecord,
    Shape,
    container_of,
    frame_of,
    node_at,
    visible_from,
)
from mems_sketch.engine import Build
from mems_sketch.process import rules

if TYPE_CHECKING:
    from mems_sketch.editing.session import EditSession


class Results:
    """What a session's components evaluate to, computed on demand and cached until
    the session changes. Of the active component unless another is named.

    A big design takes a while to build: :meth:`build_in_background` builds it on
    another thread, and what is asked for meanwhile waits for that build rather
    than starting another."""

    def __init__(self, session: EditSession) -> None:
        self.session = session
        self.version = 0  # counts the session's changes: what a result was made for
        # How long the components of this version took to build, of those that were
        # built (what was built before takes no time and says nothing).
        self.build_seconds: dict[str, float] = {}
        self._lock = threading.Lock()  # results stored by a background build
        self._inspection: dict[str, dict[NodePath, NodeRecord]] = {}
        self._problems: list[str] | None = None
        self._pending: tuple[int, Future] | None = None
        self._building = None  # the engine of the background build, while it runs
        session.changed.connect(self._changed)

    def _changed(self) -> None:
        building = self._building
        if building is not None:
            building.cancel()  # its design is no longer current: stop it, mid-boolean too
        with self._lock:
            self.version += 1
            self._inspection.clear()
            self._problems = None
            self.build_seconds = {}

    def build_in_background(self, components: list[str], executor: Executor) -> Future:
        """Build ``components`` (with their trial values) and check the project, on
        ``executor``, for the session as it is now. The future's result says whether
        the results are still current (False: the session changed meanwhile)."""
        version, session = self.version, self.session
        session.engine  # noqa: B018 - loaded, so that the copy shares its caches
        engine = session._engine.trial(session._snapshot())  # its own copy of the project
        wanted = [(c, session.trials_for(c)) for c in dict.fromkeys(components)]

        def run() -> bool:
            try:
                return build()
            except engine_module.BuildCancelled:
                return False  # a newer change: its build follows
            finally:
                if self._building is engine:
                    self._building = None

        def build() -> bool:
            if self.version != version:
                return False  # a newer change came first: its build follows
            records, seconds = {}, {}
            for component, trials in wanted:
                build = engine.build(component, trials)
                started, cached = time.perf_counter(), engine.cached
                try:
                    build.geometry  # noqa: B018 - building is the point
                except engine_module.BuildCancelled:
                    raise
                except Exception:  # noqa: BLE001, S112 - asked for again, it says why
                    continue
                if engine.cached != cached:
                    seconds[component] = time.perf_counter() - started
                records[component] = build.records()
            problems = engine.problems()
            with self._lock:
                if self.version != version:
                    return False
                self._inspection.update(records)
                self._problems = problems
                self.build_seconds.update(seconds)
            return True

        self._building = engine
        future = executor.submit(run)
        self._pending = (version, future)
        return future

    def ready(self) -> bool:
        """Nothing is being built in the background for the session as it is now."""
        pending = self._pending
        return pending is None or pending[0] != self.version or pending[1].done()

    def _wait(self) -> None:
        """Let a background build of the session as it is now finish (what it makes
        is then here), rather than build the same again."""
        pending = self._pending
        if pending is not None and pending[0] == self.version:
            try:
                pending[1].result()
            except Exception:  # noqa: BLE001, S110 - building again says why
                pass

    def problems(self) -> list[str]:
        """Why the project does not build (empty when it does), cached until it changes."""
        self._wait()
        if self._problems is None:
            self._problems = self.session.problems()
        return list(self._problems)

    def scope(self, path: NodePath | None = None, component: str | None = None) -> dict[str, float]:
        """Resolved parameters (trial values included) plus ``process.*`` constants.

        With a ``path``, also the point coordinates (``node.point.x``) and sizes
        (``node.width``) the node there can use, in its own frame, e.g. to preview
        expressions.
        """
        component = component or self.session.active
        variables = self.session.engine.variables(component, self.session.trials_for(component))
        if path is None:
            return variables
        record = self.inspection(component)
        if path[:-1] and path[:-1] not in record:
            return variables
        into = frame_of(record, path).inverted() if path[:-1] else None
        points: dict[str, tuple[float, float]] = {}
        for name, _, x, y in self.align_targets(path, component):
            if into is not None:
                x, y = into.apply(x, y)
            variables[f"{name}.x"], variables[f"{name}.y"] = x, y
            points[name] = x, y
        for name in {n.rpartition(".")[0] for n in points}:  # node.width, node.height
            for size, (a, b) in {"width": ("left", "right"), "height": ("bottom", "top")}.items():
                if f"{name}.{a}" in points and f"{name}.{b}" in points:
                    variables[f"{name}.{size}"] = math.dist(
                        points[f"{name}.{a}"], points[f"{name}.{b}"]
                    )
        return variables

    def inspection(self, component: str | None = None) -> dict[NodePath, NodeRecord]:
        """Every node of a component as evaluated (cached until the next change)."""
        component = component or self.session.active
        if component not in self._inspection:
            self._wait()
        if component not in self._inspection:
            self._inspection[component] = self._build(component).records()
        return self._inspection[component]

    def geometry(self, component: str | None = None) -> Geometry:
        component = component or self.session.active
        build = self._build(component)
        started, cached = time.perf_counter(), build._engine.cached
        try:
            return build.geometry
        finally:
            if build._engine.cached != cached:  # it was built, not found built
                self.build_seconds[component] = time.perf_counter() - started

    def preview(self, path: NodePath, node: Shape) -> Geometry:
        """The active component as it would be with ``node`` at ``path``, without
        changing anything (e.g. while a value is dragged). Raises if it does not build."""
        component = self.session.active
        engine = self.session.engine.trial(self._trial(path, node))
        return engine.build(component, self.session.trials_for(component)).geometry

    def preview_outlined(
        self, path: NodePath, node: Shape
    ) -> tuple[Geometry, Geometry | None, list[Geometry]]:
        """:meth:`preview`, the outline of ``node`` as it would be (to keep it
        outlined as selected while one of its values is dragged) and the
        component's shapes as they would be (see :meth:`shapes`)."""
        component = self.session.active
        engine = self.session.engine.trial(self._trial(path, node))
        build = engine.build(component, self.session.trials_for(component))
        record = build.records()
        shapes = _top_level(record)
        if path not in record:
            return build.geometry, None, shapes
        outline = Geometry()
        outline.merge(record[path].geometry, frame_of(record, path))
        return build.geometry, None if outline.is_empty() else outline, shapes

    def shapes(self, component: str | None = None) -> list[Geometry]:
        """The geometry of each shown top-level node of a component, in its frame:
        drawn outlined one by one over the merged layers."""
        return _top_level(self.inspection(component))

    def inspect_with(self, path: NodePath, node: Shape) -> dict[NodePath, NodeRecord]:
        """Every node of the active component as it would be with ``node`` at ``path``."""
        component = self.session.active
        engine = self.session.engine.trial(self._trial(path, node))
        return engine.build(component, self.session.trials_for(component)).records()

    def build(self, component: str | None = None) -> Build:
        """The engine's build of a component (default: the active one) with its trial
        values: what outputs (exports, rule checks) are made from."""
        component = component or self.session.active
        self._wait()
        return self.session.engine.build(component, self.session.trials_for(component))

    _build = build

    def _trial(self, path: NodePath, node: Shape):
        project, component = self.session.project, self.session.active
        definition = project.components[component].model_copy(deep=True)
        container, index = container_of(definition.shapes, path)
        container[index] = node
        return dataclasses.replace(
            project, components={**project.components, component: definition}
        )

    def checked(self, component: str | None = None) -> Geometry:
        """What the rule checks look at: the component rounded onto the process's
        grid as an export would write it (requirement DRC-3)."""
        process = self.session.project.process
        return self.build(component).output(process.grid_um, process.chord_um).geometry

    def check(
        self, geometry: Geometry | None = None, component: str | None = None
    ) -> list[rules.Violation]:
        """The rule checks on ``geometry`` (default: :meth:`checked`)."""
        component = component or self.session.active
        geometry = self.checked(component) if geometry is None else geometry
        return rules.check(self.session.project, geometry, component)

    def node_regions(
        self, visible: dict[str, bool], component: str | None = None
    ) -> list[tuple[NodePath, Geometry]]:
        """The geometry of each top-level node of a component on the visible layers,
        for click selection."""
        result = []
        for path, record in sorted(self.inspection(component).items()):
            if len(path) != 1:
                continue
            shown = [n for n in record.geometry.layer_names() if visible.get(n, True)]
            result.append((path, record.geometry.only(shown)))
        return result

    def highlight(self, paths: list[NodePath], component: str | None = None) -> Geometry | None:
        """Outline geometry of the given nodes, placed as they appear in the component
        (what they place stays placed, not flattened)."""
        record = self.inspection(component)
        result = Geometry()
        for path in paths:
            if path not in record:
                continue
            result.merge(record[path].geometry, frame_of(record, path))
        return None if result.is_empty() else result

    def highlights(self, paths: list[NodePath], component: str | None = None) -> list[Geometry]:
        """:meth:`highlight` of each node on its own, so nodes that touch or overlap
        are still outlined one by one."""
        return [g for g in (self.highlight([p], component) for p in paths) if g is not None]

    def pieces(self, path: NodePath, component: str | None = None) -> int:
        """How many separate pieces a node's geometry has (all layers together), e.g.
        two when a cut goes right through it."""
        record = self.inspection(component)
        if path not in record:
            return 0
        return record[path].geometry.pieces()

    def node_box(
        self, path: NodePath, component: str | None = None
    ) -> list[tuple[float, float]] | None:
        """Corners of the box a node's points (``center``, ``left``, …) come from, in
        the component's frame (turned with the node's frame), or None without geometry."""
        record = self.inspection(component)
        if path not in record:
            return None
        box = record[path].geometry.bbox()
        if box is None:
            return None
        frame, (x0, y0, x1, y1) = frame_of(record, path), box
        return [frame.apply(x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]

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
            result.append((name, *frame.apply(x, y)))
        return result

    def align_targets(
        self, path: NodePath, component: str | None = None
    ) -> list[tuple[str, NodePath, float, float]]:
        """Points the node at ``path`` can align to: ``(node.point, node path, x, y)``.

        Coordinates are in the component's frame.
        """
        component = component or self.session.active
        shapes = self.session.definition_of(component).shapes
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

    def default_points(self, component: str | None = None) -> dict[str, tuple[float, float]]:
        """The points every component has (``center``, ``left``, …): of its whole
        drawn geometry. Empty when it draws nothing."""
        geometry = self.geometry(component=component)
        points = NodePoints(component or self.session.active, geometry, {})
        try:
            return {name: points.point(name) for name in BBOX_POINTS}
        except ValueError:  # nothing drawn
            return {}

    def shape_points(
        self, component: str | None = None
    ) -> dict[str, dict[str, tuple[float, float]]]:
        """The points of each named top-level shape (what a declared point can be
        measured from), by shape name, in the component's frame."""
        component = component or self.session.active
        shapes = self.session.definition_of(component).shapes
        result = {}
        for path in sorted(self.inspection(component)):
            name = node_at(shapes, path).name
            if len(path) == 1 and name:
                result[name] = {p: (x, y) for p, x, y in self.node_points(path, component)}
        return result

    def declared_points(self, component: str | None = None) -> dict[str, tuple[float, float]]:
        """Positions of a component's declared points (trial values included)."""
        component = component or self.session.active
        return self._build(component).points()

    def selection_center(self, paths: list[NodePath]) -> tuple[float, float] | None:
        """Centre of the bounding box of the given shapes, in the component's frame."""
        geometry = self.highlight(paths)
        if geometry is None:
            return None
        box = geometry.bbox()
        if box is None:
            return None
        x0, y0, x1, y1 = box
        return (x0 + x1) / 2, (y0 + y1) / 2

    def guides(
        self, component: str | None = None
    ) -> list[tuple[NodePath, str, tuple[float, float], tuple[float, float]]]:
        """The guide lines of a component: ``(path, name, start, end)`` in its frame."""
        component = component or self.session.active
        shapes = self.session.definition_of(component).shapes
        record = self.inspection(component)
        result = []
        for path in sorted(record):
            node = node_at(shapes, path)
            if node.category != "guide":
                continue
            frame, points = frame_of(record, path), record[path].points
            ends = [frame.apply(*points.point(end)) for end in ("start", "end")]
            result.append((path, node.name or node.kind, ends[0], ends[1]))
        return result

    def all_points(self, component: str | None = None) -> list[tuple[str, float, float]]:
        """Every shape's points (``shape.point``, x, y) in the component's frame, e.g. to
        snap to."""
        component = component or self.session.active
        shapes = self.session.definition_of(component).shapes
        result = []
        for path in sorted(self.inspection(component)):
            label = node_at(shapes, path).name or node_at(shapes, path).kind
            result += [(f"{label}.{p}", x, y) for p, x, y in self.node_points(path, component)]
        return result


def _top_level(record: dict[NodePath, NodeRecord]) -> list[Geometry]:
    return [r.geometry for p, r in sorted(record.items()) if len(p) == 1]
