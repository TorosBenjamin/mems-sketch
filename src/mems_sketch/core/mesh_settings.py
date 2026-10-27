"""How a component is meshed (requirement MSH-2, #50): project data like the rest
of the component, saved in its file, compared in diffs, and parametric: every
number may be an expression over the component's parameters and the process
constants.

In a component's file::

    mesh:
      size: 20                    # the largest element (µm); left out: automatic
      growth: 1.2                 # how much an element may be larger than the next
      layers: {anchor: 5}         # a size per layer
      automatic: true             # finer in narrow parts, gaps and curves (#73)
      across: 2                   # elements across the narrowest part of a beam or gap
      refine:
        - {kind: point, x: 0, y: "plate / 2", size: 1, radius: 3}
        - {kind: box, x0: -10, y0: 0, x1: 10, y1: 40, size: 2}
        - {kind: edges, layer: device, size: 0.5, distance: 1}

- A **point** refinement is finest within ``radius`` of the point.
- A **box** is finest inside it.
- **edges** is finest within ``distance`` of the edges of ``layer``.

Away from each of them the size grows by at most ``growth`` from one element to
the next (MSH-7, #52), up to the size of the layer it is on, then ``size``.

The **automatic size map** (``automatic``, on unless turned off) adds what the
geometry needs: about ``across`` elements across each narrow beam and each
small gap, and enough along tight curves (see :mod:`mems_sketch.mesh_sizes`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from mems_sketch.core.expressions import evaluate

Value = float | str
REFINEMENT_KINDS = ("point", "box", "edges")
DEFAULT_GROWTH = 1.2
DEFAULT_ACROSS = 2.0  # elements across the narrowest part, for the automatic size map
AUTOMATIC_DIVISIONS = 20  # automatic size: the component's smaller side over this


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Refinement(_Model):
    """A place the mesh is finer: see the module docstring for the kinds. Each kind
    knows its fields (``FIELDS``: name and label, for forms), how it is listed
    (:attr:`label`) and its numbers (:meth:`resolved`)."""

    FIELDS: ClassVar[tuple[tuple[str, str], ...]] = ()
    size: Value

    @property
    def label(self) -> str:  # pragma: no cover - each kind has its own
        raise NotImplementedError

    def resolved(self, number: Callable[[Value, str], float]) -> ResolvedRefinement:
        raise NotImplementedError  # pragma: no cover


class PointRefinement(_Refinement):
    """Finest within ``radius`` of the point (x, y)."""

    FIELDS = (("size", "Size"), ("x", "x"), ("y", "y"), ("radius", "Radius"))
    kind: Literal["point"] = "point"
    x: Value = 0.0
    y: Value = 0.0
    radius: Value = 0.0

    @property
    def label(self) -> str:
        return f"point ({_text(self.x)}, {_text(self.y)})"

    def resolved(self, number) -> ResolvedPoint:
        return ResolvedPoint(
            number(self.size, "size"),
            number(self.x, "x"),
            number(self.y, "y"),
            max(number(self.radius, "radius"), 0.0),
        )


class BoxRefinement(_Refinement):
    """Finest inside the box (x0, y0)–(x1, y1)."""

    FIELDS = (("size", "Size"), ("x0", "x0"), ("y0", "y0"), ("x1", "x1"), ("y1", "y1"))
    kind: Literal["box"] = "box"
    x0: Value = 0.0
    y0: Value = 0.0
    x1: Value = 0.0
    y1: Value = 0.0

    @property
    def label(self) -> str:
        corners = f"({_text(self.x0)}, {_text(self.y0)}) – ({_text(self.x1)}, {_text(self.y1)})"
        return f"box {corners}"

    def resolved(self, number) -> ResolvedBox:
        xs = sorted((number(self.x0, "x0"), number(self.x1, "x1")))
        ys = sorted((number(self.y0, "y0"), number(self.y1, "y1")))
        return ResolvedBox(number(self.size, "size"), (xs[0], ys[0], xs[1], ys[1]))


class EdgesRefinement(_Refinement):
    """Finest within ``distance`` of the edges of ``layer``."""

    FIELDS = (("size", "Size"), ("distance", "Within"))
    kind: Literal["edges"] = "edges"
    layer: str
    distance: Value = 0.0

    @property
    def label(self) -> str:
        return f"edges of {self.layer}"

    def resolved(self, number) -> ResolvedEdges:
        return ResolvedEdges(
            number(self.size, "size"), self.layer, max(number(self.distance, "distance"), 0.0)
        )


Refinement = Annotated[
    PointRefinement | BoxRefinement | EdgesRefinement, Field(discriminator="kind")
]
_REFINEMENT = TypeAdapter(Refinement)


def refinement(**fields) -> PointRefinement | BoxRefinement | EdgesRefinement:
    """A refinement from its fields, its ``kind`` among them."""
    return _REFINEMENT.validate_python(fields)


class MeshSettings(_Model):
    """A component's mesh settings (see the module docstring)."""

    size: Value | None = None  # the largest element; None: automatic
    growth: Value = DEFAULT_GROWTH
    layers: dict[str, Value] = Field(default_factory=dict)
    refine: list[Refinement] = Field(default_factory=list)
    # The automatic size map (#73): finer in narrow parts, small gaps and curves,
    # with ``across`` elements across the narrowest part of a beam or a gap.
    automatic: bool = True
    across: Value = DEFAULT_ACROSS

    def resolved(
        self, variables: dict[str, float], bbox: tuple[float, float, float, float] | None
    ) -> ResolvedMesh:
        """The settings as numbers: expressions evaluated with ``variables`` (the
        component's parameters and the process constants); ``bbox`` (the
        component's extent) gives the automatic size. Raises ValueError for a
        size that is not positive or a growth rate not above 1."""

        def number(value: Value, what: str) -> float:
            try:
                result = evaluate(value, variables)
            except Exception as exc:
                raise ValueError(f"mesh {what}: {exc}") from exc
            return result

        def positive(value: Value, what: str) -> float:
            result = number(value, what)
            if not result > 0:
                raise ValueError(f"mesh {what} must be more than 0, not {result:g}")
            return result

        if self.size is not None:
            size = positive(self.size, "size")
        elif bbox is not None:
            x0, y0, x1, y1 = bbox
            size = max(min(x1 - x0, y1 - y0), 1e-3) / AUTOMATIC_DIVISIONS
        else:
            size = 1.0
        growth = number(self.growth, "growth")
        if not growth > 1:
            raise ValueError(f"mesh growth must be more than 1, not {growth:g}")
        layers = {name: positive(value, f"size of {name}") for name, value in self.layers.items()}
        refine = []
        for k, r in enumerate(self.refine, start=1):
            what = f"refinement {k} ({r.label})"

            def field(value: Value, name: str, what: str = what) -> float:
                if name == "size":
                    return positive(value, f"{what} size")
                return number(value, f"{what} {name}")

            refine.append(r.resolved(field))
        across = positive(self.across, "elements across")
        return ResolvedMesh(size, growth, layers, refine, self.automatic, across)


class ResolvedRefinement:
    """A refinement as numbers. :meth:`apply` hands it to what makes the mesh's size
    field (``sizes.point(r)``, ``.box(r)`` or ``.edges(r)``); :meth:`mark` is how the
    canvas draws it (None: drawn by the layer's own edges)."""

    size: float

    def apply(self, sizes) -> None:  # pragma: no cover - each kind has its own
        raise NotImplementedError

    def mark(self) -> tuple | None:
        return None


@dataclass
class ResolvedPoint(ResolvedRefinement):
    size: float
    x: float
    y: float
    radius: float

    def apply(self, sizes) -> None:
        sizes.point(self)

    def mark(self) -> tuple:
        return ("point", self.x, self.y, self.radius)


@dataclass
class ResolvedBox(ResolvedRefinement):
    size: float
    box: tuple[float, float, float, float]

    def apply(self, sizes) -> None:
        sizes.box(self)

    def mark(self) -> tuple:
        return ("box", *self.box)


@dataclass
class ResolvedEdges(ResolvedRefinement):
    size: float
    layer: str
    distance: float

    def apply(self, sizes) -> None:
        sizes.edges(self)


@dataclass
class ResolvedMesh:
    """:class:`MeshSettings` with numbers for its expressions."""

    size: float
    growth: float
    layers: dict[str, float] = field(default_factory=dict)
    refine: list[ResolvedRefinement] = field(default_factory=list)
    automatic: bool = False
    across: float = 2.0

    def ramp(self, small: float, large: float) -> float:
        """How far the size takes to grow from ``small`` to ``large``, by at most the
        growth rate per element: with the size growing linearly, by ``growth - 1``
        per unit of distance, each element is ``growth`` times the one before."""
        return max(large - small, 0.0) / (self.growth - 1)


def _text(value: Value) -> str:
    return f"{value:g}" if isinstance(value, int | float) else str(value)
