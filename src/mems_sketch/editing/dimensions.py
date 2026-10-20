"""Parameters on the canvas (#266): the selected shape's values that use
parameters, as dimensions, and what a parameter affects.

:meth:`Dimensions.of` places a node's dimensions (see
:class:`~mems_sketch.core.shapes.handles.Dimension`) in the component's frame,
keeping those that name a parameter or a process constant.
:meth:`Dimensions.affected` finds what changing parameters would move: the
nodes that use them, directly or through a derived parameter, and the nodes
aligned to those (and to those, and so on).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from mems_sketch.core.expressions import names_in
from mems_sketch.core.shapes import NodePath, Shape, paths
from mems_sketch.core.shapes.handles import Dimension
from mems_sketch.editing.commands import Commands

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

    def _names_of(self, found) -> list[str]:
        from mems_sketch.core.shapes import node_at

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
