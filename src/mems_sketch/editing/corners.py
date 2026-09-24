"""Corners picked on the canvas, for a node's corners modifier.

A click lands on a vertex of what the node makes (before its corners
modifier). It is recorded so that it follows the design, most stable first:

1. one of the node's own points (``self.top_right``);
2. a point of a named shape next to it (``beam.top``);
3. per axis: an own point's coordinate (``self.top.y``), a coordinate the
   shapes inside the node are written with (``slot_x``: where a cut made the
   corner), or a neighbour's (``beam.right.x``);
4. else the numbers.
"""

from __future__ import annotations

import contextlib
from typing import Any

import klayout.db as kdb

from mems_sketch.core.component import DBU_UM, Geometry
from mems_sketch.core.expressions import evaluate
from mems_sketch.core.shapes import NodePath, Shape, frame_of, to_ictrans
from mems_sketch.core.shapes.geometry import apply_transform
from mems_sketch.core.shapes.modifiers import Corner, CornersModifier
from mems_sketch.core.shapes.points import NodePoints
from mems_sketch.editing.commands import Commands

TOLERANCE_UM = 0.002  # positions this close are the same corner

Position = tuple[float, float]


class CornerEdits(Commands):
    """The corners of a node's corners modifier; each command is one undo step."""

    # -- what can be picked ------------------------------------------------------

    def candidates(self, path: NodePath) -> list[Position]:
        """The vertices of the node before its corners modifier, in the component's
        frame: the corners that can be rounded."""
        geometry, _, to_component = self._before(path)
        result: list[Position] = []
        for region in geometry.layers.values():
            for polygon in region.merged().each():
                rings = [polygon.each_point_hull()]
                rings += [polygon.each_point_hole(h) for h in range(polygon.holes())]
                for ring in rings:
                    for p in ring:
                        q = to_component * kdb.DPoint(p.x * DBU_UM, p.y * DBU_UM)
                        if not any(_same((q.x, q.y), r) for r in result):
                            result.append((q.x, q.y))
        return result

    def rounded(self, path: NodePath) -> list[tuple[int, Position]]:
        """The node's recorded corners that can be found: (index, position in the
        component's frame)."""
        found = self._modifier(self.session.node(path))
        if found is None:
            return []
        _, modifier = found
        _, own, to_component = self._before(path)
        variables = self._variables(path, own)
        result = []
        for index, corner in enumerate(modifier.corners):
            with contextlib.suppress(ValueError):  # the messages panel says what is wrong
                x, y = CornersModifier(corners=[corner]).positions(variables)[0]
                q = to_component * kdb.DPoint(x, y)
                result.append((index, (q.x, q.y)))
        return result

    def at(self, path: NodePath, x: float, y: float) -> int | None:
        """The recorded corner at ``(x, y)`` (component frame), if any."""
        return next((i for i, p in self.rounded(path) if _same(p, (x, y))), None)

    # -- editing -----------------------------------------------------------------

    def add(
        self, path: NodePath, x: float, y: float, radius: Any = 1.0, style: str = "round"
    ) -> int:
        """Round the corner at ``(x, y)`` (component frame; a vertex, see
        :meth:`candidates`). The node gets a corners modifier first in its stack
        if it has none (so an array copies the rounded shape). Returns the
        corner's index; a corner already there is not added twice."""
        existing = self.at(path, x, y)
        if existing is not None:
            return existing
        fields = self.describe(path, x, y)
        corner = Corner(**fields, radius=radius, style=style)
        node = self.session.node(path)
        found = self._modifier(node)
        modifiers = list(node.modifiers)
        if found is None:
            modifiers.insert(0, CornersModifier(corners=[corner]))
            index = 0
        else:
            m, modifier = found
            modifiers[m] = modifier.model_copy(update={"corners": [*modifier.corners, corner]})
            index = len(modifier.corners)
        self.session.modifiers._set(path, modifiers, f"Round a corner of {_label(node)}")
        return index

    def update(self, path: NodePath, index: int, /, **fields: Any) -> None:
        """Change a corner's radius, style or position (``at``, ``x``, ``y``)."""
        node = self.session.node(path)
        m, modifier = self._require(node)
        corners = list(modifier.corners)
        corners[index] = Corner.model_validate({**corners[index].model_dump(), **fields})
        modifiers = list(node.modifiers)
        modifiers[m] = modifier.model_copy(update={"corners": corners})
        self.session.modifiers._set(path, modifiers, f"Change a corner of {_label(node)}")

    def remove(self, path: NodePath, index: int) -> None:
        """Make a corner sharp again (the modifier goes when it has none left)."""
        node = self.session.node(path)
        m, modifier = self._require(node)
        corners = [c for i, c in enumerate(modifier.corners) if i != index]
        modifiers = list(node.modifiers)
        if corners:
            modifiers[m] = modifier.model_copy(update={"corners": corners})
        else:
            modifiers.pop(m)
        self.session.modifiers._set(path, modifiers, f"Sharpen a corner of {_label(node)}")

    # -- recording ---------------------------------------------------------------

    def describe(self, path: NodePath, x: float, y: float) -> dict[str, Any]:
        """How to record the corner at ``(x, y)`` (component frame): ``at``, or ``x``
        and ``y``; see the module docstring."""
        _, own, to_component = self._before(path)
        p = to_component.inverted() * kdb.DPoint(x, y)
        target = (p.x, p.y)
        for name in own.names():
            try:
                if _same(own.point(name), target):
                    return {"at": f"self.{name}"}
            except ValueError:
                continue
        neighbours = self._neighbours(path)
        for name, point in neighbours.items():
            if _same(point, target):
                return {"at": name}
        return {
            "x": self._axis(path, own, neighbours, 0, p.x),
            "y": self._axis(path, own, neighbours, 1, p.y),
        }

    def _axis(self, path, own: NodePoints, neighbours: dict, axis: int, value: float) -> Any:
        """An expression for one coordinate of a corner (a number if nothing fits)."""
        name = "xy"[axis]
        options: list[tuple[Any, float]] = []
        for point in own.names():
            try:
                options.append((f"self.{point}.{name}", own.point(point)[axis]))
            except ValueError:
                continue
        written = self._written(path, axis)
        options += [(v, n) for v, n in written if isinstance(v, str)]
        options += [(f"{point}.{name}", xy[axis]) for point, xy in neighbours.items()]
        for expression, number in options:
            if abs(number - value) <= TOLERANCE_UM:
                return expression
        return round(value, 6) + 0.0

    def _written(self, path: NodePath, axis: int) -> list[tuple[Any, float]]:
        """The coordinates the node and the shapes inside it are written with (the
        ones in its own frame: not inside placed parts, not aligned), evaluated."""
        variables = self.session.results.scope(path)
        found: list[tuple[Any, float]] = []

        def collect(value, which):
            if which == axis:
                with contextlib.suppress(ValueError):  # names only valid deeper inside
                    found.append((value, evaluate(value, variables)))
            return value

        def visit(shape: Shape, top: bool) -> None:
            if (shape.align is not None and not top) or type(shape).placed:
                return
            shape.moved(lambda v: collect(v, 0), lambda v: collect(v, 1), lambda c: c)
            for children in shape.child_lists():
                for child in children:
                    visit(child, False)

        visit(self.session.node(path), True)
        return found

    def _neighbours(self, path: NodePath) -> dict[str, Position]:
        """Points of the named shapes the node can see, in the frame its modifiers
        work in; none when it is aligned (its modifiers' frame moves with it)."""
        if self.session.node(path).align is not None:
            return {}
        scope = self.session.results.scope(path)
        points = {}
        for key in scope:
            if key.count(".") == 2 and key.endswith(".x") and key[:-2] + ".y" in scope:
                points[key[:-2]] = (scope[key], scope[key[:-2] + ".y"])
        return points

    def _variables(self, path: NodePath, own: NodePoints) -> dict[str, float]:
        variables = dict(self.session.results.scope(path))
        for name in own.names():
            try:
                x, y = own.point(name)
            except ValueError:
                continue
            variables[f"self.{name}.x"], variables[f"self.{name}.y"] = x, y
        return variables

    # -- helpers -----------------------------------------------------------------

    def _before(self, path: NodePath) -> tuple[Geometry, NodePoints, kdb.DCplxTrans]:
        """What the node makes before its corners modifier, where its modifiers work
        (its list's frame, before its alignment moves it): the geometry, its
        points, and the transform into the component's frame as it is shown."""
        node = self.session.node(path)
        found = self._modifier(node)
        keep = found[0] if found is not None else 0
        before = node.model_copy(update={"modifiers": node.modifiers[:keep]})
        record = self.session.results.inspect_with(path, before)
        if path not in record:
            raise ValueError(f"'{_label(node)}' does not build")
        shown = self.session.results.inspection()
        unshift = record[path].shift.inverted()
        geometry = Geometry()
        geometry.merge(record[path].geometry, to_ictrans(unshift))
        declared = {
            name: apply_transform(unshift, point)
            for name, point in record[path].points.declared.items()
        }
        own = NodePoints("self", geometry, declared)
        shift = shown[path].shift if path in shown else kdb.DCplxTrans()
        return geometry, own, frame_of(record, path) * shift

    def _modifier(self, node: Shape) -> tuple[int, CornersModifier] | None:
        for index, modifier in enumerate(node.modifiers):
            if isinstance(modifier, CornersModifier):
                return index, modifier
        return None

    def _require(self, node: Shape) -> tuple[int, CornersModifier]:
        found = self._modifier(node)
        if found is None:
            raise ValueError(f"'{_label(node)}' has no rounded corners")
        return found


def _same(a: Position, b: Position) -> bool:
    return abs(a[0] - b[0]) <= TOLERANCE_UM and abs(a[1] - b[1]) <= TOLERANCE_UM


def _label(node: Shape) -> str:
    return node.name or node.kind
