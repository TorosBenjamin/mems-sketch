"""Lateral etch loss (undercut) per layer.

``undercut`` is the material removed from every edge during etching. Two
directions are useful:

* :func:`etched` predicts the as-fabricated shape from the drawn shape.
* :func:`compensated` biases the drawn shape so that it etches to the drawn size.
"""

from __future__ import annotations

from mems_sketch.core.component import Geometry, to_dbu
from mems_sketch.core.design import Design


def etched(design: Design, drawn: Geometry | None = None) -> Geometry:
    return _sized(design, drawn, sign=-1)


def compensated(design: Design, drawn: Geometry | None = None) -> Geometry:
    return _sized(design, drawn, sign=+1)


def _sized(design: Design, drawn: Geometry | None, sign: int) -> Geometry:
    drawn = design.render() if drawn is None else drawn
    result = Geometry()
    for name, region in drawn.layers.items():
        layer = design.layers.get(name)
        bias = to_dbu(layer.undercut) if layer else 0
        result.layers[name] = region.sized(sign * bias) if bias else region.dup()
    return result
