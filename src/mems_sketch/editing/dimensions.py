"""Parameters on the canvas (#266): the selected shape's values that use
parameters, as dimensions, and what a parameter affects.

:meth:`Dimensions.of` places a node's dimensions (see
:class:`~mems_sketch.core.shapes.handles.Dimension`) in the component's frame,
keeping those that name a parameter or a process constant.
:meth:`Dimensions.affected` finds what changing parameters would move: the
nodes that use them, directly or through a derived parameter, and the nodes
aligned to those (and to those, and so on).

Typing for a dimension (:meth:`Dimensions.set`) changes the parameter: a value
for ``length`` sets ``length``; a number for ``w / 2`` sets ``w`` so that it is
worth that (as Alt-drag does); an expression for ``length`` becomes its default,
and one for ``length + 3`` replaces the value. ``name = value`` makes a
parameter and puts it in the value, here and for a chosen handle
(:meth:`Dimensions.make_for_handle`).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from mems_sketch.core.component import Geometry
from mems_sketch.core.expressions import names_in
from mems_sketch.core.project import Project
from mems_sketch.core.shapes import NodePath, Shape, container_of, node_at, paths
from mems_sketch.core.shapes.handles import Dimension, entry_size, handle_label, typed_point
from mems_sketch.core.user_component import RESERVED_PARAM_NAMES
from mems_sketch.editing.commands import Commands

NEW_PARAMETER = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*(\S.*?)\s*$")  # ``gap = 3``

Point = tuple[float, float]


@dataclass(frozen=True)
class PlacedDimension:
    """A :class:`~mems_sketch.core.shapes.handles.Dimension` in the component's
    frame; ``side`` turned with it; ``names`` the parameters (and process
    constants) its expression uses, in the order it names them."""

    field: str
    expression: str
    value: float
    a: Point
    b: Point
    side: Point
    style: str
    names: tuple[str, ...]

    @property
    def label(self) -> str:
        """``length = 40``; ``length + 3 = 43``."""
        return f"{self.expression} = {self.value:g}"


@dataclass(frozen=True)
class Change:
    """What a typed value does: ``node`` as it becomes, ``parameters`` set (name:
    new default) and ``created`` (name: default)."""

    node: Shape
    parameters: dict
    created: dict


@dataclass(frozen=True)
class Affected:
    """What changing some parameters moves: ``users`` use them (or parameters
    derived from them), ``aligned`` are aligned to one that moves."""

    users: tuple[NodePath, ...]
    aligned: tuple[NodePath, ...]

    @property
    def paths(self) -> list[NodePath]:
        return [*self.users, *self.aligned]


class Dimensions(Commands):
    """See the module docstring. ``session.dimensions``."""

    def of(self, path: NodePath) -> list[PlacedDimension]:
        """The dimensions of the node at ``path`` that use a parameter or a process
        constant, in the component's frame; none if it does not evaluate."""
        found = self.session.handles._frame(path)
        if found is None:
            return []
        node, frame, scope = found

        def v(value) -> float:
            return float(value) if isinstance(value, int | float) else _evaluate(value, scope)

        try:
            dimensions = node.dimensions(v)
        except (ValueError, KeyError, ZeroDivisionError):
            return []
        known = self._parameter_names()
        placed = []
        for d in dimensions:
            names = tuple(
                n for n in ordered_names(d.expression) if n in known or n.startswith("process.")
            )
            if names:
                placed.append(_placed(d, frame, names))
        return placed

    def affected(self, names) -> Affected:
        """The nodes that changing the parameters ``names`` moves (see :class:`Affected`)."""
        changed = self._with_derived(set(names))
        nodes = list(paths(self.session.shapes))
        users = tuple(p for p, node in nodes if _uses(_own(node), changed))
        moving = {node.name for p, node in nodes if p in users and node.name}
        aligned: list[NodePath] = []
        grew = True
        while grew:  # aligned to one that moves: it moves too
            grew = False
            for p, node in nodes:
                if p in users or p in aligned or node.align is None:
                    continue
                if node.align.to.partition(".")[0] in moving:
                    aligned.append(p)
                    if node.name:
                        moving.add(node.name)
                    grew = True
        return Affected(users, tuple(aligned))

    def describe(self, names) -> str:
        """``length  used by beam, rail · moves pad (aligned)``."""
        names = list(names)
        affected = self.affected(names)
        used = _listed(self._names_of(affected.users))
        text = f"{', '.join(names)}  " + (f"used by {used}" if used else "used by nothing")
        if affected.aligned:
            text += f" · moves {_listed(self._names_of(affected.aligned))} (aligned)"
        return text

    # -- typing for a dimension, or name = value for a handle ------------------------

    def plan(self, path: NodePath, field: str, text: str) -> Change:
        """What typing ``text`` for the dimension of ``field`` (of the node at ``path``)
        does (see the module docstring); raises ValueError if it cannot."""
        node = node_at(self.session.shapes, path)
        current = field_value(node, field)
        made = NEW_PARAMETER.match(text)
        if made:
            name, default = made.group(1), _value(made.group(2))
            self._check_new(name)
            return Change(with_field(node, field, name), {}, {name: default})
        value = _value(text)
        definitions = {p.name: p for p in self.session.active_definition.parameters}
        bare = current.strip() if isinstance(current, str) else None
        if bare in definitions and not (
            isinstance(value, float) and isinstance(definitions[bare].default, str)
        ):  # (a number for a derived one: solved for what it is derived from)
            return Change(node, {bare: value}, {})
        if isinstance(value, str):
            return Change(with_field(node, field, value), {}, {})
        tuned = self._solved(path, current, value)
        if not tuned:
            raise ValueError(f"no parameter of {current} can make it {value:g}")
        return Change(node, tuned, {})

    def set(self, path: NodePath, field: str, text: str) -> None:
        """Type ``text`` for the dimension of ``field``: one edit (see :meth:`plan`)."""
        self._apply(path, self.plan(path, field, text), field)

    def preview(self, path: NodePath, field: str, text: str) -> tuple[Geometry | None, str]:
        """The node as typing ``text`` would make it (built on its own, in the
        component's frame) and what it changes, e.g. ``length  40 → 45   moves beam,
        pad``; raises ValueError if it cannot be typed."""
        change = self.plan(path, field, text)
        return self._built(path, change), self._said(path, field, change)

    def make_for_handle(self, path: NodePath, key: str, text: str) -> None:
        """``name = value`` typed for handle ``key``: make the parameter and put it in
        the value the handle sets (one that takes one value: a side, a radius, a
        width, an angle): one edit."""
        made = NEW_PARAMETER.match(text)
        if made is None:
            raise ValueError("type name = value to make a parameter")
        name, default = made.group(1), _value(made.group(2))
        self._check_new(name)
        handles = self.session.handles
        drag = handles._started(path, key)
        if entry_size(drag.handle) != 1:
            raise ValueError(f"name = value sets one value; {handle_label(key)} takes two (x, y)")
        value = drag.value(default)
        fields = drag.node.handle_moved(key, typed_point(drag.handle, [value]), drag.value)
        if len(fields) != 1:
            raise ValueError(f"{handle_label(key)} changes more than one value")
        (field,) = fields
        node = with_field(handles.typed(drag, [value]), field, name)
        self._apply(path, Change(node, {}, {name: default}), field)

    def _check_new(self, name: str) -> None:
        if name in RESERVED_PARAM_NAMES:
            raise ValueError(f"'{name}' is a reserved name")
        if name in self._parameter_names():
            raise ValueError(f"there is already a parameter '{name}'")

    def _solved(self, path: NodePath, expression, value: float) -> dict[str, float]:
        """The parameters to change so ``expression`` is worth ``value`` (as Alt-drag)."""
        from mems_sketch.editing.handles import HandleDrag

        found = self.session.handles._frame(path)
        if found is None or not isinstance(expression, str):
            return {}
        node, frame, scope = found
        drag = HandleDrag(path, node, None, frame, scope, None)
        tuned: dict[str, float] = {}
        self.session.handles._tune(drag, expression, value, tuned)
        return tuned

    def _apply(self, path: NodePath, change: Change, field: str) -> None:
        original = node_at(self.session.shapes, path)
        active = self.session.active

        def edit(project: Project) -> None:
            self.session.local(project)
            for name, default in {**change.created, **change.parameters}.items():
                project.set_parameter(name, default, active)
            if change.node != original:
                container, index = container_of(self.session.shapes_in(project), path)
                container[index] = change.node

        if change.created:
            what = f"Make parameter {', '.join(change.created)}"
        elif change.parameters:
            what = f"Set {', '.join(change.parameters)}"
        else:
            what = f"Set {field} of {_name(original)}"
        self.session.edit(what, edit)

    def _built(self, path: NodePath, change: Change) -> Geometry | None:
        from mems_sketch.editing.handles import HandleDrag

        found = self.session.handles._frame(path)
        if found is None:
            return None
        node, frame, scope = found
        drag = HandleDrag(path, node, None, frame, scope, None)
        handles = self.session.handles
        try:
            values = {**change.created, **change.parameters}
            drag = HandleDrag(path, node, None, frame, handles._scope_with(drag, values), None)
        except Exception:  # noqa: BLE001 - e.g. an expression that does not resolve
            return None
        return handles.preview(drag, change.node)

    def _said(self, path: NodePath, field: str, change: Change) -> str:
        definitions = {p.name: p for p in self.session.active_definition.parameters}
        if change.created:
            ((name, default),) = change.created.items()
            return f"new parameter {name} = {_shown(default)}; {field} becomes {name}"
        if change.parameters:
            parts = [
                f"{name}  {_shown(definitions[name].default)} → {_shown(value)}"
                for name, value in change.parameters.items()
            ]
            moved = _listed(self._names_of(self.affected(change.parameters).paths))
            return "   ".join(parts) + (f"   moves {moved}" if moved else "")
        before = field_value(node_at(self.session.shapes, path), field)
        return f"{field}  {_shown(before)} → {_shown(field_value(change.node, field))}"

    def _names_of(self, found) -> list[str]:
        shapes = self.session.shapes
        return [_name(node_at(shapes, p)) for p in found]

    def _parameter_names(self) -> set[str]:
        return {p.name for p in self.session.active_definition.parameters}

    def _with_derived(self, changed: set[str]) -> set[str]:
        """``changed`` and the parameters derived from them (``pitch = width + gap``)."""
        definitions = self.session.active_definition.parameters
        grew = True
        while grew:
            grew = False
            for p in definitions:
                derived = p.name not in changed and isinstance(p.default, str)
                if derived and _named(p.default) & changed:
                    changed.add(p.name)
                    grew = True
        return changed


def _placed(d: Dimension, frame, names: tuple[str, ...]) -> PlacedDimension:
    a, b = frame.apply(*d.a), frame.apply(*d.b)
    side = (0.0, 0.0)
    if d.side != (0.0, 0.0):
        tip = frame.apply(d.a[0] + d.side[0], d.a[1] + d.side[1])
        dx, dy = tip[0] - a[0], tip[1] - a[1]
        length = math.hypot(dx, dy) or 1.0
        side = (dx / length, dy / length)
    return PlacedDimension(d.field, d.expression, d.value, a, b, side, d.style, names)


def field_value(node: Shape, field: str):
    """The value a dimension's ``field`` names: ``x1``, or ``point.2.x``."""
    prefix, _, rest = field.partition(".")
    if prefix == "point" and rest:
        index, _, axis = rest.partition(".")
        return node.points[int(index)][0 if axis == "x" else 1]
    return getattr(node, field)


def with_field(node: Shape, field: str, value) -> Shape:
    """``node`` with ``field`` (as :func:`field_value` names it) set to ``value``."""
    prefix, _, rest = field.partition(".")
    if prefix == "point" and rest:
        index, _, axis = rest.partition(".")
        points = list(node.points)
        x, y = points[int(index)]
        points[int(index)] = (value, y) if axis == "x" else (x, value)
        return node.model_copy(update={"points": points})
    return type(node).model_validate({**node.model_dump(), field: value})


def _value(text: str) -> float | str:
    """A typed value: a number, or an expression (checked)."""
    text = text.strip()
    if not text:
        raise ValueError("type a value")
    try:
        return float(text)
    except ValueError:
        pass
    try:
        names_in(text)
    except (ValueError, SyntaxError):
        raise ValueError(f"'{text}' is not a value or an expression") from None
    return text


def _shown(value) -> str:
    return f"{value:g}" if isinstance(value, int | float) else str(value)


def _evaluate(value, scope) -> float:
    from mems_sketch.core.expressions import evaluate

    return float(evaluate(value, scope))


def _named(expression: str) -> set[str]:
    """The names in an expression, or none if it does not parse."""
    try:
        return names_in(expression)
    except (ValueError, SyntaxError):
        return set()


def ordered_names(expression: str) -> list[str]:
    """The names in an expression, in the order it names them."""

    def where(name: str) -> int:
        found = re.search(rf"(?<![\w.]){re.escape(name)}(?![\w.])", expression)
        return found.start() if found else len(expression)

    return sorted(_named(expression), key=lambda n: (where(n), n))


def _own(node: Shape) -> dict:
    """A node's fields, without its children's."""
    return node.model_dump(exclude=set(type(node).child_fields))


def _uses(data, names: set[str]) -> bool:
    """Whether dumped fields name one of ``names`` in an expression."""
    if isinstance(data, dict):
        return any(_uses(v, names) for k, v in data.items() if k not in ("name", "layer", "kind"))
    if isinstance(data, list | tuple):
        return any(_uses(v, names) for v in data)
    return isinstance(data, str) and bool(_named(data) & names)


def _name(node: Shape) -> str:
    return node.name or node.kind


def _listed(names: list[str], most: int = 3) -> str:
    if len(names) <= most:
        return ", ".join(names)
    return f"{', '.join(names[:most])} and {len(names) - most} more"
