"""Shape models: the nodes of the parametric shape tree."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

Value = float | str  # a number or an expression
INDEX_NAMES = ("i", "j")
Point = tuple[float, float]


def check_point_reference(reference: str) -> str:
    """``reference`` must look like ``node.point``."""
    node, sep, point = reference.partition(".")
    if not sep or not node.isidentifier() or not point.isidentifier():
        raise ValueError(f"'{reference}' is not a point reference like 'mass.top'")
    return reference


class Repeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: Value = 1
    rows: Value = 1
    dx: Value = 0.0
    dy: Value = 0.0


class Align(BaseModel):
    """Move a node so that its ``point`` lands on ``to`` (``node.point``), plus ``dx``, ``dy``.

    The alignment is kept: it is re-evaluated whenever anything changes. The
    node's own position (``x``, ``y`` of a reference or transform) then only
    matters through rotation and mirroring.
    """

    model_config = ConfigDict(extra="forbid")

    point: str = "center"
    to: str
    dx: Value = 0.0
    dy: Value = 0.0

    @field_validator("point")
    @classmethod
    def _point_name(cls, point: str) -> str:
        if not point.isidentifier():
            raise ValueError(f"'{point}' is not a point name")
        return point

    @field_validator("to")
    @classmethod
    def _reference(cls, to: str) -> str:
        return check_point_reference(to)


class _Node(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None  # stable handle for the GUI and scripts
    align: Align | None = None
    enabled: bool = True
    repeat: Repeat | None = None


# -- primitives ------------------------------------------------------------


class RectShape(_Node):
    kind: Literal["rect"] = "rect"
    layer: str
    x0: Value
    y0: Value
    x1: Value
    y1: Value


class PolygonShape(_Node):
    kind: Literal["polygon"] = "polygon"
    layer: str
    points: list[tuple[Value, Value]] = Field(min_length=3)


class CircleShape(_Node):
    kind: Literal["circle"] = "circle"
    layer: str
    x: Value = 0.0
    y: Value = 0.0
    radius: Value
    segments: Value | None = None  # default: from ARC_TOLERANCE_UM


class ArcShape(_Node):
    """Annular sector (a ring when the angles span 360°). Angles in degrees, CCW from +x."""

    kind: Literal["arc"] = "arc"
    layer: str
    x: Value = 0.0
    y: Value = 0.0
    inner_radius: Value = 0.0
    outer_radius: Value
    start_angle: Value = 0.0
    end_angle: Value = 360.0
    segments: Value | None = None  # for a full circle; scaled by the swept angle


class PathShape(_Node):
    """A wire of constant ``width`` along a centreline, e.g. a beam or a trace."""

    kind: Literal["path"] = "path"
    layer: str
    points: list[tuple[Value, Value]] = Field(min_length=2)
    width: Value
    ends: Literal["flush", "square", "round"] = "flush"


class RefShape(_Node):
    """An instance of a built-in or user-defined component."""

    kind: Literal["ref"] = "ref"
    component: str
    params: dict[str, Value] = Field(default_factory=dict)
    x: Value = 0.0
    y: Value = 0.0
    rotation: Value = 0.0  # degrees, counter-clockwise
    mirror_x: bool = False


# -- operations ------------------------------------------------------------


class TransformShape(_Node):
    """Mirror ``children`` about x, scale, rotate and move them as one piece.

    For something reusable, make a component instead; a transform is for
    moving a few shapes together once. Files written before the rename used
    ``kind: group``, which is still read.
    """

    kind: Literal["transform", "group"] = "transform"
    children: list[Shape] = Field(default_factory=list)
    x: Value = 0.0
    y: Value = 0.0
    rotation: Value = 0.0
    mirror_x: bool = False
    scale: Value = 1.0

    @field_validator("kind")
    @classmethod
    def _current_kind(cls, kind: str) -> str:
        return "transform"


GroupShape = TransformShape  # the earlier name


class BooleanShape(_Node):
    kind: Literal["boolean"] = "boolean"
    op: Literal["union", "subtract", "intersect", "xor"]
    a: list[Shape]
    b: list[Shape]


class OffsetShape(_Node):
    """Grow (positive ``distance``) or shrink (negative) the children's outlines."""

    kind: Literal["offset"] = "offset"
    children: list[Shape]
    distance: Value
    corners: Literal["square", "bevel"] = "square"


class FilletShape(_Node):
    """Round corners: ``radius`` for convex corners, ``inner_radius`` for concave ones."""

    kind: Literal["fillet"] = "fillet"
    children: list[Shape]
    radius: Value = 0.0
    inner_radius: Value = 0.0
    segments: Value | None = None  # per full circle; default from ARC_TOLERANCE_UM


class LayerMapShape(_Node):
    """Move children's geometry between layers.

    ``mapping`` sends source layer -> target layer; several sources may merge
    into one target. Layers not in ``mapping`` are dropped unless
    ``keep_unmapped`` is set.
    """

    kind: Literal["layer_map"] = "layer_map"
    children: list[Shape]
    mapping: dict[str, str]
    keep_unmapped: bool = False

    @model_validator(mode="after")
    def _not_empty(self):
        if not self.mapping:
            raise ValueError("layer_map needs at least one mapping")
        return self


Shape = Annotated[
    RectShape
    | PolygonShape
    | CircleShape
    | ArcShape
    | PathShape
    | RefShape
    | TransformShape
    | BooleanShape
    | OffsetShape
    | FilletShape
    | LayerMapShape,
    Field(discriminator="kind"),
]

for _model in (TransformShape, BooleanShape, OffsetShape, FilletShape, LayerMapShape):
    _model.model_rebuild()

PRIMITIVE_KINDS = ("rect", "polygon", "circle", "arc", "path")

SHAPE_ADAPTER: TypeAdapter = TypeAdapter(Shape)
