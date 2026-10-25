"""Parameters on the canvas (#266): the selected shape's values that use
parameters, as dimensions, and what a parameter affects.

:meth:`Dimensions.of` places a node's dimensions (see
:class:`~mems_sketch.core.shapes.handles.Dimension`) in the component's frame,
keeping those that name a parameter or a process constant.
:meth:`Dimensions.affected` finds what changing parameters would move: the
nodes that use them, directly or through a derived parameter, and the nodes
aligned to those (and to those, and so on).

A placed component shows its parameters: each where a shape inside it uses the
parameter as a value (``x1 = length`` in the component: the dimension of its
side, placed and turned with the placement), the others as a row of labels
under it. The label says this placement's value (``length = 120``, or
``length = L (120)`` when the placement gives an expression); typing for it sets
the placement's value, or the parameter that expression names.

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
import typing
from dataclasses import dataclass, replace

from mems_sketch.core.component import Geometry
from mems_sketch.core.expressions import names_in
from mems_sketch.core.project import Project
from mems_sketch.core.shapes import NodePath, Shape, container_of, frame_of, node_at, paths
from mems_sketch.core.shapes.handles import ACROSS, Dimension, entry_size, handle_label, typed_point
from mems_sketch.core.user_component import RESERVED_PARAM_NAMES
from mems_sketch.editing.commands import Commands

CHIP = "chip"  # a placement's parameter with no dimension: a label in a row under it
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
    text: str | None = None  # the label, when not ``expression = value``
    owner: NodePath | None = None  # a placement's: the placement
    about: str | None = None  # a placement's: what it is, for the label by the cursor

    @property
    def label(self) -> str:
        """``length = 40``; ``length + 3 = 43``."""
        return self.text or f"{self.expression} = {self.value:g}"


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
        placed_component = node.placed_component()
        if placed_component is not None:
            return self._of_placement(path, node, frame, scope, *placed_component)

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

    def _of_placement(self, path, node, frame, scope, name, given) -> list[PlacedDimension]:
        """A placed component's numeric parameters (see the module docstring)."""
        engine, active = self.session.engine, self.session.active
        try:
            component = engine.component(name, active)
            qualified = engine.project.qualify(name, active)
            hidden = set(getattr(component, "internal", ()))  # not settable where placed (#299)
            numeric = [
                n
                for n, f in component.Params.model_fields.items()
                if _numeric(f.annotation) and n not in hidden
            ]
            values = {k: _number(v, scope) for k, v in given.items() if k in numeric}
            inner = engine.variables(qualified, values)
            inside = frame * node.placement(scope)
        except Exception:  # noqa: BLE001 - it does not evaluate: nothing to show
            return []
        found = self._inside(qualified, values, inner, set(numeric))
        anchor = self._under(path)
        known = self._parameter_names()
        title = f"{node.name} ({name})" if node.name else name
        placed = []
        for parameter in numeric:
            if parameter not in inner:
                continue
            value, raw = inner[parameter], given.get(parameter)
            if isinstance(raw, str):
                text = f"{parameter} = {raw} ({value:g})"
                names = tuple(
                    n for n in ordered_names(raw) if n in known or n.startswith("process.")
                )
            else:
                text, names = f"{parameter} = {value:g}", ()
            extra = {
                "field": f"params.{parameter}",
                "expression": parameter,
                "value": value,
                "names": names,
                "text": text,
                "owner": path,
                "about": f"{parameter} of {title}",
            }
            if parameter in found:
                dimension, within = found[parameter]
                placed.append(replace(_placed(dimension, inside * within, ()), **extra))
            elif anchor is not None:
                placed.append(
                    PlacedDimension(a=anchor, b=anchor, side=(0.0, 0.0), style=CHIP, **extra)
                )
        return placed

    def _inside(self, component: str, values, scope, wanted: set[str]) -> dict:
        """For each of ``wanted`` (parameters of ``component``), the first dimension
        inside it that is just that parameter, and the frame it is in (the
        component's own); none for a component without shapes of its own."""
        engine = self.session.engine
        definition = engine.project.definition(component)
        if definition is None:
            return {}
        try:
            record = engine.build(component, values).records()
        except Exception:  # noqa: BLE001 - it does not build: labels only
            return {}

        def v(value) -> float:
            return float(value) if isinstance(value, int | float) else _evaluate(value, scope)

        found: dict = {}
        for inner_path, inner in paths(definition[0].shapes):
            if inner_path not in record:
                continue
            try:
                dimensions = inner.dimensions(v)
            except (ValueError, KeyError, ZeroDivisionError):
                continue
            for d in dimensions:
                key = d.expression.strip()
                if key in wanted and key not in found:
                    found[key] = (d, frame_of(record, inner_path) * record[inner_path].shift)
        return found

    def _under(self, path: NodePath) -> Point | None:
        """The bottom-left corner of the node at ``path`` (the component's frame)."""
        record = self.session.results.inspection()
        if path not in record:
            return None
        box = record[path].geometry.bbox()
        if box is None:
            return None
        return frame_of(record, path).apply(box[0], box[1])

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
        if isinstance(value, str) or not isinstance(current, str):  # (a placement's number)
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
        alone = handles.preview(drag, change.node)
        return alone if alone is not None else self._built_in_place(path, change)

    def _built_in_place(self, path: NodePath, change: Change) -> Geometry | None:
        """The node built where it is, in the component as it would be: for one that
        does not build on its own (a placement)."""
        if change.created:
            return None  # (a parameter the component does not have yet)
        session = self.session
        active = session.active
        try:
            engine = session.engine.trial(session.results._trial(path, change.node))
            values = {**session.trials_for(active), **change.parameters}
            record = engine.build(active, values).records()
            if path not in record:
                return None
            return record[path].geometry.transformed(frame_of(record, path))
        except Exception:  # noqa: BLE001 - it does not build: no preview
            return None

    @staticmethod
    def dragged_value(dimension: PlacedDimension, x: float, y: float) -> float:
        """The value a dimension would have with its end (``b``) dragged to ``(x, y)``
        (the component's frame): its value scaled by how far along it that is, from
        its start (a width: from its middle)."""
        (ax, ay), (bx, by) = dimension.a, dimension.b
        if dimension.style == ACROSS:
            ax, ay = (ax + bx) / 2, (ay + by) / 2
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        if length < 1e-12:
            return dimension.value
        along = ((x - ax) * dx + (y - ay) * dy) / length
        return round(dimension.value * along, 6) + 0.0

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
        before = "default" if before is None else _shown(before)
        shown = field.removeprefix("params.")
        return f"{shown}  {before} → {_shown(field_value(change.node, field))}"

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
    """The value a dimension's ``field`` names: ``x1``, ``point.2.x``, or a placement's
    ``params.length`` (None when the placement leaves it to its default)."""
    prefix, _, rest = field.partition(".")
    if prefix == "params" and rest:
        return node.params.get(rest)
    if prefix == "point" and rest:
        index, _, axis = rest.partition(".")
        return node.points[int(index)][0 if axis == "x" else 1]
    return getattr(node, field)


def with_field(node: Shape, field: str, value) -> Shape:
    """``node`` with ``field`` (as :func:`field_value` names it) set to ``value``."""
    prefix, _, rest = field.partition(".")
    if prefix == "params" and rest:
        return node.model_copy(update={"params": {**node.params, rest: value}})
    if prefix == "point" and rest:
        index, _, axis = rest.partition(".")
        points = list(node.points)
        x, y = points[int(index)]
        points[int(index)] = (value, y) if axis == "x" else (x, value)
        return node.model_copy(update={"points": points})
    return type(node).model_validate({**node.model_dump(), field: value})


def _numeric(annotation) -> bool:
    """Whether a component parameter's type is a number (not a switch or a name)."""
    found = {annotation, *typing.get_args(annotation)}
    return bool(found & {float, int}) and bool not in found and str not in found


def _number(value, scope) -> float:
    return float(value) if isinstance(value, int | float) else _evaluate(value, scope)


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
