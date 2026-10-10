"""Parametric components: typed parameters in, polygons per layer out.

A component never stores geometry. It is a pure function of its parameters,
which is what keeps a design parametric and makes the GUI and scripting modes
share one code path.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, ClassVar, NamedTuple

import numpy as np
from pydantic import BaseModel, ConfigDict

from mems_sketch.core.expressions import evaluate
from mems_sketch.core.region import DBU_UM, Box, IntPolygon, Region
from mems_sketch.core.transform import Transform


def to_dbu(value_um: float) -> int:
    return round(value_um / DBU_UM)


Ring = np.ndarray  # a closed outline in µm, (n, 2) floats, without repeating its first point


class Polygon(NamedTuple):
    """A merged polygon in µm: its outline and the outlines of its holes
    (``.tolist()`` gives plain pairs)."""

    hull: Ring
    holes: list[Ring]


class Geometry:
    """Polygons grouped by layer name, on the 1 nm grid; coordinates in µm.

    Code outside the geometry backend (the GUI, editing, storage) uses only
    these methods, never the regions in :attr:`layers` (:mod:`mems_sketch.core.region`).
    """

    def __init__(self) -> None:
        self.layers: dict[str, Region] = {}

    def region(self, layer: str) -> Region:
        return self.layers.setdefault(layer, Region())

    def add_layer(self, layer: str) -> None:
        """A layer, empty until something is added to it."""
        self.region(layer)

    def add_rect(self, layer: str, x0: float, y0: float, x1: float, y1: float) -> None:
        self.region(layer).insert(Box(to_dbu(x0), to_dbu(y0), to_dbu(x1), to_dbu(y1)))

    def add_polygon(
        self,
        layer: str,
        points_um: Iterable[tuple[float, float]],
        holes: Iterable[Iterable[tuple[float, float]]] = (),
    ) -> None:
        self.region(layer).insert(IntPolygon(_points(points_um), [_points(hole) for hole in holes]))

    def merge(self, other: Geometry, transform: Transform | None = None) -> None:
        """Add ``other``'s polygons, placed by ``transform`` (snapped to the database grid)."""
        for layer, region in other.layers.items():
            placed = region if transform is None else region.transformed(transform)
            self.region(layer).insert(placed)

    def transformed(self, transform: Transform) -> Geometry:
        result = Geometry()
        result.merge(self, transform)
        return result

    def merged(self) -> Geometry:
        result = Geometry()
        result.layers = {name: region.merged() for name, region in self.layers.items()}
        return result

    def only(self, layers: Iterable[str]) -> Geometry:
        """The geometry on the given layers."""
        result = Geometry()
        result.layers = {name: self.layers[name] for name in layers if name in self.layers}
        return result

    def is_empty(self) -> bool:
        return all(region.is_empty() for region in self.layers.values())

    def layer_names(self) -> list[str]:
        """The layers with geometry."""
        return [name for name, region in self.layers.items() if not region.is_empty()]

    def polygons(self, layer: str | None = None) -> list[Polygon]:
        """The merged polygons of a layer, or of all layers together."""
        if layer is None:
            region = Region()
            for r in self.layers.values():
                region.insert(r)
        else:
            region = self.layers.get(layer, Region())
        return [Polygon(hull, holes) for hull, holes in region.points_um()]

    def bbox(self) -> tuple[float, float, float, float] | None:
        """``(left, bottom, right, top)`` in µm of all layers, or None when empty."""
        box = Box()
        for region in self.layers.values():
            box = box + region.bbox()
        if box.empty():
            return None
        return box.left * DBU_UM, box.bottom * DBU_UM, box.right * DBU_UM, box.top * DBU_UM

    def touches(self, x: float, y: float, reach: float = 0.0) -> bool:
        """Some polygon is within ``reach`` µm of the point (at least one database
        unit)."""
        r = max(1, to_dbu(reach))
        cx, cy = to_dbu(x), to_dbu(y)
        for region in self.layers.values():
            box = region.bbox()
            if box.empty() or not (
                box.left - r <= cx <= box.right + r and box.bottom - r <= cy <= box.top + r
            ):
                continue
            for polygon in region.each():
                b = polygon.bbox()
                if (
                    b.left - r <= cx <= b.right + r
                    and b.bottom - r <= cy <= b.top + r
                    and polygon.near(cx, cy, r)
                ):
                    return True
        return False

    def point_count(self) -> int:
        """How many points the polygons have, all layers together (a measure of
        how much work the geometry is)."""
        return sum(p.num_points() for region in self.layers.values() for p in region.each())

    def pieces(self) -> int:
        """How many separate pieces the geometry has, all layers together."""
        region = Region()
        for r in self.layers.values():
            region.insert(r)
        return region.merged().count()

    def difference(self, other: Geometry) -> Geometry:
        """What is in this geometry and not in ``other``, layer by layer (merged)."""
        result = Geometry()
        for name, region in self.layers.items():
            rest = region - other.layers.get(name, Region())
            if not rest.is_empty():
                result.layers[name] = rest
        return result


def _points(points_um: Iterable[tuple[float, float]]) -> list[tuple[int, int]]:
    return [(to_dbu(x), to_dbu(y)) for x, y, *_ in points_um]


class Params(BaseModel):
    """Base class for component parameters. Lengths are in micrometres."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Component:
    """Something a ``ref`` can place: set ``type_name`` and ``Params``, implement
    :meth:`build` (user components, imported cells)."""

    type_name: ClassVar[str]
    Params: ClassVar[type[Params]]
    # Parameters only the component itself uses: a placement cannot set them.
    internal: frozenset[str] = frozenset()
    # The level of the layer stack it is on unless placed elsewhere (None: the
    # level of whatever places it); see mems_sketch.core.levels.
    default_level: str | None = None

    def public_params(self) -> dict[str, Any]:
        """The parameters a placement may set (name -> pydantic field info)."""
        return {k: v for k, v in self.Params.model_fields.items() if k not in self.internal}

    def check_placement(self, names: Iterable[str]) -> None:
        """Refuse values given where the component is placed for its internal parameters."""
        hidden = sorted(set(names) & self.internal)
        if hidden:
            listed = ", ".join(f"'{n}'" for n in hidden)
            raise ValueError(
                f"{listed} of component '{self.type_name}' "
                f"{'is' if len(hidden) == 1 else 'are'} internal: "
                "it cannot be set where the component is placed"
            )


def resolve_params(
    component: Component, raw: Mapping[str, Any], variables: Mapping[str, float]
) -> Params:
    """Evaluate expression-valued parameters and validate them against the component schema.

    Strings are treated as expressions except for fields declared as ``str``
    (e.g. a layer name), which are passed through unchanged.
    """
    fields = component.Params.model_fields
    values: dict[str, Any] = {}
    for key, value in raw.items():
        is_text = key in fields and fields[key].annotation is str
        values[key] = value if is_text or not isinstance(value, str) else evaluate(value, variables)
    return component.Params(**values)


def builtin_definitions() -> dict:
    """The built-in components' definitions by name (mems_sketch.components)."""
    from mems_sketch.components import builtin_components

    return builtin_components()


def is_builtin(name: str) -> bool:
    return "/" not in name and name in builtin_definitions()


def component_types() -> list[str]:
    """The built-in components' names."""
    return sorted(n for n in builtin_definitions() if "/" not in n)
