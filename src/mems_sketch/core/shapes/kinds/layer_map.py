"""Children's geometry moved between layers."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

from pydantic import model_validator

from mems_sketch.core.component import Geometry
from mems_sketch.core.shapes.base import Operation, Point, RenderContext

if TYPE_CHECKING:
    from mems_sketch.core.shapes.registry import Shape


class LayerMapShape(Operation):
    """Move children's geometry between layers.

    ``mapping`` sends source layer -> target layer; several sources may merge
    into one target. Layers not in ``mapping`` are dropped unless
    ``keep_unmapped`` is set.
    """

    icon: ClassVar[str] = "layer_map"
    wraps: ClassVar[tuple[str, ...]] = ("layer_map",)

    kind: Literal["layer_map"] = "layer_map"
    children: list[Shape]
    mapping: dict[str, str]
    keep_unmapped: bool = False

    @model_validator(mode="after")
    def _not_empty(self):
        if not self.mapping:
            raise ValueError("layer_map needs at least one mapping")
        return self

    def render(self, ctx: RenderContext) -> tuple[Geometry, dict[str, Point]]:
        geometry = Geometry()
        for layer, region in ctx.children([self.children])[0].layers.items():
            target = self.mapping.get(layer, layer if self.keep_unmapped else None)
            if target is not None:
                geometry.region(target).insert(region)
        return geometry, {}

    def summary(self) -> str:
        return "layers " + ", ".join(f"{a}→{b}" for a, b in self.mapping.items())

    @classmethod
    def wrap(cls, op: str, name: str, nodes: list[Shape]) -> LayerMapShape:
        layers = sorted(_layers(nodes))
        return cls(
            name=name,
            mapping={layer: layer for layer in layers} or {"device": "device"},
            children=nodes,
        )


def _layers(nodes: list[Shape]) -> set[str]:
    found = set()
    for node in nodes:
        if (layer := getattr(node, "layer", None)) is not None:
            found.add(layer)
        for children in node.child_lists():
            found |= _layers(children)
    return found
