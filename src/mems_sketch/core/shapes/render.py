"""Evaluating a shape tree into geometry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

import klayout.db as kdb

from mems_sketch.core.component import DBU_UM, Geometry, placement, resolve_params, to_dbu
from mems_sketch.core.expressions import evaluate
from mems_sketch.core.shapes.base import (
    ArcShape,
    BooleanShape,
    CircleShape,
    FilletShape,
    LayerMapShape,
    OffsetShape,
    PathShape,
    Point,
    PolygonShape,
    RectShape,
    RefShape,
    Repeat,
    Shape,
    TransformShape,
    Value,
)
from mems_sketch.core.shapes.geometry import (
    ARC_TOLERANCE_UM,
    annular_sector,
    apply_transform,
    arc_points,
    boolean_op,
    path_of,
    segments,
    to_ictrans,
)
from mems_sketch.core.shapes.points import NodePoints, own_strings, point_dependencies, point_values
from mems_sketch.core.shapes.tree import NodePath

if TYPE_CHECKING:
    from mems_sketch.core.component import Component


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
            inner = shift * _transform_of(shape, first)
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
        def ev(value: Value) -> float:
            return evaluate(value, v)

        def children(lists: list[list[Shape]], inner_scope=scope) -> list[Geometry]:
            return self._render_lists(lists, v, inner_scope, path)[0]

        geometry = Geometry()
        declared: dict[str, Point] = {}
        match shape:
            case RectShape():
                geometry.add_rect(
                    shape.layer, ev(shape.x0), ev(shape.y0), ev(shape.x1), ev(shape.y1)
                )
            case PolygonShape():
                geometry.add_polygon(shape.layer, [(ev(x), ev(y)) for x, y in shape.points])
            case CircleShape():
                r = ev(shape.radius)
                n = segments(r, shape.segments and ev(shape.segments))
                geometry.add_polygon(
                    shape.layer, arc_points(ev(shape.x), ev(shape.y), r, 0, 360, n)
                )
            case ArcShape():
                geometry.layers[shape.layer] = annular_sector(shape, ev)
            case PathShape():
                geometry.region(shape.layer).insert(path_of(shape, ev))
            case RefShape():
                child = self.lookup(shape.component)
                built, points = child.compile(resolve_params(child, shape.params, v))
                transform = _transform_of(shape, v)
                geometry.merge(built, to_ictrans(transform))
                declared = {name: apply_transform(transform, p) for name, p in points.items()}
            case TransformShape():
                if ev(shape.scale) <= 0:
                    raise ValueError("transform scale must be positive")
                transform = _transform_of(shape, v)
                inverse = transform.inverted()
                inner_scope = {k: p.seen_through(inverse) for k, p in scope.items()}
                (inner,) = children([shape.children], inner_scope)
                geometry.merge(inner, to_ictrans(transform))
            case BooleanShape():
                a, b = children([shape.a, shape.b])
                geometry = boolean_op(shape.op, a, b)
            case OffsetShape():
                mode = 2 if shape.corners == "square" else 1
                d = to_dbu(ev(shape.distance))
                for layer, region in children([shape.children])[0].layers.items():
                    geometry.layers[layer] = region.sized(d, mode)
            case FilletShape():
                r_out, r_in = ev(shape.radius), ev(shape.inner_radius)
                if r_out < 0 or r_in < 0:
                    raise ValueError("fillet radii must not be negative")
                n = segments(
                    max(r_out, r_in, ARC_TOLERANCE_UM), shape.segments and ev(shape.segments)
                )
                for layer, region in children([shape.children])[0].layers.items():
                    geometry.layers[layer] = region.merged().rounded_corners(
                        to_dbu(r_in), to_dbu(r_out), n
                    )
            case LayerMapShape():
                for layer, region in children([shape.children])[0].layers.items():
                    target = shape.mapping.get(layer, layer if shape.keep_unmapped else None)
                    if target is not None:
                        geometry.region(target).insert(region)
        return geometry, declared


def _transform_of(shape: Shape, v: dict[str, float]) -> kdb.DCplxTrans:
    """The placement a reference or transform applies to its content, in µm."""
    if isinstance(shape, RefShape | TransformShape):
        scale = evaluate(shape.scale, v) if isinstance(shape, TransformShape) else 1.0
        return kdb.DCplxTrans(
            scale,
            evaluate(shape.rotation, v),
            shape.mirror_x,
            evaluate(shape.x, v),
            evaluate(shape.y, v),
        )
    return kdb.DCplxTrans()


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
