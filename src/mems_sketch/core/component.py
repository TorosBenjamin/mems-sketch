"""Parametric components: typed parameters in, polygons per layer out.

A component never stores geometry. It is a pure function of its parameters,
which is what keeps a design parametric and makes the GUI and scripting modes
share one code path.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, ClassVar, NamedTuple

import klayout.db as kdb
from pydantic import BaseModel, ConfigDict

from mems_sketch.core.expressions import evaluate
from mems_sketch.core.transform import Transform

DBU_UM = 0.001  # database unit: 1 nm, in micrometres


def to_dbu(value_um: float) -> int:
    return round(value_um / DBU_UM)


Ring = list[tuple[float, float]]  # a closed outline in µm, without repeating its first point


class Polygon(NamedTuple):
    """A merged polygon in µm: its outline and the outlines of its holes."""

    hull: Ring
    holes: list[Ring]


class Geometry:
    """Polygons grouped by layer name. Coordinates are given in micrometres.

    Code outside the geometry backend (the GUI, editing, storage) uses only
    these methods, never the regions in :attr:`layers`, so that the backend
    can change underneath it.
    """

    def __init__(self) -> None:
        self.layers: dict[str, kdb.Region] = {}

    def region(self, layer: str) -> kdb.Region:
        return self.layers.setdefault(layer, kdb.Region())

    def add_layer(self, layer: str) -> None:
        """A layer, empty until something is added to it."""
        self.region(layer)

    def add_rect(self, layer: str, x0: float, y0: float, x1: float, y1: float) -> None:
        box = kdb.Box(
            to_dbu(min(x0, x1)), to_dbu(min(y0, y1)), to_dbu(max(x0, x1)), to_dbu(max(y0, y1))
        )
        self.region(layer).insert(box)

    def add_polygon(
        self,
        layer: str,
        points_um: Iterable[tuple[float, float]],
        holes: Iterable[Iterable[tuple[float, float]]] = (),
    ) -> None:
        polygon = kdb.Polygon(_points(points_um))
        for hole in holes:
            polygon.insert_hole(_points(hole))
        self.region(layer).insert(polygon)

    def merge(self, other: Geometry, transform: Transform | None = None) -> None:
        """Add ``other``'s polygons, placed by ``transform`` (snapped to the database grid)."""
        trans = None if transform is None or transform.is_identity else _ictrans(transform)
        for layer, region in other.layers.items():
            self.region(layer).insert(region.transformed(trans) if trans else region)

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
            region = kdb.Region()
            for r in self.layers.values():
                region.insert(r)
        else:
            region = self.layers.get(layer, kdb.Region())
        return [
            Polygon(
                _ring(polygon.each_point_hull()),
                [_ring(polygon.each_point_hole(h)) for h in range(polygon.holes())],
            )
            for polygon in region.each_merged()
        ]

    def bbox(self) -> tuple[float, float, float, float] | None:
        """``(left, bottom, right, top)`` in µm of all layers, or None when empty."""
        box = kdb.Box()
        for region in self.layers.values():
            box += region.bbox()
        if box.empty():
            return None
        return box.left * DBU_UM, box.bottom * DBU_UM, box.right * DBU_UM, box.top * DBU_UM

    def touches(self, x: float, y: float, reach: float = 0.0) -> bool:
        """Some polygon is within ``reach`` µm of the point (a square around it, at
        least one database unit wide)."""
        r = max(1, to_dbu(reach))
        cx, cy = to_dbu(x), to_dbu(y)
        probe = kdb.Region(kdb.Box(cx - r, cy - r, cx + r, cy + r))
        return any(not (region & probe).is_empty() for region in self.layers.values())

    def pieces(self) -> int:
        """How many separate pieces the geometry has, all layers together."""
        region = kdb.Region()
        for r in self.layers.values():
            region.insert(r)
        return region.merged().count()

    def difference(self, other: Geometry) -> Geometry:
        """What is in this geometry and not in ``other``, layer by layer (merged)."""
        result = Geometry()
        for name, region in self.layers.items():
            rest = (region - other.layers.get(name, kdb.Region())).merged()
            if not rest.is_empty():
                result.layers[name] = rest
        return result


def _points(points_um: Iterable[tuple[float, float]]) -> list[kdb.Point]:
    return [kdb.Point(to_dbu(x), to_dbu(y)) for x, y, *_ in points_um]


def _ring(points: Iterable[kdb.Point]) -> Ring:
    return [(p.x * DBU_UM, p.y * DBU_UM) for p in points]


def _ictrans(transform: Transform) -> kdb.ICplxTrans:
    return kdb.ICplxTrans(
        transform.mag,
        transform.angle,
        transform.mirror,
        to_dbu(transform.dx),
        to_dbu(transform.dy),
    )


class Params(BaseModel):
    """Base class for component parameters. Lengths are in micrometres."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Component:
    """Subclass, set ``type_name`` and ``Params``, implement :meth:`build`, then register."""

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

    def build(self, params: Params) -> Geometry:
        raise NotImplementedError

    def points(self, params: Params) -> dict[str, tuple[float, float]]:
        """Named alignment points in the component's own coordinates (µm).

        Override to offer points besides the bounding-box ones every shape
        has, e.g. where a spring attaches. Names must not be bounding-box
        point names (``center``, ``top``, ``bottom_left``, ...).
        """
        return {}

    def compile(
        self, params: Params, level: str | None = None
    ) -> tuple[Geometry, dict[str, tuple[float, float]]]:
        """Geometry and alignment points together (one evaluation for user components),
        on a level of the layer stack (which components with fixed layers ignore)."""
        return self.build(params), self.points(params)


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


_REGISTRY: dict[str, type[Component]] = {}


def register_component(cls: type[Component]) -> type[Component]:
    if cls.type_name in _REGISTRY:
        raise ValueError(f"component type '{cls.type_name}' is already registered")
    _REGISTRY[cls.type_name] = cls
    return cls


def is_builtin(type_name: str) -> bool:
    _ensure_builtin_components()
    return type_name in _REGISTRY


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
