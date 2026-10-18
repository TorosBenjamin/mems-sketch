"""How the nodes of a shape tree came out of a build (the engine's records):
what the editor selects, shows points on and moves."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

from mems_sketch.core.shapes.points import NodePoints
from mems_sketch.core.shapes.tree import NodePath
from mems_sketch.core.transform import IDENTITY, Transform

if TYPE_CHECKING:
    from mems_sketch.core.component import Geometry
    from mems_sketch.core.shapes.registry import Shape


class Port(NamedTuple):
    """A port as built (see :class:`~mems_sketch.core.user_component.PortDef`): where
    it is (µm), the direction a wire leaves it in (degrees, counter-clockwise from
    +x), the wire width it expects (0: any) and the layer the wire runs on."""

    x: float
    y: float
    angle: float
    width: float
    layer: str

    def transformed(self, t: Transform) -> Port:
        """The port moved, turned and mirrored by ``t``: its direction turns with it."""
        x, y = t.apply(self.x, self.y)
        dx, dy = t.apply_vector(
            math.cos(math.radians(self.angle)), math.sin(math.radians(self.angle))
        )
        return Port(x, y, math.degrees(math.atan2(dy, dx)), self.width * t.mag, self.layer)


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
    ports: dict[str, Port] = field(default_factory=dict)  # a placed component's


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
