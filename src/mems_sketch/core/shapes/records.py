"""How the nodes of a shape tree came out of a build (the engine's records):
what the editor selects, shows points on and moves."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mems_sketch.core.shapes.points import NodePoints
from mems_sketch.core.shapes.tree import NodePath
from mems_sketch.core.transform import IDENTITY, Transform

if TYPE_CHECKING:
    from mems_sketch.core.component import Geometry
    from mems_sketch.core.shapes.registry import Shape


@dataclass
class NodeRecord:
    """How one node came out of an evaluation (for the GUI: selection, point markers).

    ``geometry`` and ``points`` are in the frame of the list holding the node;
    ``inner`` maps the node's children's frame into that frame, and ``shift``
    is the move its alignment applied (identity when it is not aligned).
    """

    geometry: Geometry
    points: NodePoints
    inner: Transform
    shift: Transform


def transform_of(shape: Shape, v: dict[str, float]) -> Transform:
    """The placement a node applies to its content, in µm (identity if it has none)."""
    transform = shape.placement(v)
    return IDENTITY if transform is None else transform


def frame_of(record: Mapping[NodePath, NodeRecord], path: NodePath) -> Transform:
    """Maps the frame of the list holding ``path`` into the component's frame."""
    transform = IDENTITY
    for depth in range(1, len(path)):
        transform = transform * record[path[:depth]].inner
    return transform
