"""The parametric shape tree.

Geometry is described as a tree of nodes that is re-evaluated whenever a
parameter changes; nothing is ever edited destructively. Leaves are primitives
(``rect``, ``polygon``, ``circle``, ``arc``, ``path``) and references to other
components (``ref``). Inner nodes are operations:

* ``transform``  its children moved/rotated/mirrored/scaled as one piece
* ``boolean``    ``a`` op ``b`` with op in union / subtract / intersect / xor
* ``offset``     grow (positive) or shrink (negative) by a distance
* ``fillet``     round convex and concave corners
* ``layer_map``  move geometry between layers (select, rename, derive layers)

Semantics:

* Every node yields geometry on one or more named layers. Booleans, offsets
  and fillets act **per layer**: ``subtract`` removes ``b``'s device-layer
  geometry from ``a``'s device-layer geometry, and so on. To combine
  different layers, bring them onto one layer with ``layer_map`` first.
* Every value may be an expression over the variables in scope: the
  component's parameters inside a component, the design's global variables at
  the top level.
* Any node can carry ``repeat`` to place copies on a grid; inside it the
  column and row indices are ``i`` and ``j`` (the innermost repeat wins).
* ``enabled=False`` skips a node, e.g. to try a variant in the GUI.
* **Alignment points.** Every node has bounding-box points (``center``,
  ``top``, ``bottom_left``, ...); a component reference also has the points its
  component declares. ``align`` moves a node so that one of its points lands
  on a point of another named node, and expressions can use point coordinates
  as ``<node>.<point>.x`` / ``.y``. A node sees the named nodes in its own
  list, in the other child lists of its parent (``a`` and ``b`` of a boolean)
  and in every enclosing list, always in its own coordinate frame. Nodes are
  evaluated in dependency order; a cycle is an error.
* Coordinates snap to the 1 nm database grid; curves are approximated with
  segments no further than :data:`ARC_TOLERANCE_UM` from the true arc.
"""

from mems_sketch.core.shapes.base import (
    INDEX_NAMES,
    Align,
    Node,
    Operation,
    Point,
    Primitive,
    RenderContext,
    Repeat,
    Value,
    check_point_reference,
)
from mems_sketch.core.shapes.geometry import (
    ARC_TOLERANCE_UM,
    MAX_ARC_SEGMENTS,
    to_ictrans,
)
from mems_sketch.core.shapes.kinds import (
    KINDS,
    ArcShape,
    BooleanShape,
    CircleShape,
    FilletShape,
    LayerMapShape,
    OffsetShape,
    PathShape,
    PolygonShape,
    RectShape,
    RefShape,
    TransformShape,
)
from mems_sketch.core.shapes.points import (
    BBOX_POINTS,
    NodePoints,
    own_strings,
    point_dependencies,
    point_names,
    point_values,
)
from mems_sketch.core.shapes.registry import (
    BY_KIND,
    PRIMITIVE_KINDS,
    SHAPE_ADAPTER,
    WRAPPERS,
    Shape,
    default_shape,
    kind_class,
    wrap_shapes,
)
from mems_sketch.core.shapes.render import (
    Evaluator,
    NodeRecord,
    frame_of,
    transform_of,
)
from mems_sketch.core.shapes.rewrite import (
    map_expressions,
    offset_value,
    rename_node_references,
    rewrite,
    translated,
)
from mems_sketch.core.shapes.tree import (
    NodePath,
    child_lists,
    container_of,
    find,
    node_at,
    paths,
    placement_of,
    references,
    visible_from,
    walk,
)

GroupShape = TransformShape  # the earlier name

__all__ = [
    "ARC_TOLERANCE_UM",
    "BBOX_POINTS",
    "BY_KIND",
    "INDEX_NAMES",
    "KINDS",
    "MAX_ARC_SEGMENTS",
    "PRIMITIVE_KINDS",
    "SHAPE_ADAPTER",
    "WRAPPERS",
    "Align",
    "ArcShape",
    "BooleanShape",
    "CircleShape",
    "Evaluator",
    "FilletShape",
    "GroupShape",
    "LayerMapShape",
    "Node",
    "NodePath",
    "NodePoints",
    "NodeRecord",
    "OffsetShape",
    "Operation",
    "PathShape",
    "Point",
    "PolygonShape",
    "Primitive",
    "RectShape",
    "RefShape",
    "RenderContext",
    "Repeat",
    "Shape",
    "TransformShape",
    "Value",
    "check_point_reference",
    "child_lists",
    "container_of",
    "default_shape",
    "find",
    "frame_of",
    "kind_class",
    "map_expressions",
    "node_at",
    "offset_value",
    "own_strings",
    "paths",
    "placement_of",
    "point_dependencies",
    "point_names",
    "point_values",
    "references",
    "rename_node_references",
    "rewrite",
    "to_ictrans",
    "transform_of",
    "translated",
    "visible_from",
    "walk",
    "wrap_shapes",
]
