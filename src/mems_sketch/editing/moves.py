"""Moving, rotating and mirroring shapes of the active component."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import klayout.db as kdb

from mems_sketch.core.component import Geometry
from mems_sketch.core.project import Project
from mems_sketch.core.shapes import (
    NodePath,
    NodeRecord,
    Shape,
    TransformShape,
    container_of,
    frame_of,
    node_at,
    offset_value,
    own_strings,
    point_names,
    to_ictrans,
    translated,
    walk,
)
from mems_sketch.editing.commands import Commands
from mems_sketch.editing.naming import fresh_name


@dataclass
class DragPlan:
    """What moves when the given shapes are dragged, worked out once when a drag starts.

    ``roots`` are the shapes that get moved; ``followers`` are aligned to them
    (directly or in a chain) and move along by exactly the same amount;
    ``loose`` use their points in expressions and are only updated when the
    drag ends. ``preview`` is the geometry of roots and followers, and
    ``points`` / ``targets`` are the roots' points and the other shapes'
    points for snapping, all in the component's frame.
    """

    roots: list[NodePath] = field(default_factory=list)
    followers: list[NodePath] = field(default_factory=list)
    loose: list[NodePath] = field(default_factory=list)
    preview: Geometry = field(default_factory=Geometry)
    points: list[tuple[NodePath, str, float, float]] = field(default_factory=list)
    targets: list[tuple[str, float, float]] = field(default_factory=list)


class MoveEdits(Commands):
    """Moving, rotating and mirroring shapes of the active component."""

    def plan_drag(self, paths: list[NodePath]) -> DragPlan:
        """Work out what moves along with ``paths`` (see :class:`DragPlan`)."""
        record = self.session.results.inspection()
        shapes = self.session.shapes
        plan = DragPlan(roots=_outermost([p for p in paths if p in record]))
        if not plan.roots:
            return plan
        moving = set()
        for path in plan.roots:
            moving |= _subtree_names(node_at(shapes, path))

        def inside(path: NodePath, groups: list[NodePath]) -> bool:
            return any(path[: len(g)] == g for g in groups)

        changed = True
        while changed:  # anything aligned to a moving shape moves too
            changed = False
            for path in sorted(record):
                if inside(path, plan.roots + plan.followers):
                    continue
                node = node_at(shapes, path)
                if node.align is not None and node.align.to.partition(".")[0] in moving:
                    plan.followers.append(path)
                    moving |= _subtree_names(node)
                    changed = True
        for path in sorted(record):
            if inside(path, plan.roots + plan.followers):
                continue
            used = {
                n.partition(".")[0]
                for t in own_strings(node_at(shapes, path))
                for n in point_names(t)
            }
            if used & moving:
                plan.loose.append(path)
        for path in _outermost(plan.roots + plan.followers):
            plan.preview.merge(record[path].geometry, to_ictrans(frame_of(record, path)))
        for path in plan.roots:
            plan.points += [
                (path, name, x, y) for name, x, y in self.session.results.node_points(path)
            ]
        plan.targets = [
            (name, x, y)
            for name, _, x, y in self.session.results.align_targets(plan.roots[0])
            if name.partition(".")[0] not in moving
        ]
        return plan

    def move(self, paths: list[NodePath], dx: float, dy: float, detach: bool = False) -> None:
        """Move shapes by ``(dx, dy)``, given in the active component's frame.

        Each shape is moved in its own parent's frame, so shapes inside a
        rotated transform follow the mouse. Aligned shapes get a new offset,
        or with ``detach`` lose their alignment and keep where they are, moved.
        Shapes aligned to another moved shape follow it by themselves.
        """
        record = self.session.results.inspection()
        roots = _outermost([p for p in paths if p in record])
        if not roots or (dx == 0 and dy == 0 and not detach):
            return
        shapes = self.session.shapes
        names = {p: _subtree_names(node_at(shapes, p)) for p in roots}
        moves = {}
        for path in roots:
            node = node_at(shapes, path)
            others = frozenset().union(*(n for p, n in names.items() if p != path))
            if node.align is not None and node.align.to.partition(".")[0] in others:
                continue  # follows the shape it is aligned to
            delta = frame_of(record, path).inverted() * kdb.DVector(dx, dy)
            if detach and node.align is not None:
                delta += record[path].shift.disp
                node = node.model_copy(update={"align": None})
            moves[path] = translated(node, delta.x, delta.y, others)
        label = ", ".join(node_at(shapes, p).name or node_at(shapes, p).kind for p in moves)

        def change(project: Project) -> None:
            for path, moved in moves.items():
                container, index = container_of(self.session.shapes_in(project), path)
                container[index] = moved

        self.session.edit(f"Move {label}", change)

    def rotate(self, paths: list[NodePath], angle: float, pivot: tuple[float, float]) -> None:
        """Rotate shapes by ``angle`` degrees (counter-clockwise) about ``pivot``."""
        px, py = pivot
        about = kdb.DCplxTrans(px, py) * kdb.DCplxTrans(1, angle, False, 0, 0)
        self.transform(paths, about * kdb.DCplxTrans(-px, -py), f"Rotate {angle:g}°")

    def mirror(self, paths: list[NodePath], left_right: bool, center: tuple[float, float]) -> None:
        """Mirror shapes left-right (about a vertical line) or up-down, through ``center``."""
        cx, cy = center
        if left_right:
            flip = kdb.DCplxTrans(1, 180, True, 2 * cx, 0)
        else:
            flip = kdb.DCplxTrans(1, 0, True, 0, 2 * cy)
        self.transform(paths, flip, "Mirror " + ("left-right" if left_right else "up-down"))

    def transform(self, paths: list[NodePath], transform: kdb.DCplxTrans, description: str) -> None:
        """Apply a rigid transform, given in the active component's frame, to shapes.

        References and transforms get a new ``rotation`` / ``mirror_x`` (and
        position); other shapes have no orientation of their own, so they are
        wrapped in a transform that takes over their name, so alignments and
        point expressions that use them keep working. Aligned shapes stay
        attached: only their orientation changes.
        """
        record = self.session.results.inspection()
        roots = _outermost([p for p in paths if p in record])
        if not roots:
            return
        shapes = self.session.shapes
        names = {p: _subtree_names(node_at(shapes, p)) for p in roots}
        taken = {n.name for n in walk(shapes) if n.name}
        replacements = {}
        for path in roots:
            node = node_at(shapes, path)
            others = frozenset().union(*(n for p, n in names.items() if p != path))
            if node.align is not None and node.align.to.partition(".")[0] in others:
                continue  # follows the shape it is aligned to
            frame = frame_of(record, path)
            local = frame.inverted() * transform * frame  # the same move, in the parent's frame
            if type(node).placed:
                replacements[path] = _reoriented(node, local, record[path])
            else:
                name = node.name or fresh_name("transform", taken)
                inner = fresh_name(f"{name}_shape", taken)
                taken |= {name, inner}
                wrapper = TransformShape(
                    name=name,
                    align=node.align,
                    children=[node.model_copy(update={"name": inner, "align": None})],
                    rotation=_angle(local.angle),
                    mirror_x=local.is_mirror(),
                    x=_round_um(local.disp.x),
                    y=_round_um(local.disp.y),
                )
                replacements[path] = wrapper

        def change(project: Project) -> None:
            for path, new in replacements.items():
                container, index = container_of(self.session.shapes_in(project), path)
                container[index] = new

        self.session.edit(description, change)


def _reoriented(node: Shape, local: kdb.DCplxTrans, record: NodeRecord) -> Shape:
    """A reference or transform with ``local`` applied to its placement (in its parent's frame).

    Expressions stay expressions: rotation and position get offsets added.
    """
    placed = record.shift.inverted() * record.inner  # its own placement, without alignment
    new = local * record.inner
    turn = _angle(new.angle - placed.angle)
    update: dict[str, Any] = {
        "rotation": (
            offset_value(node.rotation, turn)
            if isinstance(node.rotation, str)
            else _angle(node.rotation + turn) + 0.0  # + 0.0: no "-0"
        ),
        "mirror_x": new.is_mirror(),
    }
    if node.align is None:  # an aligned shape keeps its attachment; only the orientation changes
        update["x"] = offset_value(node.x, new.disp.x - placed.disp.x)
        update["y"] = offset_value(node.y, new.disp.y - placed.disp.y)
    return node.model_copy(update=update)


def _angle(degrees: float) -> float:
    """An angle in (-180, 180], without float noise."""
    angle = round(degrees % 360, 6)
    return angle - 360 if angle > 180 else angle


def _round_um(value: float) -> float:
    return round(value, 6)


def _outermost(paths: list[NodePath]) -> list[NodePath]:
    """``paths`` without those inside another one of them (they move with it)."""
    unique = list(dict.fromkeys(paths))
    return [p for p in unique if not any(q != p and p[: len(q)] == q for q in unique)]


def _subtree_names(node: Shape) -> set[str]:
    return {n.name for n in walk([node]) if n.name}
