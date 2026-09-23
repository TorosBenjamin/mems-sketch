"""Lateral etch loss (undercut) per layer.

``undercut`` is the material removed from every edge during etching. Two
directions are useful:

* :func:`etched` predicts the as-fabricated shape from the drawn shape.
* :func:`compensated` biases the drawn shape so that it etches to the drawn size.
"""

from __future__ import annotations

from mems_sketch.core.component import Geometry, to_dbu
from mems_sketch.core.project import Project


def etched(project: Project, drawn: Geometry | None = None) -> Geometry:
    return _sized(project, drawn, sign=-1)


def compensated(project: Project, drawn: Geometry | None = None) -> Geometry:
    return _sized(project, drawn, sign=+1)


def _sized(project: Project, drawn: Geometry | None, sign: int) -> Geometry:
    drawn = project.render() if drawn is None else drawn
    result = Geometry()
    for name, region in drawn.layers.items():
        layer = project.layers.get(name)
        bias = to_dbu(layer.undercut) if layer else 0
        result.layers[name] = region.sized(sign * bias) if bias else region.dup()
    return result
