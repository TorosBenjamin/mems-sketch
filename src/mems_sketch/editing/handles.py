"""Dragging a shape's handles: its sides, vertices, radii and angles (see
:mod:`mems_sketch.core.shapes.handles`).

A drag is worked out in the shape's own frame and applied as one edit. The
coordinates it changes get an offset, so expressions are kept (``length``
becomes ``length + 3``). An aligned shape keeps its alignment: when the drag
moves the point it is aligned by (the side its ``left`` sits on), the
alignment's offset changes by as much, so the dragged handle still ends up
under the cursor and the rest of the shape stays put.

Dragged with Alt (:meth:`HandleEdits.retuned`), a coordinate that names a
parameter changes that parameter instead: ``x1 = length`` dragged 3 µm sets
``length`` from 40 to 43, and everything else that uses it follows. A parameter
reached through a derived one (``pitch = width + gap``) is found too; one with a
trial value, or a coordinate that names none, gets the offset as usual.

While dragging, :meth:`HandleEdits.preview` builds the changed shape on its
own (its expressions evaluated, without modifiers), so the canvas can show it
without building the whole component.
"""

from __future__ import annotations

import typing
from dataclasses import dataclass
from typing import Any

from mems_sketch.core.component import Geometry
from mems_sketch.core.expressions import evaluate, resolve_variables
from mems_sketch.core.project import Project
from mems_sketch.core.shapes import NodePath, Shape, container_of, frame_of, node_at
from mems_sketch.core.shapes.handles import Handle, handle_label, rounded, typed_point
from mems_sketch.core.shapes.points import BBOX_POINTS
from mems_sketch.core.transform import Transform
from mems_sketch.editing.commands import Commands
from mems_sketch.editing.dimensions import ordered_names


@dataclass
class HandleDrag:
    """A drag of one handle, worked out when it starts. ``frame`` maps the frame of
    the list holding the node into the component's (its parents' frames and its
    alignment); the node's own frame is that turned by its rotation (``own``)."""

    path: NodePath
    node: Shape
    handle: Handle
    frame: Transform
    scope: dict[str, float]
    box: tuple[float, float, float, float] | None  # the node alone, before the drag

    def value(self, value: Any) -> float:
        return _number(value, self.scope)

    @property
    def own(self) -> Transform:
        """Maps the node's own frame (its handles') into the component's."""
        return own_frame(self.node, self.frame, self.value)

    @property
    def at(self) -> tuple[float, float]:
        """Where the handle is, in the component's frame."""
        return self.own.apply(self.handle.x, self.handle.y)


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
            own = own_frame(node, frame, lambda value: _number(value, scope))
        except (ValueError, KeyError, ZeroDivisionError):  # an expression it cannot work out
            return []
        return [(h, *own.apply(h.x, h.y)) for h in handles]

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
        return self._moved_to(drag, drag.own.inverted().apply(x, y))

    def typed(self, drag: HandleDrag, values: list[float]) -> Shape:
        """The node with the handle where typed ``values`` put it: a side's coordinate,
        a vertex's x and y, a radius, an angle or a width, in the node's own frame
        (see :func:`~mems_sketch.core.shapes.handles.typed_point`)."""
        return self._moved_to(drag, typed_point(drag.handle, values))

    def _moved_to(self, drag: HandleDrag, to: tuple[float, float]) -> Shape:
        node = drag.node
        changed = node.handle_moved(drag.handle.key, to, drag.value)
        moved = type(node).model_validate({**node.model_dump(), **changed})
        return _keep_alignment(drag, _follow_corners(drag, _keep_turn(drag, moved)))

    def retuned(self, drag: HandleDrag, x: float, y: float) -> tuple[Shape, dict[str, float]]:
        """The drag to ``(x, y)`` done with Alt: the node, and the parameters that change
        (name: new default). A coordinate that names a parameter keeps its expression
        and the parameter changes to put it under the cursor; the others get an
        offset, as :meth:`dragged` does."""
        node = drag.node
        to = drag.own.inverted().apply(x, y)
        changed = node.handle_moved(drag.handle.key, to, drag.value)
        tuned: dict[str, float] = {}
        rest: dict[str, Any] = {}
        for field, new in changed.items():
            old = getattr(node, field)
            if isinstance(new, list) and isinstance(old, list) and len(new) == len(old):
                new = [self._tuned_point(drag, a, b, tuned) for a, b in zip(old, new, strict=True)]
                if new != old:
                    rest[field] = new
            elif not self._tune(drag, old, new, tuned):
                rest[field] = new
        if not rest:
            return node, tuned
        moved = type(node).model_validate({**node.model_dump(), **rest})
        return _keep_alignment(drag, _follow_corners(drag, _keep_turn(drag, moved))), tuned

    def _tuned_point(self, drag: HandleDrag, old, new, tuned: dict[str, float]):
        if not isinstance(old, list | tuple) or old == new:
            return new
        kept = [a if self._tune(drag, a, b, tuned) else b for a, b in zip(old, new, strict=True)]
        return type(new)(kept)

    def _tune(self, drag: HandleDrag, old, new, tuned: dict[str, float]) -> bool:
        """Change a parameter ``old`` (an expression) names so it is worth ``new``;
        False if none can (``tuned`` gains it, and holds those changed already)."""
        if not isinstance(old, str) or old == new:
            return False
        try:
            target = _number(new, self._scope_with(drag, tuned))
        except (ValueError, KeyError, ZeroDivisionError):
            return False
        for name in self._tunable(old):
            if name in tuned:
                return True  # another coordinate set it: this one follows it
            value = self._solved(drag, old, name, target, tuned)
            if value is not None:
                tuned[name] = value
                return True
        return False

    def _tunable(self, expression: str) -> list[str]:
        """The parameters an expression depends on that a drag can change, in the order
        it names them: its own, or those a derived one uses; not one with a trial
        value (that is what it is built with)."""
        definitions = {p.name: p for p in self.session.active_definition.parameters}
        trials = self.session.trials_for(self.session.active)
        found: list[str] = []

        def visit(text: str, seen: set[str]) -> None:
            for name in ordered_names(text):
                if name not in definitions or name in seen or name in trials:
                    continue
                seen.add(name)
                default = definitions[name].default
                if isinstance(default, str):
                    visit(default, seen)
                elif name not in found:
                    found.append(name)

        visit(expression, set())
        return found

    def _solved(self, drag, expression: str, name: str, target: float, tuned) -> float | None:
        """The value of parameter ``name`` that makes ``expression`` worth ``target``
        (kept within its limits), or None if none does."""
        definition = next(p for p in self.session.active_definition.parameters if p.name == name)

        def worth(value: float) -> float:
            return _number(expression, self._scope_with(drag, {**tuned, name: value}))

        try:
            value = float(definition.default)
            for _ in range(20):  # Newton's method: one step for a linear expression
                now = worth(value)
                if abs(now - target) < 1e-9:
                    break
                step = max(abs(value) * 1e-6, 1e-6)
                slope = (worth(value + step) - now) / step
                if abs(slope) < 1e-12:
                    return None
                value += (target - now) / slope
            if abs(worth(value) - target) > 1e-6:
                return None  # it cannot reach it
            return self._within_limits(drag, definition, value, tuned)
        except (ValueError, KeyError, ZeroDivisionError, OverflowError):
            return None

    def _within_limits(self, drag, definition, value: float, tuned) -> float | None:
        if definition.integer:
            value = float(round(value))
        scope = self._scope_with(drag, tuned)
        for limit, exclusive, low in (
            (definition.min, definition.min_exclusive, True),
            (definition.max, definition.max_exclusive, False),
        ):
            if limit is None:
                continue
            bound = _number(limit, scope)
            past = value < bound if low else value > bound
            if past or (exclusive and value == bound):
                if exclusive:
                    return None
                value = bound
        return rounded(value)

    def _scope_with(self, drag: HandleDrag, tuned: dict[str, float]) -> dict[str, float]:
        """The drag's scope with parameters set to ``tuned`` (derived ones follow)."""
        if not tuned:
            return drag.scope
        definitions = self.session.active_definition.parameters
        values: dict[str, Any] = {p.name: p.default for p in definitions}
        values.update(self.session.trials_for(self.session.active))
        values.update(tuned)
        fixed = {k: v for k, v in drag.scope.items() if k not in values}
        return {**drag.scope, **resolve_variables(values, fixed)}

    def followers(self, drag: HandleDrag, tuned: dict[str, float]) -> list[str]:
        """The other shapes a change of ``tuned`` moves: those that use the parameters
        (or ones derived from them), and those aligned to one that moves."""
        if not tuned:
            return []
        affected = self.session.dimensions.affected(tuned)
        mine = drag.path[:1]
        shapes = self.session.shapes
        found = [node_at(shapes, p) for p in affected.paths if p[:1] != mine]
        return [node.name or node.kind for node in found]

    def sketch(self, drag: HandleDrag, node: Shape) -> tuple[list, bool] | None:
        """The line to draw for ``node`` while dragging, in the component's frame, for
        one that has no preview of its own (a guide; a route, from its start port
        through its via points to its end port); None for the others."""
        try:
            found = node.drag_sketch(drag.value)
        except (ValueError, KeyError, ZeroDivisionError):
            return None
        if found is None:
            return None
        points, closed = found
        points = [drag.frame.apply(x, y) for x, y in points]
        try:
            ends = self.session.results.node_ports(drag.path)
        except Exception:  # noqa: BLE001 - drawn without its ends
            ends = {}
        if "start" in ends and "end" in ends:  # a route: its ends are its ports
            points = [(ends["start"].x, ends["start"].y), *points, (ends["end"].x, ends["end"].y)]
        return points, closed

    def preview(
        self, drag: HandleDrag, node: Shape, tuned: dict[str, float] | None = None
    ) -> Geometry | None:
        """``node`` built on its own, in the component's frame, with the parameters
        ``tuned`` (:meth:`retuned`) (None: it does not build)."""
        from mems_sketch.engine import build_shapes

        try:
            plain = numeric(node, self._scope_with(drag, tuned or {}))
            if plain is None:
                return None
            return build_shapes([plain]).transformed(drag.frame)
        except Exception:  # noqa: BLE001 - e.g. a degenerate shape: no preview
            return None

    def describe(self, drag: HandleDrag, node: Shape, tuned: dict[str, float] | None = None) -> str:
        """What the drag changes, e.g. ``x1  length → length + 3   (43 µm)``, or with
        Alt ``length  40 → 43   also moves: pad``."""
        old, new = drag.node, node
        parts, aligned = [], []
        definitions = {p.name: p for p in self.session.active_definition.parameters}
        for name, value in (tuned or {}).items():
            parts.append(f"{name}  {_shown(definitions[name].default, drag)} → {value:g}")
        for field in type(old).model_fields:
            before, after = getattr(old, field), getattr(new, field)
            if before == after:
                continue
            if field in ("points", "via"):
                noun = "via point" if field == "via" else "point"
                parts.append(_points_change(before, after, drag, noun))
            elif field == "align":  # (last: the drag's own change first)
                for axis in ("dx", "dy"):
                    a, b = getattr(before, axis), getattr(after, axis)
                    if a != b:
                        aligned.append(f"align {axis}  {_shown(a, drag)} → {_shown(b, drag)}")
            else:
                unit = "°" if field.endswith("angle") else " µm"
                parts.append(f"{field}  {_shown(before, drag)} → {_shown(after, drag, unit)}")
        text = "   ".join(parts + aligned)
        others = self.followers(drag, tuned or {})
        if others:
            shown = ", ".join(others[:3]) + (
                f" and {len(others) - 3} more" if len(others) > 3 else ""
            )
            text += f"   also moves: {shown}"
        return text

    def drag(self, path: NodePath, key: str, x: float, y: float) -> None:
        """Drag handle ``key`` of the node at ``path`` to ``(x, y)``: one edit."""
        drag = self._started(path, key)
        self._replace(path, drag.node, self.dragged(drag, x, y), "Resize")

    def drag_parameters(self, path: NodePath, key: str, x: float, y: float) -> None:
        """Drag handle ``key`` to ``(x, y)`` with Alt (see :meth:`retuned`): one edit,
        the parameters and the node together."""
        drag = self._started(path, key)
        node, tuned = self.retuned(drag, x, y)
        if not tuned:
            self._replace(path, drag.node, node, "Resize")
            return

        def change(project: Project) -> None:
            parameters = self.session.local(project).parameters
            for index, p in enumerate(parameters):
                if p.name in tuned:
                    parameters[index] = p.model_copy(update={"default": tuned[p.name]})
            if node != drag.node:
                container, index = container_of(self.session.shapes_in(project), path)
                container[index] = node

        self.session.edit(f"Set {', '.join(tuned)}", change)

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
        moved = _keep_alignment(drag, _follow_corners(drag, _keep_turn(drag, moved)))
        self._replace(path, node, moved, f"Remove {handle_label(key)} of")

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


def own_frame(node: Shape, frame: Transform, v) -> Transform:
    """``frame`` (the list's) followed by the node's own rotation (#214): where its
    handles and dimensions are."""
    turn = node.turn(v)
    return frame if turn is None else frame * turn


def _keep_turn(drag: HandleDrag, moved: Shape) -> Shape:
    """A turned node (its ``rotation``) moved so that what the drag did not change
    stays where it was: the drag moves the centre it turns about, which would
    move the rest of it too."""
    try:
        before, after = drag.node.turn(drag.value), moved.turn(drag.value)
    except (ValueError, KeyError, ZeroDivisionError):
        return moved
    if before is None or after is None:
        return moved
    (bx, by), (ax, ay) = before.apply(0.0, 0.0), after.apply(0.0, 0.0)
    dx, dy = round(bx - ax, 6), round(by - ay, 6)
    if dx == 0 and dy == 0:
        return moved
    from mems_sketch.core.shapes.handles import shifted

    changed = moved.moved(lambda x: shifted(x, dx), lambda y: shifted(y, dy), lambda inner: inner)
    return moved.model_copy(update=changed)


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


def _points_change(before, after, drag: HandleDrag, noun: str = "point") -> str:
    """``point 3  (2, 30) → (10, 30)``, ``new point 3  (5, 5)`` or ``point 3 removed``."""

    def at(point) -> str:
        return ", ".join(f"{drag.value(c):g}" for c in point)

    index = next(
        (i for i, (a, b) in enumerate(zip(before, after, strict=False)) if a != b),
        min(len(before), len(after)),
    )
    if len(after) > len(before):
        return f"new {noun} {index + 1}  ({at(after[index])})"
    if len(after) < len(before):
        return f"{noun} {index + 1} removed"
    return f"{noun} {index + 1}  ({at(before[index])}) → ({at(after[index])})"


def _follow_corners(drag: HandleDrag, moved: Shape) -> Shape:
    """``moved`` with the rounded corners of its corners modifier (the Corners tool's)
    on the vertices they round: a corner on a vertex the drag moved moves with it
    (an offset, so expressions are kept); one on a vertex that is gone goes too.
    Corners at a named point (``self.top_left``) follow by themselves."""
    from mems_sketch.core.shapes.handles import shifted
    from mems_sketch.core.shapes.modifiers import CornersModifier

    node = drag.node
    index = next((i for i, m in enumerate(node.modifiers) if isinstance(m, CornersModifier)), None)
    if index is None:
        return moved
    try:
        before, after = node.corner_vertices(drag.value), moved.corner_vertices(drag.value)
    except (ValueError, KeyError, ZeroDivisionError):
        return moved
    if before is None or after is None:
        return moved
    corners = []
    for corner in node.modifiers[index].corners:
        if corner.at is not None or "self." in f"{corner.x}{corner.y}":
            corners.append(corner)
            continue
        try:
            at = (drag.value(corner.x), drag.value(corner.y))
        except (ValueError, KeyError, ZeroDivisionError):
            corners.append(corner)
            continue
        vertex = next((i for i, p in enumerate(before) if _same(p, at)), None)
        if vertex is None:
            corners.append(corner)  # not on a vertex now: an error it shows already
        elif len(after) == len(before):  # a vertex moved
            dx, dy = after[vertex][0] - before[vertex][0], after[vertex][1] - before[vertex][1]
            corners.append(
                corner.model_copy(
                    update={
                        "x": shifted(corner.x, round(dx, 6)),
                        "y": shifted(corner.y, round(dy, 6)),
                    }
                )
            )
        elif any(_same(p, at) for p in after):  # one added or removed elsewhere
            corners.append(corner)
        # else: its vertex was removed, so it goes too
    modifiers = list(moved.modifiers)
    modifiers[index] = modifiers[index].model_copy(update={"corners": corners})
    return moved.model_copy(update={"modifiers": modifiers})


def _same(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6


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
