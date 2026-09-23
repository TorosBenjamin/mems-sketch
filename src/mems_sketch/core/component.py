"""Parametric components: typed parameters in, polygons per layer out.

A component never stores geometry. It is a pure function of its parameters,
which is what keeps a design parametric and makes the GUI and scripting modes
share one code path.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

import klayout.db as kdb
from pydantic import BaseModel, ConfigDict

DBU_UM = 0.001  # database unit: 1 nm, in micrometres


def to_dbu(value_um: float) -> int:
    return round(value_um / DBU_UM)


class Geometry:
    """Polygons grouped by layer name. Coordinates are given in micrometres."""

    def __init__(self) -> None:
        self.layers: dict[str, kdb.Region] = {}

    def region(self, layer: str) -> kdb.Region:
        return self.layers.setdefault(layer, kdb.Region())

    def add_rect(self, layer: str, x0: float, y0: float, x1: float, y1: float) -> None:
        box = kdb.Box(
            to_dbu(min(x0, x1)), to_dbu(min(y0, y1)), to_dbu(max(x0, x1)), to_dbu(max(y0, y1))
        )
        self.region(layer).insert(box)

    def add_polygon(self, layer: str, points_um: Iterable[tuple[float, float]]) -> None:
        points = [kdb.Point(to_dbu(x), to_dbu(y)) for x, y in points_um]
        self.region(layer).insert(kdb.Polygon(points))

    def merge(self, other: Geometry, transform: kdb.ICplxTrans | None = None) -> None:
        for layer, region in other.layers.items():
            self.region(layer).insert(region.transformed(transform) if transform else region)

    def merged(self) -> Geometry:
        result = Geometry()
        result.layers = {name: region.merged() for name, region in self.layers.items()}
        return result


class Params(BaseModel):
    """Base class for component parameters. Lengths are in micrometres."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Component:
    """Subclass, set ``type_name`` and ``Params``, implement :meth:`build`, then register."""

    type_name: ClassVar[str]
    Params: ClassVar[type[Params]]

    def build(self, params: Params) -> Geometry:
        raise NotImplementedError


_REGISTRY: dict[str, type[Component]] = {}


def register_component(cls: type[Component]) -> type[Component]:
    if cls.type_name in _REGISTRY:
        raise ValueError(f"component type '{cls.type_name}' is already registered")
    _REGISTRY[cls.type_name] = cls
    return cls


def get_component(type_name: str) -> Component:
    _ensure_builtin_components()
    try:
        return _REGISTRY[type_name]()
    except KeyError:
        raise KeyError(f"unknown component type '{type_name}'") from None


def component_types() -> list[str]:
    _ensure_builtin_components()
    return sorted(_REGISTRY)


def _ensure_builtin_components() -> None:
    import mems_sketch.components.library  # noqa: F401  (registers on import)
