"""Rewriting shapes: moving them and changing the names their expressions use."""

from __future__ import annotations

import ast
from collections.abc import Callable
from typing import Any

from mems_sketch.core.expressions import ExpressionError, substitute
from mems_sketch.core.shapes.base import (
    SHAPE_ADAPTER,
    ArcShape,
    BooleanShape,
    CircleShape,
    FilletShape,
    LayerMapShape,
    OffsetShape,
    PathShape,
    PolygonShape,
    RectShape,
    RefShape,
    Shape,
    TransformShape,
    Value,
)
from mems_sketch.core.shapes.points import point_names
from mems_sketch.core.shapes.tree import walk


def offset_value(value: Value, delta: float) -> Value:
    """``value`` plus ``delta``, keeping expressions parametric.

    Numbers are added to. An expression gets a trailing ``+ delta`` term; if
    it already ends in ``+ number`` or ``- number``, that number is updated,
    so repeated moves do not pile up terms: ``plate/2 + 39`` moved by 11
    becomes ``plate/2 + 50``.
    """
    if not delta:
        return value
    if not isinstance(value, str):
        return _round(float(value) + delta)
    try:
        body = ast.parse(value.strip(), mode="eval").body
    except SyntaxError:
        return value
    base, constant = value.strip(), 0.0
    if (
        isinstance(body, ast.BinOp)
        and isinstance(body.op, ast.Add | ast.Sub)
        and isinstance(body.right, ast.Constant)
        and isinstance(body.right.value, int | float)
    ):
        base = ast.get_source_segment(value.strip(), body.left) or ast.unparse(body.left)
        constant = body.right.value if isinstance(body.op, ast.Add) else -body.right.value
    total = _round(constant + delta)
    if total == 0:
        return base
    sign = "+" if total > 0 else "-"
    return f"{base} {sign} {_number(abs(total))}"


def _round(value: float) -> float:
    return round(value, 6)  # well below the 1 nm grid; hides float noise


def _number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def translated(shape: Shape, dx: float, dy: float, moving: frozenset[str] = frozenset()) -> Shape:
    """A copy of ``shape`` moved by ``(dx, dy)`` in the frame of the list holding it.

    An aligned node keeps its alignment and gets a new offset. A reference or
    transform moves by its ``x``, ``y`` (its content stays in its own frame);
    a primitive by all its coordinates; other operations by moving their
    children. Nodes whose position follows from a node in ``moving`` (which
    moves too) are left alone: aligned to it, or using its points for the same
    axis in an expression.
    """
    if shape.align is not None:
        if shape.align.to.partition(".")[0] in moving:
            return shape
        align = shape.align.model_copy(
            update={"dx": offset_value(shape.align.dx, dx), "dy": offset_value(shape.align.dy, dy)}
        )
        return shape.model_copy(update={"align": align})

    def x(value: Value) -> Value:
        return value if _follows(value, "x", moving) else offset_value(value, dx)

    def y(value: Value) -> Value:
        return value if _follows(value, "y", moving) else offset_value(value, dy)

    match shape:
        case RectShape():
            update = {"x0": x(shape.x0), "x1": x(shape.x1), "y0": y(shape.y0), "y1": y(shape.y1)}
        case PolygonShape() | PathShape():
            update = {"points": [(x(px), y(py)) for px, py in shape.points]}
        case CircleShape() | ArcShape() | RefShape() | TransformShape():
            update = {"x": x(shape.x), "y": y(shape.y)}
        case BooleanShape():
            inner = moving | _names(shape)
            update = {
                "a": [translated(c, dx, dy, inner) for c in shape.a],
                "b": [translated(c, dx, dy, inner) for c in shape.b],
            }
        case OffsetShape() | FilletShape() | LayerMapShape():
            inner = moving | _names(shape)
            update = {"children": [translated(c, dx, dy, inner) for c in shape.children]}
    return shape.model_copy(update=update)


def _names(shape: Shape) -> frozenset[str]:
    return frozenset(n.name for n in walk([shape]) if n.name)


def _follows(value: Value, axis: str, moving: frozenset[str]) -> bool:
    """Whether an expression uses a moving node's point on ``axis`` (so it moves by itself)."""
    if not isinstance(value, str) or not moving:
        return False
    return any(
        name.endswith(f".{axis}") and name.partition(".")[0] in moving
        for name in point_names(value)
    )


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
    return [SHAPE_ADAPTER.validate_python(item) for item in data]


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
