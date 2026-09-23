"""Walking and addressing the shape tree."""

from __future__ import annotations

from collections.abc import Iterator

import klayout.db as kdb

from mems_sketch.core.component import to_dbu
from mems_sketch.core.expressions import evaluate
from mems_sketch.core.shapes.base import (
    BooleanShape,
    FilletShape,
    LayerMapShape,
    OffsetShape,
    RefShape,
    Shape,
    TransformShape,
)


def child_lists(shape: Shape) -> list[list[Shape]]:
    match shape:
        case BooleanShape():
            return [shape.a, shape.b]
        case TransformShape() | OffsetShape() | FilletShape() | LayerMapShape():
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


# A node's address in a tree: one (slot, index) step per level. The slot picks
# one of the parent's child lists (see child_lists); the top level is slot 0.
NodePath = tuple[tuple[int, int], ...]


def container_of(shapes: list[Shape], path: NodePath) -> tuple[list[Shape], int]:
    """The list that holds the node at ``path``, and its index in that list."""
    if not path or path[0][0] != 0:
        raise KeyError(f"invalid node path {path}")
    container = shapes
    for depth, (slot, index) in enumerate(path):
        if depth:
            parent = container[path[depth - 1][1]]
            lists = child_lists(parent)
            if not 0 <= slot < len(lists):
                raise KeyError(f"invalid node path {path}")
            container = lists[slot]
        if not 0 <= index < len(container):
            raise KeyError(f"invalid node path {path}")
    return container, path[-1][1]


def node_at(shapes: list[Shape], path: NodePath) -> Shape:
    container, index = container_of(shapes, path)
    return container[index]


def paths(
    shapes: list[Shape], prefix: NodePath = (), slot: int = 0
) -> Iterator[tuple[NodePath, Shape]]:
    """Every node with its path, depth first."""
    for index, shape in enumerate(shapes):
        path = (*prefix, (slot, index))
        yield path, shape
        for child_slot, children in enumerate(child_lists(shape)):
            yield from paths(children, path, child_slot)


def placement_of(
    shapes: list[Shape], path: NodePath, variables: dict[str, float]
) -> kdb.ICplxTrans:
    """Combined transform of the transforms enclosing ``path`` (repeats and alignment
    are not included; see :class:`NodeRecord` for the placement as evaluated)."""
    transform = kdb.ICplxTrans()
    for depth in range(1, len(path)):
        ancestor = node_at(shapes, path[:depth])
        if isinstance(ancestor, TransformShape):
            v = {**variables, "i": 0.0, "j": 0.0}
            transform = transform * kdb.ICplxTrans(
                evaluate(ancestor.scale, v),
                evaluate(ancestor.rotation, v),
                ancestor.mirror_x,
                to_dbu(evaluate(ancestor.x, v)),
                to_dbu(evaluate(ancestor.y, v)),
            )
    return transform


def find(shapes: list[Shape], name: str) -> Shape:
    for shape in walk(shapes):
        if shape.name == name:
            return shape
    raise KeyError(f"no shape named '{name}'")


def visible_from(path: NodePath, other: NodePath) -> bool:
    """Whether the node at ``path`` can use the points of the node at ``other``.

    ``other`` must sit in a list of ``path``'s parent or of an ancestor, and
    must not be ``path`` itself or one of its ancestors.
    """
    parent, other_parent = path[:-1], other[:-1]
    if other_parent != parent[: len(other_parent)]:
        return False
    return other != path[: len(other)]
