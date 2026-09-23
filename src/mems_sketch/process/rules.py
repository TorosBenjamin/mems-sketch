"""Design-rule checks: minimum width and spacing per layer, plus unknown layers."""

from __future__ import annotations

from dataclasses import dataclass

import klayout.db as kdb

from mems_sketch.core.component import DBU_UM, Geometry, to_dbu
from mems_sketch.core.design import Design


@dataclass(frozen=True)
class Violation:
    rule: str
    layer: str
    message: str
    bbox_um: tuple[float, float, float, float] | None = None  # for highlighting in the GUI


def check(design: Design, geometry: Geometry | None = None) -> list[Violation]:
    """Check ``geometry`` (default: the drawn design) against the layer rules."""
    geometry = design.render() if geometry is None else geometry
    violations: list[Violation] = []
    for name, region in geometry.layers.items():
        layer = design.layers.get(name)
        if layer is None:
            violations.append(Violation("layer", name, f"layer '{name}' is not defined"))
            continue
        if layer.min_width is not None:
            pairs = region.width_check(to_dbu(layer.min_width))
            violations += _from_pairs(pairs, "min_width", name, f"width < {layer.min_width} µm")
        if layer.min_space is not None:
            pairs = region.space_check(to_dbu(layer.min_space))
            violations += _from_pairs(pairs, "min_space", name, f"spacing < {layer.min_space} µm")
    return violations


def _from_pairs(pairs: kdb.EdgePairs, rule: str, layer: str, message: str) -> list[Violation]:
    result = []
    for pair in pairs.each():
        box = pair.bbox()
        bbox = (box.left * DBU_UM, box.bottom * DBU_UM, box.right * DBU_UM, box.top * DBU_UM)
        result.append(Violation(rule, layer, message, bbox))
    return result
