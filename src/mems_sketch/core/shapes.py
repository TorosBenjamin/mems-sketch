"""The parametric shape tree.

Geometry is described as a tree of nodes that is re-evaluated whenever a
parameter changes; nothing is ever edited destructively. Leaves are primitives
(``rect``, ``polygon``, ``circle``, ``arc``, ``path``) and references to other
components (``ref``). Inner nodes are operations:

* ``transform``  its children moved/rotated/mirrored/scaled as one piece
* ``boolean``    ``a`` op ``b`` with op in union / subtract / intersect / xor
* ``offset``     grow (positive) or shrink (negative) by a distance
* ``fillet``     round convex and concave corners
* ``layer_map``  move geometry between layers (select, rename, derive layers)

Semantics:

* Every node yields geometry on one or more named layers. Booleans, offsets
  and fillets act **per layer**: ``subtract`` removes ``b``'s device-layer
  geometry from ``a``'s device-layer geometry, and so on. To combine
  different layers, bring them onto one layer with ``layer_map`` first.
* Every value may be an expression over the variables in scope: the
  component's parameters inside a component, the design's global variables at
  the top level.
* Any node can carry ``repeat`` to place copies on a grid; inside it the
  column and row indices are ``i`` and ``j`` (the innermost repeat wins).
* ``enabled=False`` skips a node, e.g. to try a variant in the GUI.
* **Alignment points.** Every node has bounding-box points (``center``,
  ``top``, ``bottom_left``, ...); a component reference also has the points its
  component declares. ``align`` moves a node so that one of its points lands
  on a point of another named node, and expressions can use point coordinates
  as ``<node>.<point>.x`` / ``.y``. A node sees the named nodes in its own
  list, in the other child lists of its parent (``a`` and ``b`` of a boolean)
  and in every enclosing list, always in its own coordinate frame. Nodes are
  evaluated in dependency order; a cycle is an error.
* Coordinates snap to the 1 nm database grid; curves are approximated with
  segments no further than :data:`ARC_TOLERANCE_UM` from the true arc.
"""

from __future__ import annotations

import functools
import math
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Literal

import klayout.db as kdb
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mems_sketch.core.component import DBU_UM, Geometry, placement, resolve_params, to_dbu
from mems_sketch.core.expressions import ExpressionError, evaluate, names_in, substitute

if TYPE_CHECKING:
    from mems_sketch.core.component import Component

Value = float | str  # a number or an expression
INDEX_NAMES = ("i", "j")
ARC_TOLERANCE_UM = 0.005  # max chord deviation for circles and arcs
MAX_ARC_SEGMENTS = 4096
# Points every node has, from the bounding box of its geometry.
BBOX_POINTS = (
    "center",
    "left",
    "right",
    "top",
    "bottom",
    "top_left",
    "top_right",
    "bottom_left",
    "bottom_right",
)
Point = tuple[float, float]


def check_point_reference(reference: str) -> str:
    """``reference`` must look like ``node.point``."""
    node, sep, point = reference.partition(".")
    if not sep or not node.isidentifier() or not point.isidentifier():
        raise ValueError(f"'{reference}' is not a point reference like 'mass.top'")
    return reference


class Repeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: Value = 1
    rows: Value = 1
    dx: Value = 0.0
    dy: Value = 0.0


class Align(BaseModel):
    """Move a node so that its ``point`` lands on ``to`` (``node.point``), plus ``dx``, ``dy``.

    The alignment is kept: it is re-evaluated whenever anything changes. The
    node's own position (``x``, ``y`` of a reference or transform) then only
    matters through rotation and mirroring.
    """

    model_config = ConfigDict(extra="forbid")

    point: str = "center"
    to: str
    dx: Value = 0.0
    dy: Value = 0.0

    @field_validator("point")
    @classmethod
    def _point_name(cls, point: str) -> str:
        if not point.isidentifier():
            raise ValueError(f"'{point}' is not a point name")
        return point

    @field_validator("to")
    @classmethod
    def _reference(cls, to: str) -> str:
        return check_point_reference(to)


class _Node(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None  # stable handle for the GUI and scripts
    align: Align | None = None
    enabled: bool = True
    repeat: Repeat | None = None


# -- primitives ------------------------------------------------------------


class RectShape(_Node):
    kind: Literal["rect"] = "rect"
    layer: str
    x0: Value
    y0: Value
    x1: Value
    y1: Value


class PolygonShape(_Node):
    kind: Literal["polygon"] = "polygon"
    layer: str
    points: list[tuple[Value, Value]] = Field(min_length=3)


class CircleShape(_Node):
    kind: Literal["circle"] = "circle"
    layer: str
    x: Value = 0.0
    y: Value = 0.0
    radius: Value
    segments: Value | None = None  # default: from ARC_TOLERANCE_UM


class ArcShape(_Node):
    """Annular sector (a ring when the angles span 360°). Angles in degrees, CCW from +x."""

    kind: Literal["arc"] = "arc"
    layer: str
    x: Value = 0.0
    y: Value = 0.0
    inner_radius: Value = 0.0
    outer_radius: Value
    start_angle: Value = 0.0
    end_angle: Value = 360.0
    segments: Value | None = None  # for a full circle; scaled by the swept angle


class PathShape(_Node):
    """A wire of constant ``width`` along a centreline, e.g. a beam or a trace."""

    kind: Literal["path"] = "path"
    layer: str
    points: list[tuple[Value, Value]] = Field(min_length=2)
    width: Value
    ends: Literal["flush", "square", "round"] = "flush"


class RefShape(_Node):
    """An instance of a built-in or user-defined component."""

    kind: Literal["ref"] = "ref"
    component: str
    params: dict[str, Value] = Field(default_factory=dict)
    x: Value = 0.0
    y: Value = 0.0
    rotation: Value = 0.0  # degrees, counter-clockwise
    mirror_x: bool = False


# -- operations ------------------------------------------------------------


class TransformShape(_Node):
    """Mirror ``children`` about x, scale, rotate and move them as one piece.

    For something reusable, make a component instead; a transform is for
    moving a few shapes together once. Files written before the rename used
    ``kind: group``, which is still read.
    """

    kind: Literal["transform", "group"] = "transform"
    children: list[Shape] = Field(default_factory=list)
    x: Value = 0.0
    y: Value = 0.0
    rotation: Value = 0.0
    mirror_x: bool = False
    scale: Value = 1.0

    @field_validator("kind")
    @classmethod
    def _current_kind(cls, kind: str) -> str:
        return "transform"


GroupShape = TransformShape  # the earlier name


class BooleanShape(_Node):
    kind: Literal["boolean"] = "boolean"
    op: Literal["union", "subtract", "intersect", "xor"]
    a: list[Shape]
    b: list[Shape]


class OffsetShape(_Node):
    """Grow (positive ``distance``) or shrink (negative) the children's outlines."""

    kind: Literal["offset"] = "offset"
    children: list[Shape]
    distance: Value
    corners: Literal["square", "bevel"] = "square"


class FilletShape(_Node):
    """Round corners: ``radius`` for convex corners, ``inner_radius`` for concave ones."""

    kind: Literal["fillet"] = "fillet"
    children: list[Shape]
    radius: Value = 0.0
    inner_radius: Value = 0.0
    segments: Value | None = None  # per full circle; default from ARC_TOLERANCE_UM


class LayerMapShape(_Node):
    """Move children's geometry between layers.

    ``mapping`` sends source layer -> target layer; several sources may merge
    into one target. Layers not in ``mapping`` are dropped unless
    ``keep_unmapped`` is set.
    """

    kind: Literal["layer_map"] = "layer_map"
    children: list[Shape]
    mapping: dict[str, str]
    keep_unmapped: bool = False

    @model_validator(mode="after")
    def _not_empty(self):
        if not self.mapping:
            raise ValueError("layer_map needs at least one mapping")
        return self


Shape = Annotated[
    RectShape
    | PolygonShape
    | CircleShape
    | ArcShape
    | PathShape
    | RefShape
    | TransformShape
    | BooleanShape
    | OffsetShape
    | FilletShape
    | LayerMapShape,
    Field(discriminator="kind"),
]

for _model in (TransformShape, BooleanShape, OffsetShape, FilletShape, LayerMapShape):
    _model.model_rebuild()

PRIMITIVE_KINDS = ("rect", "polygon", "circle", "arc", "path")


# -- tree helpers ----------------------------------------------------------


def child_lists(shape: Shape) -> list[list[Shape]]:
    match shape:
        case BooleanShape():
            return [shape.a, shape.b]
        case TransformShape() | OffsetShape() | FilletShape() | LayerMapShape():
            return [shape.children]
    return []


def walk(shapes: list[Shape]) -> Iterator[Shape]:
    """Every node in the tree, depth first."""
    for shape in shapes:
        yield shape
        for children in child_lists(shape):
            yield from walk(children)


def references(shapes: list[Shape]) -> set[str]:
    return {s.component for s in walk(shapes) if isinstance(s, RefShape)}


# A node's address in a tree: one (slot, index) step per level. The slot picks
# one of the parent's child lists (see child_lists); the top level is slot 0.
NodePath = tuple[tuple[int, int], ...]


def container_of(shapes: list[Shape], path: NodePath) -> tuple[list[Shape], int]:
    """The list that holds the node at ``path``, and its index in that list."""
    if not path or path[0][0] != 0:
        raise KeyError(f"invalid node path {path}")
    container = shapes
    for depth, (slot, index) in enumerate(path):
        if depth:
            parent = container[path[depth - 1][1]]
            lists = child_lists(parent)
            if not 0 <= slot < len(lists):
                raise KeyError(f"invalid node path {path}")
            container = lists[slot]
        if not 0 <= index < len(container):
            raise KeyError(f"invalid node path {path}")
    return container, path[-1][1]


def node_at(shapes: list[Shape], path: NodePath) -> Shape:
    container, index = container_of(shapes, path)
    return container[index]


def paths(
    shapes: list[Shape], prefix: NodePath = (), slot: int = 0
) -> Iterator[tuple[NodePath, Shape]]:
    """Every node with its path, depth first."""
    for index, shape in enumerate(shapes):
        path = (*prefix, (slot, index))
        yield path, shape
        for child_slot, children in enumerate(child_lists(shape)):
            yield from paths(children, path, child_slot)


def placement_of(
    shapes: list[Shape], path: NodePath, variables: dict[str, float]
) -> kdb.ICplxTrans:
    """Combined transform of the transforms enclosing ``path`` (repeats and alignment
    are not included; see :class:`NodeRecord` for the placement as evaluated)."""
    transform = kdb.ICplxTrans()
    for depth in range(1, len(path)):
        ancestor = node_at(shapes, path[:depth])
        if isinstance(ancestor, TransformShape):
            v = {**variables, "i": 0.0, "j": 0.0}
            transform = transform * kdb.ICplxTrans(
                evaluate(ancestor.scale, v),
                evaluate(ancestor.rotation, v),
                ancestor.mirror_x,
                to_dbu(evaluate(ancestor.x, v)),
                to_dbu(evaluate(ancestor.y, v)),
            )
    return transform


def find(shapes: list[Shape], name: str) -> Shape:
    for shape in walk(shapes):
        if shape.name == name:
            return shape
    raise KeyError(f"no shape named '{name}'")


# -- alignment points ------------------------------------------------------


class NodePoints:
    """The alignment points of one evaluated node, in the frame of the list holding it.

    ``declared`` are the points of a component reference (already placed);
    bounding-box points come from the geometry and are computed on demand.
    ``transform`` maps them into another frame (e.g. inside a transform node).
    """

    def __init__(
        self,
        name: str,
        geometry: Geometry,
        declared: Mapping[str, Point],
        transform: kdb.DCplxTrans | None = None,
    ) -> None:
        self.name = name
        self.geometry = geometry
        self.declared = dict(declared)
        self.transform = transform

    def names(self) -> list[str]:
        return [*self.declared, *BBOX_POINTS]

    def point(self, point: str) -> Point:
        if point in self.declared:
            x, y = self.declared[point]
        elif point in BBOX_POINTS:
            x, y = _bbox_point(self.geometry, point, self.name)
        else:
            raise ValueError(f"shape '{self.name}' has no point '{point}'")
        if self.transform is None:
            return x, y
        p = self.transform * kdb.DPoint(x, y)
        return p.x, p.y

    def seen_through(self, transform: kdb.DCplxTrans) -> NodePoints:
        """The same points, mapped by ``transform`` (applied after any existing one)."""
        combined = transform if self.transform is None else transform * self.transform
        return NodePoints(self.name, self.geometry, self.declared, combined)


def _bbox_point(geometry: Geometry, point: str, name: str) -> Point:
    box = kdb.Box()
    for region in geometry.layers.values():
        box += region.bbox()
    if box.empty():
        raise ValueError(f"shape '{name}' has no geometry to align to")
    x0, y0, x1, y1 = (v * DBU_UM for v in (box.left, box.bottom, box.right, box.top))
    xs = {"left": x0, "right": x1}
    ys = {"bottom": y0, "top": y1}
    vertical, _, horizontal = point.partition("_")
    if point == "center":
        return (x0 + x1) / 2, (y0 + y1) / 2
    if point in xs:
        return xs[point], (y0 + y1) / 2
    if point in ys:
        return (x0 + x1) / 2, ys[point]
    return xs[horizontal], ys[vertical]


@functools.lru_cache(maxsize=65536)
def point_names(expression: str) -> frozenset[str]:
    """Point coordinates (``node.point.x`` / ``.y``) used by an expression."""
    if expression.count(".") < 2:
        return frozenset()
    try:
        names = names_in(expression)
    except ExpressionError:
        return frozenset()
    return frozenset(n for n in names if n.count(".") == 2 and n.rsplit(".", 1)[1] in "xy")


def point_values(expressions: list[str], scope: Mapping[str, NodePoints]) -> dict[str, float]:
    """Values of the point coordinates used by ``expressions`` that ``scope`` provides."""
    values = {}
    for expression in expressions:
        for name in point_names(expression):
            node, point, axis = name.split(".")
            if node in scope:
                values[name] = scope[node].point(point)[0 if axis == "x" else 1]
    return values


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, BaseModel):
        for field in type(value).model_fields:
            yield from _strings(getattr(value, field))
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def own_strings(shape: Shape) -> list[str]:
    """Every string in a node's own fields (its children excluded)."""
    return [
        text
        for field in type(shape).model_fields
        if field not in _CHILD_FIELDS
        for text in _strings(getattr(shape, field))
    ]


_CHILD_FIELDS = frozenset({"children", "a", "b"})


def point_dependencies(shape: Shape) -> set[str]:
    """Names of the nodes whose points ``shape`` or its subtree uses."""
    found = set()
    for node in walk([shape]):
        if not node.enabled:
            continue
        if node.align is not None:
            found.add(node.align.to.partition(".")[0])
        for text in own_strings(node):
            found.update(name.partition(".")[0] for name in point_names(text))
    return found


# -- evaluation ------------------------------------------------------------


@dataclass
class NodeRecord:
    """How one node came out of an evaluation (for the GUI: selection, point markers).

    ``geometry`` and ``points`` are in the frame of the list holding the node;
    ``inner`` maps the node's children's frame into that frame.
    """

    geometry: Geometry
    points: NodePoints
    inner: kdb.DCplxTrans


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
                label, geometry, {k: _apply(shift, p) for k, p in points.declared.items()}
            )
        if self.record is not None and path not in self.record:
            inner = shift * _transform_of(shape, first)
            self.record[path] = NodeRecord(geometry, points, inner)
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
                n = _segments(r, shape.segments and ev(shape.segments))
                geometry.add_polygon(
                    shape.layer, _arc_points(ev(shape.x), ev(shape.y), r, 0, 360, n)
                )
            case ArcShape():
                geometry.layers[shape.layer] = _annular_sector(shape, ev)
            case PathShape():
                geometry.region(shape.layer).insert(_path(shape, ev))
            case RefShape():
                child = self.lookup(shape.component)
                built, points = child.compile(resolve_params(child, shape.params, v))
                transform = _transform_of(shape, v)
                geometry.merge(built, to_ictrans(transform))
                declared = {name: _apply(transform, p) for name, p in points.items()}
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
                geometry = _boolean(shape.op, a, b)
            case OffsetShape():
                mode = 2 if shape.corners == "square" else 1
                d = to_dbu(ev(shape.distance))
                for layer, region in children([shape.children])[0].layers.items():
                    geometry.layers[layer] = region.sized(d, mode)
            case FilletShape():
                r_out, r_in = ev(shape.radius), ev(shape.inner_radius)
                if r_out < 0 or r_in < 0:
                    raise ValueError("fillet radii must not be negative")
                n = _segments(
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


def to_ictrans(transform: kdb.DCplxTrans) -> kdb.ICplxTrans:
    return kdb.ICplxTrans(
        transform.mag,
        transform.angle,
        transform.is_mirror(),
        to_dbu(transform.disp.x),
        to_dbu(transform.disp.y),
    )


def _apply(transform: kdb.DCplxTrans, point: Point) -> Point:
    p = transform * kdb.DPoint(*point)
    return p.x, p.y


# -- renaming ----------------------------------------------------------------

# Node fields that hold names or choices rather than expressions.
_NOT_EXPRESSIONS = frozenset(
    {"kind", "name", "layer", "component", "op", "ends", "corners", "mapping", "point", "to"}
)


def map_expressions(shapes: list[Shape], change: Callable[[str], str | None]) -> list[Shape]:
    """Copies of ``shapes`` with every name in every expression passed through ``change``.

    ``change`` gets a (dotted) name and returns an expression to put in its
    place, or None to keep it. Point references in ``align.to`` are passed as
    ``node.point`` and must map to another point reference.
    """

    def visit(value: Any, key: str | None = None) -> Any:
        if isinstance(value, dict):
            if key == "align" and value is not None:
                value = {**value, "to": change(value["to"]) or value["to"]}
            if key == "mapping":
                return value
            return {k: visit(v, None if key == "params" else k) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [visit(v) for v in value]
        if isinstance(value, str) and key not in _NOT_EXPRESSIONS:
            return rewrite(value, change)
        return value

    data = [visit(shape.model_dump(), None) for shape in shapes]
    return [_SHAPE_ADAPTER.validate_python(item) for item in data]


def rewrite(expression: Value, change: Callable[[str], str | None]) -> Value:
    """``expression`` with names replaced by ``change``; a lone number stays a number."""
    if not isinstance(expression, str):
        return expression
    try:
        result = substitute(expression, change)
    except ExpressionError:
        return expression
    try:
        return float(result)
    except ValueError:
        return result


def rename_node_references(shapes: list[Shape], old: str, new: str) -> list[Shape]:
    """Copies of ``shapes`` where references to the points of node ``old`` use ``new``."""

    def change(name: str) -> str | None:
        head, dot, rest = name.partition(".")
        if head == old and dot and (rest.count(".") == 1 or "." not in rest):
            return f"{new}.{rest}"
        return None

    return map_expressions(shapes, change)


def _boolean(op: str, a: Geometry, b: Geometry) -> Geometry:
    result = Geometry()
    for layer in a.layers.keys() | b.layers.keys():
        ra = a.layers.get(layer, kdb.Region())
        rb = b.layers.get(layer, kdb.Region())
        match op:
            case "union":
                out = ra | rb
            case "subtract":
                out = ra - rb
            case "intersect":
                out = ra & rb
            case "xor":
                out = ra ^ rb
        if not out.is_empty():
            result.layers[layer] = out
    return result


def _grid(repeat: Repeat, variables: dict[str, float]):
    columns = evaluate(repeat.columns, variables)
    rows = evaluate(repeat.rows, variables)
    if columns != int(columns) or rows != int(rows) or columns < 0 or rows < 0:
        raise ValueError("repeat columns and rows must be non-negative integers")
    pitch_x, pitch_y = evaluate(repeat.dx, variables), evaluate(repeat.dy, variables)
    for j in range(int(rows)):
        for i in range(int(columns)):
            yield (i * pitch_x, j * pitch_y), {**variables, "i": float(i), "j": float(j)}


def _segments(radius: float, explicit: float | None) -> int:
    """Segments per full circle so the chord error stays below ARC_TOLERANCE_UM."""
    if explicit:
        n = int(explicit)
    elif radius <= ARC_TOLERANCE_UM:
        n = 8
    else:
        n = math.ceil(math.pi / math.acos(1 - ARC_TOLERANCE_UM / radius))
    return max(8, min(n, MAX_ARC_SEGMENTS))


def _arc_points(cx, cy, r, start, end, n_full) -> list[tuple[float, float]]:
    sweep = end - start
    n = max(1, math.ceil(n_full * abs(sweep) / 360))
    full = abs(sweep) >= 360
    count = n if full else n + 1  # a full circle must not repeat its first point
    return [
        (
            cx + r * math.cos(math.radians(start + sweep * k / n)),
            cy + r * math.sin(math.radians(start + sweep * k / n)),
        )
        for k in range(count)
    ]


def _annular_sector(shape: ArcShape, ev) -> kdb.Region:
    """Outer pie minus inner pie; with full angles this is a ring or disc."""
    cx, cy = ev(shape.x), ev(shape.y)
    r_in, r_out = ev(shape.inner_radius), ev(shape.outer_radius)
    start, end = ev(shape.start_angle), ev(shape.end_angle)
    if not 0 <= r_in < r_out:
        raise ValueError("arc needs 0 <= inner_radius < outer_radius")
    if end <= start:
        raise ValueError("arc end_angle must be greater than start_angle")
    n = _segments(r_out, shape.segments and ev(shape.segments))

    def pie(r: float) -> kdb.Region:
        points = _arc_points(cx, cy, r, start, end, n)
        if end - start < 360:
            points.append((cx, cy))
        return kdb.Region(kdb.Polygon([kdb.Point(to_dbu(x), to_dbu(y)) for x, y in points]))

    return pie(r_out) - pie(r_in) if r_in > 0 else pie(r_out)


def _path(shape: PathShape, ev) -> kdb.Path:
    points = [kdb.Point(to_dbu(ev(x)), to_dbu(ev(y))) for x, y in shape.points]
    width = to_dbu(ev(shape.width))
    if width <= 0:
        raise ValueError("path width must be positive")
    ext = 0 if shape.ends == "flush" else width // 2
    return kdb.Path(points, width, ext, ext, shape.ends == "round")


from pydantic import TypeAdapter

_SHAPE_ADAPTER: TypeAdapter = TypeAdapter(Shape)


def frame_of(record: Mapping[NodePath, NodeRecord], path: NodePath) -> kdb.DCplxTrans:
    """Maps the frame of the list holding ``path`` into the component's frame."""
    transform = kdb.DCplxTrans()
    for depth in range(1, len(path)):
        transform = transform * record[path[:depth]].inner
    return transform


def visible_from(path: NodePath, other: NodePath) -> bool:
    """Whether the node at ``path`` can use the points of the node at ``other``.

    ``other`` must sit in a list of ``path``'s parent or of an ancestor, and
    must not be ``path`` itself or one of its ancestors.
    """
    parent, other_parent = path[:-1], other[:-1]
    if other_parent != parent[: len(other_parent)]:
        return False
    return other != path[: len(other)]
