"""Evaluating a shape tree into geometry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

import klayout.db as kdb

from mems_sketch.core.component import DBU_UM, Geometry, placement, to_dbu
from mems_sketch.core.expressions import evaluate
from mems_sketch.core.shapes.base import Point, RenderContext, Repeat
from mems_sketch.core.shapes.geometry import apply_transform, to_ictrans
from mems_sketch.core.shapes.points import NodePoints, own_strings, point_dependencies, point_values
from mems_sketch.core.shapes.tree import NodePath

if TYPE_CHECKING:
    from mems_sketch.core.component import Component
    from mems_sketch.core.shapes.registry import Shape


@dataclass
class NodeRecord:
    """How one node came out of an evaluation (for the GUI: selection, point markers).

    ``geometry`` and ``points`` are in the frame of the list holding the node;
    ``inner`` maps the node's children's frame into that frame, and ``shift``
    is the move its alignment applied (identity when it is not aligned).
    """

    geometry: Geometry
    points: NodePoints
    inner: kdb.DCplxTrans
    shift: kdb.DCplxTrans


class Evaluator:
    """Turns a shape tree into :class:`Geometry`.

    ``lookup`` resolves component names used by ``ref`` nodes. When ``record``
    is a dict, every evaluated node is recorded in it by path (the first copy
    of repeated nodes).
    """

    def __init__(
        self,
        lookup: Callable[[str], Component],
        record: dict[NodePath, NodeRecord] | None = None,
    ) -> None:
        self.lookup = lookup
        self.record = record
        self._dependencies: dict[int, set[str]] = {}

    def render(
        self,
        shapes: list[Shape],
        variables: dict[str, float],
        scope: Mapping[str, NodePoints] | None = None,
    ) -> Geometry:
        return self.render_scoped(shapes, variables, scope)[0]

    def render_scoped(
        self,
        shapes: list[Shape],
        variables: dict[str, float],
        scope: Mapping[str, NodePoints] | None = None,
    ) -> tuple[Geometry, dict[str, NodePoints]]:
        """Geometry of a list of nodes, and the points of its named nodes."""
        (geometry,), local = self._render_lists([shapes], variables, scope or {}, ())
        return geometry, local

    def render_shape(self, shape: Shape, variables: dict[str, float]) -> Geometry:
        return self.render([shape], variables)

    def _render_lists(
        self,
        lists: list[list[Shape]],
        variables: dict[str, float],
        scope: Mapping[str, NodePoints],
        prefix: NodePath,
    ) -> tuple[list[Geometry], dict[str, NodePoints]]:
        """Evaluate sibling lists together, in the order their alignments require."""
        entries = [
            ((slot, index), shape)
            for slot, shapes in enumerate(lists)
            for index, shape in enumerate(shapes)
            if shape.enabled
        ]
        named = {shape.name: entry for entry in entries if (shape := entry[1]).name}
        results: dict[tuple[int, int], Geometry] = {}
        local: dict[str, NodePoints] = {}
        visiting: set[tuple[int, int]] = set()

        def visit(step: tuple[int, int], shape: Shape) -> None:
            if step in results:
                return
            if step in visiting:
                raise ValueError(f"circular alignment involving '{shape.name or shape.kind}'")
            visiting.add(step)
            for dependency in sorted(self._depends_on(shape)):
                if dependency in named:
                    visit(*named[dependency])
            visible = {**scope, **local}
            geometry, points = self._render_node(shape, variables, visible, (*prefix, step))
            visiting.discard(step)
            results[step] = geometry
            if shape.name:
                local[shape.name] = points

        for step, shape in entries:
            visit(step, shape)
        out = [Geometry() for _ in lists]
        for step, _ in entries:
            out[step[0]].merge(results[step])
        return out, local

    def _depends_on(self, shape: Shape) -> set[str]:
        key = id(shape)
        if key not in self._dependencies:
            self._dependencies[key] = point_dependencies(shape)
        return self._dependencies[key]

    def _render_node(
        self,
        shape: Shape,
        variables: dict[str, float],
        scope: Mapping[str, NodePoints],
        path: NodePath,
    ) -> tuple[Geometry, NodePoints]:
        v = {**variables, **point_values(own_strings(shape), scope)}
        first = {**v, "i": 0.0, "j": 0.0}
        if shape.repeat is None:
            geometry, declared = self._render_once(shape, first, scope, path)
        else:
            geometry, declared = Geometry(), None
            for (dx, dy), copy_scope in _grid(shape.repeat, v):
                copy, points = self._render_once(shape, copy_scope, scope, path)
                geometry.merge(copy, placement(dx, dy, 0.0, False))
                if declared is None:
                    declared = points  # the first copy sits at the node's own origin
        label = shape.name or shape.kind
        points = NodePoints(label, geometry, declared or {})
        shift = kdb.DCplxTrans()
        if shape.align is not None:
            shift = self._alignment(shape, points, first, scope)
            moved = Geometry()
            moved.merge(geometry, to_ictrans(shift))
            geometry = moved
            points = NodePoints(
                label, geometry, {k: apply_transform(shift, p) for k, p in points.declared.items()}
            )
        if self.record is not None and path not in self.record:
            inner = shift * transform_of(shape, first)
            self.record[path] = NodeRecord(geometry, points, inner, shift)
        return geometry, points

    def _alignment(
        self,
        shape: Shape,
        own: NodePoints,
        variables: dict[str, float],
        scope: Mapping[str, NodePoints],
    ) -> kdb.DCplxTrans:
        align = shape.align
        node, _, point = align.to.partition(".")
        if node not in scope:
            raise ValueError(
                f"'{shape.name or shape.kind}' is aligned to '{align.to}', "
                f"but no shape named '{node}' is visible from it"
            )
        target_x, target_y = scope[node].point(point)
        x, y = own.point(align.point)
        dx, dy = evaluate(align.dx, variables), evaluate(align.dy, variables)
        # Snap the move to the database grid so geometry and points agree.
        move_x = to_dbu(target_x + dx - x) * DBU_UM
        move_y = to_dbu(target_y + dy - y) * DBU_UM
        return kdb.DCplxTrans(move_x, move_y)

    def _render_once(
        self,
        shape: Shape,
        v: dict[str, float],
        scope: Mapping[str, NodePoints],
        path: NodePath,
    ) -> tuple[Geometry, dict[str, Point]]:
        def render_lists(lists, inner_scope) -> list[Geometry]:
            return self._render_lists(lists, v, inner_scope, path)[0]

        return shape.render(RenderContext(v, scope, self.lookup, render_lists))


def transform_of(shape: Shape, v: dict[str, float]) -> kdb.DCplxTrans:
    """The placement a node applies to its content, in µm (identity if it has none)."""
    transform = shape.placement(v)
    return kdb.DCplxTrans() if transform is None else transform


def _grid(repeat: Repeat, variables: dict[str, float]):
    columns = evaluate(repeat.columns, variables)
    rows = evaluate(repeat.rows, variables)
    if columns != int(columns) or rows != int(rows) or columns < 0 or rows < 0:
        raise ValueError("repeat columns and rows must be non-negative integers")
    pitch_x, pitch_y = evaluate(repeat.dx, variables), evaluate(repeat.dy, variables)
    for j in range(int(rows)):
        for i in range(int(columns)):
            yield (i * pitch_x, j * pitch_y), {**variables, "i": float(i), "j": float(j)}


def frame_of(record: Mapping[NodePath, NodeRecord], path: NodePath) -> kdb.DCplxTrans:
    """Maps the frame of the list holding ``path`` into the component's frame."""
    transform = kdb.DCplxTrans()
    for depth in range(1, len(path)):
        transform = transform * record[path[:depth]].inner
    return transform
