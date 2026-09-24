"""What the components evaluate to: geometry, rule checks, node positions and points."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

import klayout.db as kdb

from mems_sketch.core.component import DBU_UM, Geometry
from mems_sketch.core.shapes import (
    BBOX_POINTS,
    NodePath,
    NodePoints,
    NodeRecord,
    Shape,
    container_of,
    frame_of,
    node_at,
    to_ictrans,
    visible_from,
)
from mems_sketch.process import rules

if TYPE_CHECKING:
    from mems_sketch.editing.session import EditSession


class Results:
    """What a session's components evaluate to, computed on demand and cached until
    the session changes. Of the active component unless another is named."""

    def __init__(self, session: EditSession) -> None:
        self.session = session
        self._inspection: dict[str, dict[NodePath, NodeRecord]] = {}
        session.changed.connect(self._inspection.clear)

    def scope(self, path: NodePath | None = None, component: str | None = None) -> dict[str, float]:
        """Resolved parameters (trial values included) plus ``process.*`` constants.

        With a ``path``, also the point coordinates (``node.point.x``) the node
        there can use, in its own frame, e.g. to preview expressions.
        """
        component = component or self.session.active
        variables = self.session.compiled().variables(component, self.session.trials_for(component))
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
        component = component or self.session.active
        if component not in self._inspection:
            self._inspection[component] = self.session.compiled().inspect(
                component, self.session.trials_for(component)
            )
        return self._inspection[component]

    def geometry(self, component: str | None = None) -> Geometry:
        component = component or self.session.active
        return self.session.compiled().render(component, self.session.trials_for(component))

    def preview(self, path: NodePath, node: Shape) -> Geometry:
        """The active component as it would be with ``node`` at ``path``, without
        changing anything (e.g. while a value is dragged). Raises if it does not build."""
        component = self.session.active
        return self.session.compiler.session(self._trial(path, node)).render(
            component, self.session.trials_for(component)
        )

    def inspect_with(self, path: NodePath, node: Shape) -> dict[NodePath, NodeRecord]:
        """Every node of the active component as it would be with ``node`` at ``path``."""
        component = self.session.active
        return self.session.compiler.session(self._trial(path, node)).inspect(
            component, self.session.trials_for(component)
        )

    def _trial(self, path: NodePath, node: Shape):
        project, component = self.session.project, self.session.active
        definition = project.components[component].model_copy(deep=True)
        container, index = container_of(definition.shapes, path)
        container[index] = node
        return dataclasses.replace(
            project, components={**project.components, component: definition}
        )

    def check(
        self, drawn: Geometry | None = None, component: str | None = None
    ) -> list[rules.Violation]:
        geometry = self.geometry(component=component) if drawn is None else drawn
        return rules.check(self.session.project, geometry)

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

    def pieces(self, path: NodePath, component: str | None = None) -> int:
        """How many separate pieces a node's geometry has (all layers together), e.g.
        two when a cut goes right through it."""
        record = self.inspection(component)
        if path not in record:
            return 0
        region = kdb.Region()
        for r in record[path].geometry.layers.values():
            region.insert(r)
        return region.merged().count()

    def node_box(
        self, path: NodePath, component: str | None = None
    ) -> list[tuple[float, float]] | None:
        """Corners of the box a node's points (``center``, ``left``, …) come from, in
        the component's frame (turned with the node's frame), or None without geometry."""
        record = self.inspection(component)
        if path not in record:
            return None
        box = kdb.Box()
        for region in record[path].geometry.layers.values():
            box += region.bbox()
        if box.empty():
            return None
        frame, b = frame_of(record, path), box.to_dtype(DBU_UM)
        corners = [(b.left, b.bottom), (b.right, b.bottom), (b.right, b.top), (b.left, b.top)]
        return [(q.x, q.y) for q in (frame * kdb.DPoint(x, y) for x, y in corners)]

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
        return self.session.compiled().points(component, self.session.trials_for(component))

    def selection_center(self, paths: list[NodePath]) -> tuple[float, float] | None:
        """Centre of the bounding box of the given shapes, in the component's frame."""
        geometry = self.highlight(paths)
        if geometry is None:
            return None
        box = kdb.Box()
        for region in geometry.layers.values():
            box += region.bbox()
        center = box.center()
        return center.x / 1000, center.y / 1000

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
            ends = []
            for end in ("start", "end"):
                p = frame * kdb.DPoint(*points.point(end))
                ends.append((p.x, p.y))
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
