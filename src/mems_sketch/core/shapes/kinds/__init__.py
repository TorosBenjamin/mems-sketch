"""Every shape kind, in the order of the shape union.

To add a kind, write a module like ``rect.py`` (a :class:`~mems_sketch.core.shapes.base.Node`
subclass) and list its class in ``KINDS``. The contract test in
``tests/test_shape_kinds.py`` then checks it.
"""

from mems_sketch.core.shapes.kinds.arc import ArcShape
from mems_sketch.core.shapes.kinds.boolean import BooleanShape
from mems_sketch.core.shapes.kinds.circle import CircleShape
from mems_sketch.core.shapes.kinds.fillet import FilletShape
from mems_sketch.core.shapes.kinds.guide import GuideShape
from mems_sketch.core.shapes.kinds.layer_map import LayerMapShape
from mems_sketch.core.shapes.kinds.offset import OffsetShape
from mems_sketch.core.shapes.kinds.path import PathShape
from mems_sketch.core.shapes.kinds.polygon import PolygonShape
from mems_sketch.core.shapes.kinds.rect import RectShape
from mems_sketch.core.shapes.kinds.ref import RefShape
from mems_sketch.core.shapes.kinds.transform import TransformShape

KINDS = (
    RectShape,
    PolygonShape,
    CircleShape,
    ArcShape,
    PathShape,
    RefShape,
    TransformShape,
    BooleanShape,
    OffsetShape,
    FilletShape,
    LayerMapShape,
    GuideShape,
)
