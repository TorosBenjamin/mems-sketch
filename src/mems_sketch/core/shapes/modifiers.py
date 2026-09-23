"""Modifiers: a stack of changes applied to a node's geometry, as in Blender.

Any node can carry ``modifiers``, evaluated top to bottom: each one takes what
the node (with the modifiers before it) produces and returns new geometry.

* ``array``: copies on a grid, ``columns`` × ``rows`` with steps ``dx``, ``dy``
* ``polar_array``: ``count`` copies around a centre ``x``, ``y``, ``step``
  degrees apart (a full circle by default), rotated with it or not
* ``mirror``: the node plus its mirror image across the vertical line at
  ``x`` (``axis: x``), the horizontal line at ``y`` (``axis: y``) or both

Every value may be an expression, including point coordinates such as
``mass.center.x``. Modifiers work in the frame of the list holding the node,
before its alignment moves the result. So a mirror about ``mass.center.x``
stays where it is when the part is moved, and the two halves move
symmetrically.

Inside an array the copy's column and row are ``i`` and ``j`` (for a polar
array the copy's index is ``i``), so each copy can differ, e.g. finger lengths
growing with ``i``: the node is evaluated again for every copy. When arrays
are stacked, the one nearest the node (first in the list) sets ``i`` and ``j``.
The points a component declares are those of the first copy, the original.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, ClassVar, Literal

import klayout.db as kdb
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from mems_sketch.core.component import Geometry
from mems_sketch.core.expressions import evaluate

if TYPE_CHECKING:
    from mems_sketch.core.shapes.base import Point, Value
    from mems_sketch.core.shapes.registry import Shape
else:
    Value = float | str

# What a node makes for given variables: its geometry and declared points.
Produce = Callable[[dict[str, float]], "tuple[Geometry, dict[str, Point]]"]


class Modifier(BaseModel):
    """What every modifier has; a kind overrides :meth:`apply`."""

    model_config = ConfigDict(extra="forbid")

    icon: ClassVar[str] = "repeat"
    enabled: bool = True

    def apply(
        self, produce: Produce, variables: dict[str, float]
    ) -> tuple[Geometry, dict[str, Point]]:
        raise NotImplementedError

    def copies(self, variables: dict[str, float] | None = None) -> int:
        """How many copies of its input it makes (1 when that depends on expressions
        that cannot be evaluated with ``variables``)."""
        return 1

    def baked(
        self,
        node: Shape,
        variables: dict[str, float],
        center: Callable[[Shape], tuple[float, float]],
    ) -> list[Shape]:
        """The shapes this modifier makes of ``node`` (without modifiers), placed in
        the node's frame: what Apply turns it into. ``center`` gives a shape's
        centre, for copies that move without turning."""
        raise NotImplementedError

    def summary(self) -> str:
        return self.kind

    @classmethod
    def kind_name(cls) -> str:
        return cls.model_fields["kind"].default


def _count(value: Value, variables: dict[str, float], what: str) -> int:
    number = evaluate(value, variables)
    if number != int(number) or number < 0:
        raise ValueError(f"{what} must be a non-negative integer, got {number:g}")
    return int(number)


def _try_count(value: Value, variables: dict[str, float] | None) -> int | None:
    try:
        return int(evaluate(value, variables or {}))
    except Exception:  # noqa: BLE001 - an expression that needs more context
        return None


def _format(value: Value) -> str:
    return f"{value:g}" if isinstance(value, float | int) else str(value)


def _placed(geometry: Geometry, transform: kdb.DCplxTrans) -> Geometry:
    from mems_sketch.core.shapes.geometry import to_ictrans

    result = Geometry()
    result.merge(geometry, to_ictrans(transform))
    return result


class ArrayModifier(Modifier):
    """Copies on a grid; the copy's column and row are ``i`` and ``j``."""

    kind: Literal["array"] = "array"
    icon: ClassVar[str] = "repeat"
    columns: Value = 1.0
    rows: Value = 1.0
    dx: Value = 0.0
    dy: Value = 0.0

    def apply(self, produce, variables):
        columns = _count(self.columns, variables, "array columns")
        rows = _count(self.rows, variables, "array rows")
        dx, dy = evaluate(self.dx, variables), evaluate(self.dy, variables)
        geometry, declared = Geometry(), None
        for j in range(rows):
            for i in range(columns):
                copy, points = produce({**variables, "i": float(i), "j": float(j)})
                geometry.merge(_placed(copy, kdb.DCplxTrans(i * dx, j * dy)))
                if declared is None:
                    declared = points  # the first copy sits at the node's own place
        return geometry, declared or {}

    def copies(self, variables=None):
        columns, rows = _try_count(self.columns, variables), _try_count(self.rows, variables)
        return 1 if columns is None or rows is None else columns * rows

    def summary(self):
        return f"array {_format(self.columns)}×{_format(self.rows)}"

    def baked(self, node, variables, center):
        from mems_sketch.core.shapes.rewrite import translated

        columns = _count(self.columns, variables, "array columns")
        rows = _count(self.rows, variables, "array rows")
        dx, dy = evaluate(self.dx, variables), evaluate(self.dy, variables)
        return [
            translated(_with_indices(node, {"i": i, "j": j}), _um(i * dx), _um(j * dy))
            for j in range(rows)
            for i in range(columns)
        ]


class PolarArrayModifier(Modifier):
    """Copies around a centre, ``step`` degrees apart (a full circle by default).

    With ``rotate`` the copies turn with the circle; without, they keep their
    orientation and only their position goes round. The copy's index is ``i``.
    """

    kind: Literal["polar_array"] = "polar_array"
    icon: ClassVar[str] = "polar_array"
    count: Value = 4.0
    x: Value = 0.0  # the centre
    y: Value = 0.0
    step: Value | None = None  # degrees between copies; default 360 / count
    rotate: bool = True

    def apply(self, produce, variables):
        count = _count(self.count, variables, "polar array count")
        step = self._step(count, variables)
        cx, cy = evaluate(self.x, variables), evaluate(self.y, variables)
        geometry, declared = Geometry(), None
        for k in range(count):
            copy, points = produce({**variables, "i": float(k)})
            angle = k * step
            if self.rotate:
                around = kdb.DCplxTrans(cx, cy) * kdb.DCplxTrans(1, angle, False, 0, 0)
                transform = around * kdb.DCplxTrans(-cx, -cy)
            else:  # move the copy's centre round the circle, keep its orientation
                box = _bbox(copy)
                px, py = (box.center().x, box.center().y) if box else (cx, cy)
                turned = kdb.DCplxTrans(1, angle, False, 0, 0) * kdb.DPoint(px - cx, py - cy)
                transform = kdb.DCplxTrans(cx + turned.x - px, cy + turned.y - py)
            geometry.merge(_placed(copy, transform))
            if declared is None:
                declared = points
        return geometry, declared or {}

    def copies(self, variables=None):
        count = _try_count(self.count, variables)
        return 1 if count is None else count

    def summary(self):
        return f"polar array ×{_format(self.count)}"

    def _step(self, count: int, variables: dict[str, float]) -> float:
        if self.step is not None:
            return evaluate(self.step, variables)
        return 360.0 / count if count else 0.0

    def baked(self, node, variables, center):
        from mems_sketch.core.shapes.rewrite import translated

        count = _count(self.count, variables, "polar array count")
        step = self._step(count, variables)
        cx, cy = evaluate(self.x, variables), evaluate(self.y, variables)
        copies = []
        for k in range(count):
            copy, angle = _with_indices(node, {"i": k}), k * step
            if self.rotate:
                turned = kdb.DCplxTrans(1, angle, False, 0, 0) * kdb.DPoint(cx, cy)
                copies.append(_wrapped(copy, x=cx - turned.x, y=cy - turned.y, rotation=angle))
            else:
                px, py = center(copy)
                turned = kdb.DCplxTrans(1, angle, False, 0, 0) * kdb.DPoint(px - cx, py - cy)
                copies.append(translated(copy, _um(cx + turned.x - px), _um(cy + turned.y - py)))
        return copies


class MirrorModifier(Modifier):
    """The node and its mirror image across the line at ``x`` (vertical, ``axis: x``),
    at ``y`` (horizontal, ``axis: y``), or both (four copies). Without ``keep``
    only the mirror image remains."""

    kind: Literal["mirror"] = "mirror"
    icon: ClassVar[str] = "mirror_h"
    axis: Literal["x", "y", "both"] = "x"
    x: Value = 0.0
    y: Value = 0.0
    keep: bool = True  # keep the original next to its mirror image

    def apply(self, produce, variables):
        original, declared = produce(variables)
        x0, y0 = evaluate(self.x, variables), evaluate(self.y, variables)
        flip_x = kdb.DCplxTrans(1, 180, True, 2 * x0, 0)  # x -> 2 x0 - x
        flip_y = kdb.DCplxTrans(1, 0, True, 0, 2 * y0)  # y -> 2 y0 - y
        transforms = {"x": [flip_x], "y": [flip_y], "both": [flip_x, flip_y, flip_x * flip_y]}
        geometry = Geometry()
        if self.keep:
            geometry.merge(original)
        for transform in transforms[self.axis]:
            geometry.merge(_placed(original, transform))
        return geometry, declared

    def copies(self, variables=None):
        return (2 if self.axis != "both" else 4) - (0 if self.keep else 1)

    def summary(self):
        return f"mirror {self.axis}"

    def baked(self, node, variables, center):
        x0, y0 = evaluate(self.x, variables), evaluate(self.y, variables)
        flip_x = {"mirror_x": True, "rotation": 180.0, "x": 2 * x0}
        flip_y = {"mirror_x": True, "y": 2 * y0}
        both = {"rotation": 180.0, "x": 2 * x0, "y": 2 * y0}
        flips = {"x": [flip_x], "y": [flip_y], "both": [flip_x, flip_y, both]}[self.axis]
        copies = [node.model_copy(deep=True)] if self.keep else []
        return copies + [_wrapped(node.model_copy(deep=True), **flip) for flip in flips]


def _um(value: float) -> float:
    return round(value, 6) + 0.0


def _with_indices(node: Shape, indices: dict[str, int]) -> Shape:
    """A copy of ``node`` with array indices in its expressions replaced by numbers."""
    from mems_sketch.core.shapes.rewrite import map_expressions

    values = {name: str(value) for name, value in indices.items()}
    return map_expressions([node], lambda name: values.get(name))[0]


def _wrapped(node: Shape, **placement: float | bool) -> Shape:
    """``node`` inside a transform with this placement."""
    from mems_sketch.core.shapes.kinds.transform import TransformShape

    fields = {k: (_um(v) if isinstance(v, float) else v) for k, v in placement.items()}
    if "rotation" in fields:
        fields["rotation"] = _um(math.remainder(fields["rotation"], 360.0))
    return TransformShape(children=[node], **fields)


def _bbox(geometry: Geometry) -> kdb.DBox | None:
    from mems_sketch.core.component import DBU_UM

    box = kdb.Box()
    for region in geometry.layers.values():
        box += region.bbox()
    return None if box.empty() else box.to_dtype(DBU_UM)


MODIFIER_KINDS: tuple[type[Modifier], ...] = (ArrayModifier, PolarArrayModifier, MirrorModifier)
AnyModifier = Annotated[
    ArrayModifier | PolarArrayModifier | MirrorModifier, Field(discriminator="kind")
]
MODIFIER_ADAPTER: TypeAdapter = TypeAdapter(AnyModifier)
BY_MODIFIER_KIND: dict[str, type[Modifier]] = {m.kind_name(): m for m in MODIFIER_KINDS}


def new_modifier(kind: str) -> Modifier:
    """A modifier of ``kind`` with its default settings."""
    try:
        return BY_MODIFIER_KIND[kind]()
    except KeyError:
        raise ValueError(f"unknown modifier '{kind}'") from None


def apply_stack(
    modifiers: list[Modifier], produce: Produce, variables: dict[str, float]
) -> tuple[Geometry, dict[str, Point]]:
    """The node's result with its enabled modifiers applied, first to last."""
    for modifier in modifiers:
        if modifier.enabled:
            produce = _bind(modifier, produce)
    return produce(variables)


def _bind(modifier: Modifier, inner: Produce) -> Produce:
    return lambda variables: modifier.apply(inner, variables)


def total_copies(modifiers: list[Modifier], variables: dict[str, float] | None = None) -> int:
    count = 1
    for modifier in modifiers:
        if modifier.enabled:
            count *= modifier.copies(variables)
    return count
