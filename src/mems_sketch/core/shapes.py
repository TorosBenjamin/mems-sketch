"""The parametric shape tree.

Geometry is described as a tree of nodes that is re-evaluated whenever a
parameter changes; nothing is ever edited destructively. Leaves are primitives
(``rect``, ``polygon``, ``circle``, ``arc``, ``path``) and references to other
components (``ref``). Inner nodes are operations:

* ``group``      union of its children, optionally moved/rotated/mirrored/scaled
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
* Coordinates snap to the 1 nm database grid; curves are approximated with
  segments no further than :data:`ARC_TOLERANCE_UM` from the true arc.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Annotated, Literal

import klayout.db as kdb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from mems_sketch.core.component import Geometry, placement, resolve_params, to_dbu
from mems_sketch.core.expressions import evaluate

if TYPE_CHECKING:
    from mems_sketch.core.component import Component

Value = float | str  # a number or an expression
INDEX_NAMES = ("i", "j")
ARC_TOLERANCE_UM = 0.005  # max chord deviation for circles and arcs
MAX_ARC_SEGMENTS = 4096


class Repeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: Value = 1
    rows: Value = 1
    dx: Value = 0.0
    dy: Value = 0.0


class _Node(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None  # stable handle for the GUI and scripts
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


class GroupShape(_Node):
    """Union of ``children``, then mirrored about x, scaled, rotated and moved."""

    kind: Literal["group"] = "group"
    children: list[Shape] = Field(default_factory=list)
    x: Value = 0.0
    y: Value = 0.0
    rotation: Value = 0.0
    mirror_x: bool = False
    scale: Value = 1.0


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
    | GroupShape
    | BooleanShape
    | OffsetShape
    | FilletShape
    | LayerMapShape,
    Field(discriminator="kind"),
]

for _model in (GroupShape, BooleanShape, OffsetShape, FilletShape, LayerMapShape):
    _model.model_rebuild()

PRIMITIVE_KINDS = ("rect", "polygon", "circle", "arc", "path")


# -- tree helpers ----------------------------------------------------------


def child_lists(shape: Shape) -> list[list[Shape]]:
    match shape:
        case BooleanShape():
            return [shape.a, shape.b]
        case GroupShape() | OffsetShape() | FilletShape() | LayerMapShape():
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


def find(shapes: list[Shape], name: str) -> Shape:
    for shape in walk(shapes):
        if shape.name == name:
            return shape
    raise KeyError(f"no shape named '{name}'")


# -- evaluation ------------------------------------------------------------


class Evaluator:
    """Turns a shape tree into :class:`Geometry`.

    ``lookup`` resolves component names used by ``ref`` nodes.
    """

    def __init__(self, lookup: Callable[[str], Component]) -> None:
        self.lookup = lookup

    def render(self, shapes: list[Shape], variables: dict[str, float]) -> Geometry:
        geometry = Geometry()
        for shape in shapes:
            if shape.enabled:
                geometry.merge(self.render_shape(shape, variables))
        return geometry

    def render_shape(self, shape: Shape, variables: dict[str, float]) -> Geometry:
        if shape.repeat is None:
            return self._render_once(shape, {**variables, "i": 0.0, "j": 0.0})
        result = Geometry()
        for (dx, dy), scope in _grid(shape.repeat, variables):
            copy = self._render_once(shape, scope)
            result.merge(copy, placement(dx, dy, 0.0, False))
        return result

    def _render_once(self, shape: Shape, v: dict[str, float]) -> Geometry:
        def ev(value: Value) -> float:
            return evaluate(value, v)

        geometry = Geometry()
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
                built = child.build(resolve_params(child, shape.params, v))
                transform = placement(ev(shape.x), ev(shape.y), ev(shape.rotation), shape.mirror_x)
                geometry.merge(built, transform)
            case GroupShape():
                scale = ev(shape.scale)
                if scale <= 0:
                    raise ValueError("group scale must be positive")
                transform = kdb.ICplxTrans(
                    scale,
                    ev(shape.rotation),
                    shape.mirror_x,
                    to_dbu(ev(shape.x)),
                    to_dbu(ev(shape.y)),
                )
                geometry.merge(self.render(shape.children, v), transform)
            case BooleanShape():
                geometry = _boolean(shape.op, self.render(shape.a, v), self.render(shape.b, v))
            case OffsetShape():
                mode = 2 if shape.corners == "square" else 1
                d = to_dbu(ev(shape.distance))
                for layer, region in self.render(shape.children, v).layers.items():
                    geometry.layers[layer] = region.sized(d, mode)
            case FilletShape():
                r_out, r_in = ev(shape.radius), ev(shape.inner_radius)
                if r_out < 0 or r_in < 0:
                    raise ValueError("fillet radii must not be negative")
                n = _segments(
                    max(r_out, r_in, ARC_TOLERANCE_UM), shape.segments and ev(shape.segments)
                )
                for layer, region in self.render(shape.children, v).layers.items():
                    geometry.layers[layer] = region.merged().rounded_corners(
                        to_dbu(r_in), to_dbu(r_out), n
                    )
            case LayerMapShape():
                for layer, region in self.render(shape.children, v).layers.items():
                    target = shape.mapping.get(layer, layer if shape.keep_unmapped else None)
                    if target is not None:
                        geometry.region(target).insert(region)
        return geometry


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
