"""What every shape kind is built on: the node base class, alignment and modifiers.

A kind is a pydantic model deriving from :class:`Node` (usually through
:class:`Primitive` or :class:`Operation`) that renders itself, moves, lists
its children and describes itself for the GUI. The kinds are listed in
:mod:`mems_sketch.core.shapes.kinds`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

import klayout.db as kdb
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mems_sketch.core.expressions import evaluate
from mems_sketch.core.shapes.modifiers import AnyModifier, ArrayModifier

if TYPE_CHECKING:
    from mems_sketch.core.component import Component, Geometry
    from mems_sketch.core.shapes.points import NodePoints
    from mems_sketch.core.shapes.registry import Shape

Value = float | str  # a number or an expression
Point = tuple[float, float]
INDEX_NAMES = ("i", "j")


def check_point_reference(reference: str) -> str:
    """``reference`` must look like ``node.point``."""
    node, sep, point = reference.partition(".")
    if not sep or not node.isidentifier() or not point.isidentifier():
        raise ValueError(f"'{reference}' is not a point reference like 'mass.top'")
    return reference


# ``repeat`` was a field of its own before modifiers existed; a Repeat is an array.
Repeat = ArrayModifier


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


@dataclass(frozen=True)
class RenderContext:
    """What a kind needs to render one copy of itself."""

    variables: dict[str, float]  # parameters, point coordinates and the array indices
    scope: Mapping[str, NodePoints]  # the named nodes it can see
    lookup: Callable[[str], Component]  # components by name, for references
    render_lists: Callable[[list[list[Shape]], Mapping[str, NodePoints]], list[Geometry]]

    def ev(self, value: Value) -> float:
        return evaluate(value, self.variables)

    def children(
        self, lists: list[list[Shape]], scope: Mapping[str, NodePoints] | None = None
    ) -> list[Geometry]:
        """Geometry of each child list; ``scope`` replaces the visible points if given."""
        return self.render_lists(lists, self.scope if scope is None else scope)


class Node(BaseModel):
    """The fields every node has, and the methods a kind overrides."""

    model_config = ConfigDict(extra="forbid")

    category: ClassVar[str]  # "primitive", "operation", "reference" or "guide"
    icon: ClassVar[str] = "point"  # its icon in the GUI
    child_fields: ClassVar[tuple[str, ...]] = ()  # fields holding lists of child nodes
    placed: ClassVar[bool] = False  # has x, y, rotation and mirroring; see placement()
    wraps: ClassVar[tuple[str, ...]] = ()  # operations that create it around shapes
    cuts: ClassVar[bool] = False  # can split material into separate pieces

    name: str | None = None  # stable handle for the GUI and scripts
    modifiers: list[AnyModifier] = Field(default_factory=list)  # applied first to last
    align: Align | None = None
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _not_self(cls, name: str | None) -> str | None:
        if name == "self":
            raise ValueError("'self' is reserved: modifiers use it for the shape itself")
        return name

    @model_validator(mode="before")
    @classmethod
    def _repeat_is_an_array(cls, data: Any) -> Any:
        """``repeat`` (the earlier field, still accepted) sets the first array modifier."""
        if not isinstance(data, dict) or "repeat" not in data:
            return data
        data = dict(data)
        repeat = data.pop("repeat")
        modifiers = list(data.get("modifiers") or [])
        index = next((k for k, m in enumerate(modifiers) if _modifier_kind(m) == "array"), None)
        if repeat is not None:
            array = repeat.model_dump() if isinstance(repeat, BaseModel) else dict(repeat)
            array["kind"] = "array"
            if index is None:
                modifiers.insert(0, array)
            else:
                modifiers[index] = array
        elif index is not None:
            del modifiers[index]
        data["modifiers"] = modifiers
        return data

    @property
    def repeat(self) -> ArrayModifier | None:
        """The first array modifier (what ``repeat`` used to be), or None."""
        return next((m for m in self.modifiers if isinstance(m, ArrayModifier)), None)

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        """Geometry of one copy, and the points it declares (only references declare any)."""
        raise NotImplementedError

    def child_lists(self) -> list[list[Shape]]:
        return [getattr(self, field) for field in self.child_fields]

    def moved(
        self,
        x: Callable[[Value], Value],
        y: Callable[[Value], Value],
        inner: Callable[[list[Shape]], list[Shape]],
    ) -> dict[str, Any]:
        """Fields changed by a move: ``x`` and ``y`` move one coordinate, ``inner`` a child list."""
        return {field: inner(getattr(self, field)) for field in self.child_fields}

    def placement(self, variables: dict[str, float]) -> kdb.DCplxTrans | None:
        """The transform a ``placed`` kind applies to its content, in µm."""
        return None

    def summary(self) -> str:
        """Short description of the node, e.g. for a tooltip or a listing."""
        return self.kind

    def detail(self) -> str:
        """The shortest description, for next to the name where an icon shows the kind."""
        return self.summary()

    def icon_name(self) -> str:
        return self.icon

    @classmethod
    def kind_name(cls) -> str:
        return cls.model_fields["kind"].default

    @classmethod
    def default(cls, layer: str) -> Shape:
        """A new shape of this kind to start editing from (primitives)."""
        raise TypeError(f"'{cls.kind_name()}' has no default shape")

    @classmethod
    def wrap(cls, op: str, name: str, nodes: list[Shape]) -> Shape:
        """A new node of this kind holding ``nodes`` (kinds that list ``op`` in ``wraps``)."""
        raise TypeError(f"'{cls.kind_name()}' cannot wrap shapes")


class Primitive(Node):
    """A leaf drawn on one layer (subclasses declare ``layer``)."""

    category: ClassVar[str] = "primitive"

    def summary(self) -> str:
        return f"{self.kind} · {self.layer}"

    def detail(self) -> str:
        return self.layer


class Operation(Node):
    """An inner node made from its children's geometry."""

    category: ClassVar[str] = "operation"
    child_fields: ClassVar[tuple[str, ...]] = ("children",)


def _modifier_kind(modifier: Any) -> str | None:
    if isinstance(modifier, dict):
        return modifier.get("kind")
    return getattr(modifier, "kind", None)
