"""The shape union and lookups by kind, built from :data:`kinds.KINDS`."""

from __future__ import annotations

import functools
import operator
from typing import Annotated, get_args

from pydantic import Field, TypeAdapter

from mems_sketch.core.shapes.base import Node
from mems_sketch.core.shapes.kinds import KINDS

Shape = Annotated[functools.reduce(operator.or_, KINDS), Field(discriminator="kind")]
for _kind in KINDS:  # kinds with children refer to Shape, which exists only now
    _kind.model_rebuild(_types_namespace={"Shape": Shape})

SHAPE_ADAPTER: TypeAdapter = TypeAdapter(Shape)
# Every accepted ``kind`` value, including old ones such as ``group``.
BY_KIND: dict[str, type[Node]] = {
    name: kind for kind in KINDS for name in get_args(kind.model_fields["kind"].annotation)
}
PRIMITIVE_KINDS = tuple(kind.kind_name() for kind in KINDS if kind.category == "primitive")
WRAPPERS: dict[str, type[Node]] = {op: kind for kind in KINDS for op in kind.wraps}


def kind_class(kind: str) -> type[Node]:
    try:
        return BY_KIND[kind]
    except KeyError:
        raise KeyError(f"unknown shape kind '{kind}'") from None


def default_shape(kind: str, layer: str = "device") -> Shape:
    """A new primitive of ``kind`` to start editing from."""
    cls = BY_KIND.get(kind)
    if cls is None or cls.category != "primitive":
        raise ValueError(f"unknown primitive '{kind}'")
    return cls.default(layer)


def wrap_shapes(operation: str, name: str, nodes: list[Shape]) -> Shape:
    """A new operation node named ``name`` holding ``nodes``."""
    cls = WRAPPERS.get(operation)
    if cls is None:
        raise ValueError(f"unknown operation '{operation}'")
    return cls.wrap(operation, name, nodes)
