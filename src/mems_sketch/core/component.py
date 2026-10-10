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

    A geometry has polygons of its own and may place other geometries whole
    (:attr:`instances`: a component placed several times is one geometry,
    placed several times). :attr:`layers` is everything flattened and merged,
    worked out when first asked for; :attr:`own` is its own polygons only.

    Code outside the geometry backend (the GUI, editing, storage) uses only
    these methods, never the regions in :attr:`layers` (:mod:`mems_sketch.core.region`).
    """

    def __init__(self) -> None:
        self._own: dict[str, Region] = {}
        self.instances: list[tuple[Geometry, Transform]] = []
        self._flat: dict[str, Region] | None = None
        self._box: tuple[float, float, float, float] | None | bool = False  # False: not known

    def __getstate__(self) -> dict:
        # Without what is worked out from it (sent to a rule-check process).
        return {"_own": self._own, "instances": self.instances}

    def __setstate__(self, state: dict) -> None:
        self._own, self.instances = state["_own"], state["instances"]
        self._flat, self._box = None, False

    # -- what it holds ---------------------------------------------------------

    @property
    def own(self) -> dict[str, Region]:
        """Its own polygons per layer, without what it places."""
        return self._own

    @property
    def layers(self) -> dict[str, Region]:
        """Everything per layer: its own polygons and what it places, merged."""
        if not self.instances:
            return self._own
        if self._flat is None:
            self._flat = self._flatten()
        return self._flat

    @layers.setter
    def layers(self, layers: dict[str, Region]) -> None:
        self._own, self.instances = layers, []
        self._changed()

    def _changed(self) -> None:
        self._flat, self._box = None, False

    def _flatten(self) -> dict[str, Region]:
        parts: dict[str, list] = {}
        for layer, region in self._own.items():
            parts.setdefault(layer, []).extend(region.each())
        for geometry, transform in self.instances:
            for layer, region in geometry.layers.items():
                parts.setdefault(layer, []).extend(region.transformed(transform).each())
        return {layer: Region.from_polygons(polygons).merged() for layer, polygons in parts.items()}

    def place(self, geometry: Geometry, transform: Transform | None = None) -> None:
        """Place ``geometry`` whole (kept as an instance, not copied)."""
        self.instances.append((geometry, transform or Transform()))
        self._changed()

    def leaves(self, transform: Transform | None = None) -> list[tuple[Geometry, Transform]]:
        """Each geometry with polygons of its own, with where it ends up: this one
        and, through its instances, everything it places (for drawing each once)."""
        at = transform or Transform()
        result = [(self, at)] if any(not r.is_empty() for r in self._own.values()) else []
        for geometry, placement in self.instances:
            result += geometry.leaves(at * placement)
        return result

    # -- building ----------------------------------------------------------------

    def region(self, layer: str) -> Region:
        self._changed()
        return self._own.setdefault(layer, Region())

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
        """Add ``other``'s polygons, placed by ``transform`` (snapped to the database
        grid); what it places stays placed."""
        for layer, region in other._own.items():
            placed = region if transform is None else region.transformed(transform)
            self.region(layer).insert(placed)
        for geometry, placement in other.instances:
            self.place(geometry, placement if transform is None else transform * placement)
        self._changed()

    def transformed(self, transform: Transform) -> Geometry:
        result = Geometry()
        result.merge(self, transform)
        return result

    def merged(self) -> Geometry:
        """Everything flattened into one geometry, merged per layer."""
        result = Geometry()
        result.layers = {name: region.merged() for name, region in self.layers.items()}
        return result

    def only(self, layers: Iterable[str]) -> Geometry:
        """The geometry on the given layers."""
        keep = set(layers)
        result = Geometry()
        result._own = {name: region for name, region in self._own.items() if name in keep}
        for geometry, placement in self.instances:
            part = geometry.only(keep)
            if not part.is_empty():
                result.instances.append((part, placement))
        return result

    # -- measuring ---------------------------------------------------------------

    def is_empty(self) -> bool:
        return all(region.is_empty() for region in self._own.values()) and all(
            geometry.is_empty() for geometry, _ in self.instances
        )

    def layer_names(self) -> list[str]:
        """The layers with geometry."""
        names = [name for name, region in self._own.items() if not region.is_empty()]
        for geometry, _ in self.instances:
            names += [n for n in geometry.layer_names() if n not in names]
        return names

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
        """``(left, bottom, right, top)`` in µm of all layers, or None when empty
        (worked out without flattening what it places)."""
        if self._box is False:
            box = Box()
            for region in self._own.values():
                box = box + region.bbox()
            boxes = (
                []
                if box.empty()
                else [
                    (box.left * DBU_UM, box.bottom * DBU_UM, box.right * DBU_UM, box.top * DBU_UM)
                ]
            )
            for geometry, placement in self.instances:
                placed = _placed_box(geometry, placement)
                if placed is not None:
                    boxes.append(placed)
            self._box = (
                None
                if not boxes
                else (
                    min(b[0] for b in boxes),
                    min(b[1] for b in boxes),
                    max(b[2] for b in boxes),
                    max(b[3] for b in boxes),
                )
            )
        return self._box

    def touches(self, x: float, y: float, reach: float = 0.0) -> bool:
        """Some polygon is within ``reach`` µm of the point (at least one database
        unit); what it places is looked at where it is, not flattened."""
        box = self.bbox()
        if (
            box is None
            or not (box[0] - reach - DBU_UM <= x <= box[2] + reach + DBU_UM)
            or not (box[1] - reach - DBU_UM <= y <= box[3] + reach + DBU_UM)
        ):
            return False
        r = max(1, to_dbu(reach))
        cx, cy = to_dbu(x), to_dbu(y)
        for region in self._own.values():
            b = region.bbox()
            if b.empty() or not (
                b.left - r <= cx <= b.right + r and b.bottom - r <= cy <= b.top + r
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
        for geometry, placement in self.instances:
            px, py = placement.inverted().apply(x, y)
            if geometry.touches(px, py, reach / placement.mag):
                return True
        return False

    def point_count(self) -> int:
        """How many points the polygons have, all layers together and every placed
        copy counted (a measure of how much work the flattened geometry is)."""
        own = sum(p.num_points() for region in self._own.values() for p in region.each())
        return own + sum(geometry.point_count() for geometry, _ in self.instances)

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


def _placed_box(
    geometry: Geometry, placement: Transform
) -> tuple[float, float, float, float] | None:
    """The box around ``geometry`` where ``placement`` puts it: its own box's
    corners for quarter turns, else the box of the turned geometry."""
    box = geometry.bbox()
    if box is None:
        return None
    if placement.angle % 90:
        turned = geometry.transformed(placement)
        turned.layers = turned.layers  # flattened
        return turned.bbox()
    x0, y0, x1, y1 = box
    corners = [placement.apply(x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    return min(xs), min(ys), max(xs), max(ys)


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
