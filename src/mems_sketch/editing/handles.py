"""Dragging a shape's handles: its sides, vertices, radii and angles (see
:mod:`mems_sketch.core.shapes.handles`).

A drag is worked out in the shape's own frame and applied as one edit. The
coordinates it changes get an offset, so expressions are kept (``length``
becomes ``length + 3``). An aligned shape keeps its alignment: when the drag
moves the point it is aligned by (the side its ``left`` sits on), the
alignment's offset changes by as much, so the dragged handle still ends up
under the cursor and the rest of the shape stays put.

While dragging, :meth:`HandleEdits.preview` builds the changed shape on its
own (its expressions evaluated, without modifiers), so the canvas can show it
without building the whole component.
"""

from __future__ import annotations

import typing
from dataclasses import dataclass
from typing import Any

from mems_sketch.core.component import Geometry
from mems_sketch.core.expressions import evaluate
from mems_sketch.core.project import Project
from mems_sketch.core.shapes import NodePath, Shape, container_of, frame_of, node_at
from mems_sketch.core.shapes.handles import Handle, handle_label, typed_point
from mems_sketch.core.shapes.points import BBOX_POINTS
from mems_sketch.core.transform import Transform
from mems_sketch.editing.commands import Commands


@dataclass
class HandleDrag:
    """A drag of one handle, worked out when it starts. ``frame`` maps the node's
    own frame into the component's (its parents' frames and its alignment)."""

    path: NodePath
    node: Shape
    handle: Handle
    frame: Transform
    scope: dict[str, float]
    box: tuple[float, float, float, float] | None  # the node alone, before the drag

    def value(self, value: Any) -> float:
        return _number(value, self.scope)

    @property
    def at(self) -> tuple[float, float]:
        """Where the handle is, in the component's frame."""
        return self.frame.apply(self.handle.x, self.handle.y)


class HandleEdits(Commands):
    """See the module docstring. ``session.handles``."""

    def handles(self, path: NodePath) -> list[tuple[Handle, float, float]]:
        """The handles of the node at ``path`` and where they are in the component's
        frame; none for a node that has none or does not evaluate."""
        found = self._frame(path)
        if found is None:
            return []
        node, frame, scope = found
        try:
            handles = node.edit_handles(lambda value: _number(value, scope))
        except (ValueError, KeyError, ZeroDivisionError):  # an expression it cannot work out
            return []
        return [(h, *frame.apply(h.x, h.y)) for h in handles]

    def start(self, path: NodePath, key: str) -> HandleDrag | None:
        """Begin dragging handle ``key`` of the node at ``path`` (None: no such handle)."""
        found = self._frame(path)
        if found is None:
            return None
        node, frame, scope = found
        try:
            handles = node.edit_handles(lambda value: _number(value, scope))
        except (ValueError, KeyError, ZeroDivisionError):
            return None
        handle = next((h for h in handles if h.key == key), None)
        if handle is None:
            return None
        return HandleDrag(path, node, handle, frame, scope, _box(numeric(node, scope)))

    def dragged(self, drag: HandleDrag, x: float, y: float) -> Shape:
        """The node with the handle at ``(x, y)`` (the component's frame)."""
        return self._moved_to(drag, drag.frame.inverted().apply(x, y))

    def typed(self, drag: HandleDrag, values: list[float]) -> Shape:
        """The node with the handle where typed ``values`` put it: a side's coordinate,
        a vertex's x and y, a radius, an angle or a width, in the node's own frame
        (see :func:`~mems_sketch.core.shapes.handles.typed_point`)."""
        return self._moved_to(drag, typed_point(drag.handle, values))

    def _moved_to(self, drag: HandleDrag, to: tuple[float, float]) -> Shape:
        node = drag.node
        changed = node.handle_moved(drag.handle.key, to, drag.value)
        moved = type(node).model_validate({**node.model_dump(), **changed})
        return _keep_alignment(drag, moved)

    def preview(self, drag: HandleDrag, node: Shape) -> Geometry | None:
        """``node`` built on its own, in the component's frame (None: it does not build)."""
        from mems_sketch.engine import build_shapes

        try:
            plain = numeric(node, drag.scope)
            if plain is None:
                return None
            return build_shapes([plain]).transformed(drag.frame)
        except Exception:  # noqa: BLE001 - e.g. a degenerate shape: no preview
            return None

    def describe(self, drag: HandleDrag, node: Shape) -> str:
        """What the drag changes, e.g. ``x1  length → length + 3   (43 µm)``."""
        old, new = drag.node, node
        parts, aligned = [], []
        for field in type(old).model_fields:
            before, after = getattr(old, field), getattr(new, field)
            if before == after:
                continue
            if field == "points":
                parts.append(_points_change(before, after, drag))
            elif field == "align":  # (last: the drag's own change first)
                for axis in ("dx", "dy"):
                    a, b = getattr(before, axis), getattr(after, axis)
                    if a != b:
                        aligned.append(f"align {axis}  {_shown(a, drag)} → {_shown(b, drag)}")
            else:
                unit = "°" if field.endswith("angle") else " µm"
                parts.append(f"{field}  {_shown(before, drag)} → {_shown(after, drag, unit)}")
        return "   ".join(parts + aligned)

    def drag(self, path: NodePath, key: str, x: float, y: float) -> None:
        """Drag handle ``key`` of the node at ``path`` to ``(x, y)``: one edit."""
        drag = self._started(path, key)
        self._replace(path, drag.node, self.dragged(drag, x, y), "Resize")

    def type_in(self, path: NodePath, key: str, values: list[float]) -> None:
        """Put handle ``key`` where typed ``values`` say (see :meth:`typed`): one edit."""
        drag = self._started(path, key)
        self._replace(path, drag.node, self.typed(drag, values), "Resize")

    def remove(self, path: NodePath, key: str) -> None:
        """Remove the vertex handle ``key`` names (the others stay put): one edit.
        Raises ValueError when it cannot go: a polygon keeps three points, a path two."""
        drag = self._started(path, key)
        node = drag.node
        changed = node.handle_removed(key)
        if changed is None:
            fewest = len(getattr(node, "points", ())) or "its"
            raise ValueError(f"{node.name or node.kind} keeps at least {fewest} points")
        moved = type(node).model_validate({**node.model_dump(), **changed})
        self._replace(path, node, _keep_alignment(drag, moved), f"Remove {handle_label(key)} of")

    def _started(self, path: NodePath, key: str) -> HandleDrag:
        drag = self.start(path, key)
        if drag is None:
            raise ValueError(f"no handle '{key}' here")
        return drag

    def _replace(self, path: NodePath, old: Shape, new: Shape, verb: str) -> None:
        if new == old:
            return

        def change(project: Project) -> None:
            container, index = container_of(self.session.shapes_in(project), path)
            container[index] = new

        self.session.edit(f"{verb} {new.name or new.kind}", change)

    def _frame(self, path: NodePath) -> tuple[Shape, Transform, dict[str, float]] | None:
        record = self.session.results.inspection()
        if path not in record:
            return None
        node = node_at(self.session.shapes, path)
        frame = frame_of(record, path) * record[path].shift
        try:
            scope = self.session.results.scope(path)
        except Exception:  # noqa: BLE001 - the design does not evaluate
            return None
        return node, frame, scope


def numeric(node: Shape, scope: dict[str, float]) -> Shape | None:
    """``node`` with its expressions worked out, without alignment or modifiers (to
    build it on its own); None if one does not evaluate."""
    data = node.model_dump(exclude={"align", "name"})
    data["modifiers"] = []
    try:
        for field, info in type(node).model_fields.items():
            if field in data and _holds_values(info.annotation):
                data[field] = _evaluated(data[field], scope)
        return type(node).model_validate(data)
    except (ValueError, KeyError, ZeroDivisionError, TypeError):
        return None


def _holds_values(annotation) -> bool:
    """Whether a field's type has values (``float | str``) in it, e.g. ``points``."""
    found = set()

    def walk(t) -> None:
        found.add(t)
        for arg in typing.get_args(t):
            walk(arg)

    walk(annotation)
    return float in found and str in found


def _evaluated(value, scope):
    if isinstance(value, str):
        return _number(value, scope)
    if isinstance(value, list | tuple):
        return type(value)(_evaluated(v, scope) for v in value)
    return value


def _number(value, scope: dict[str, float]) -> float:
    return float(value) if isinstance(value, int | float) else float(evaluate(value, scope))


def _box(node: Shape | None) -> tuple[float, float, float, float] | None:
    if node is None:
        return None
    from mems_sketch.engine import build_shapes

    try:
        return build_shapes([node]).bbox()
    except Exception:  # noqa: BLE001 - it does not build on its own
        return None


def _box_point(box, point: str) -> tuple[float, float]:
    x0, y0, x1, y1 = box
    xs = {"left": x0, "right": x1}
    ys = {"bottom": y0, "top": y1}
    if point == "center":
        return (x0 + x1) / 2, (y0 + y1) / 2
    if point in xs:
        return xs[point], (y0 + y1) / 2
    if point in ys:
        return (x0 + x1) / 2, ys[point]
    vertical, _, horizontal = point.partition("_")
    return xs[horizontal], ys[vertical]


def _keep_alignment(drag: HandleDrag, moved: Shape) -> Shape:
    """An aligned node's offset changed by as much as the drag moved its aligned
    point, so the node stays where it was but for the dragged handle."""
    align = moved.align
    if align is None or align.point not in BBOX_POINTS or drag.box is None:
        return moved
    box = _box(numeric(moved, drag.scope))
    if box is None:
        return moved
    (x0, y0), (x1, y1) = _box_point(drag.box, align.point), _box_point(box, align.point)
    from mems_sketch.core.shapes.handles import shifted

    offset = {
        "dx": shifted(align.dx, round(x1 - x0, 6)),
        "dy": shifted(align.dy, round(y1 - y0, 6)),
    }
    return moved.model_copy(update={"align": align.model_copy(update=offset)})


def _points_change(before, after, drag: HandleDrag) -> str:
    """``point 3  (2, 30) → (10, 30)``, ``new point 3  (5, 5)`` or ``point 3 removed``."""

    def at(point) -> str:
        return ", ".join(f"{drag.value(c):g}" for c in point)

    index = next(
        (i for i, (a, b) in enumerate(zip(before, after, strict=False)) if a != b),
        min(len(before), len(after)),
    )
    if len(after) > len(before):
        return f"new point {index + 1}  ({at(after[index])})"
    if len(after) < len(before):
        return f"point {index + 1} removed"
    return f"point {index + 1}  ({at(before[index])}) → ({at(after[index])})"


def _shown(value, drag: HandleDrag, unit: str | None = None) -> str:
    """A value as the drag label shows it: a number, or an expression (with its value
    and ``unit`` when one is given)."""
    if isinstance(value, int | float):
        return f"{value:g}"
    if unit is None:
        return value
    try:
        return f"{value}   ({drag.value(value):g}{unit})"
    except Exception:  # noqa: BLE001 - shown without its value
        return value
