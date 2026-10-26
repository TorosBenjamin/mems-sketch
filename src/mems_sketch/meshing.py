"""Meshes of a component for simulation (MSH-1, #49), made with gmsh.

:func:`generate` meshes a component's layers in 2D (their faces; 3D, the
layers extruded through the stack, comes later). The layers are cut into one
another first, so where they overlap they share their nodes, and the mesh is
conforming everywhere (MSH-7, #52). Each layer is a named region and its edges
a named boundary (``device``, ``device_boundary``), so a solver finds them by
name.

**Sizes** come from the component's mesh settings
(:mod:`mems_sketch.core.mesh_settings`): the largest size, a size per layer,
and refinements. Where one size meets a smaller one, the size changes
linearly with distance, by ``growth - 1`` per µm: each element is then at most
``growth`` times the one next to it, never a jump at a seam.

gmsh is optional (``pip install mems-sketch[mesh]``): :func:`available` says
whether it is installed. It keeps one model at a time, so meshes are made one
after another (a lock), not at once.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from mems_sketch.core.component import Geometry
from mems_sketch.core.mesh_settings import MeshSettings, ResolvedMesh

MERGE_UM = 1e-4  # points closer than this along an outline are one point
MAX_SAMPLING = 4000  # points a distance field samples along an edge
_lock = threading.Lock()


class MeshError(Exception):
    """The mesh could not be made: gmsh missing, bad settings, or gmsh failed."""


def available() -> bool:
    """Whether gmsh is installed (and loads: on Linux it needs libGLU)."""
    try:
        _gmsh()
    except MeshError:
        return False
    return True


def _gmsh():
    try:
        import gmsh
    except (ImportError, OSError) as exc:
        raise MeshError(
            "meshing needs gmsh: pip install 'mems-sketch[mesh]' "
            f"(on Linux it also needs libGLU) ({exc})"
        ) from exc
    return gmsh


@dataclass
class Mesh:
    """A 2D triangle mesh, in µm.

    ``regions`` and ``boundaries`` are named groups of triangles and edges (by
    index). Where layers overlap a triangle is in each of their regions."""

    nodes: np.ndarray  # (n, 2) x, y
    triangles: np.ndarray  # (m, 3) node indices
    regions: dict[str, np.ndarray] = field(default_factory=dict)  # layer: triangle indices
    edges: np.ndarray = field(default_factory=lambda: np.zeros((0, 2), dtype=np.int64))
    boundaries: dict[str, np.ndarray] = field(default_factory=dict)  # name: edge indices
    quality: np.ndarray = field(default_factory=lambda: np.zeros(0))  # per triangle, 0..1
    settings: ResolvedMesh | None = None

    @property
    def element_count(self) -> int:
        return len(self.triangles)

    @property
    def min_quality(self) -> float:
        """The worst triangle's quality (gmsh's minimum scaled Jacobian: 1 is
        equilateral, near 0 is a sliver)."""
        return float(self.quality.min()) if len(self.quality) else 1.0

    def sizes(self) -> np.ndarray:
        """Each triangle's size: the square root of twice its area (its edge, were
        it a right isosceles triangle)."""
        a, b, c = (self.nodes[self.triangles[:, k]] for k in range(3))
        u, v = b - a, c - a
        area = 0.5 * np.abs(u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0])
        return np.sqrt(2 * area)

    def bodies(self) -> tuple[list[str], np.ndarray]:
        """Each triangle in one body, for formats that allow only one: the layers
        it is in, joined (``device``, ``anchor+device``); the names, and each
        triangle's index into them."""
        member = [[] for _ in range(len(self.triangles))]
        for name in sorted(self.regions):
            for index in self.regions[name]:
                member[index].append(name)
        names = sorted({"+".join(m) or "none" for m in member})
        lookup = {name: k for k, name in enumerate(names)}
        return names, np.array([lookup["+".join(m) or "none"] for m in member], dtype=np.int64)

    def summary(self) -> str:
        """``1 234 triangles, 650 nodes; worst quality 0.62``."""

        def count(n: int) -> str:
            return f"{n:,}".replace(",", "\u202f")  # a narrow space between thousands

        return (
            f"{count(self.element_count)} triangles, {count(len(self.nodes))} nodes; "
            f"worst quality {self.min_quality:.2f}"
        )


def generate(
    geometry: Geometry,
    settings: MeshSettings | ResolvedMesh,
    variables: dict[str, float] | None = None,
    layers: Iterable[str] | None = None,
    write: Callable[[object], None] | None = None,
) -> Mesh:
    """Mesh ``geometry`` (all its layers, or ``layers``) with ``settings``
    (expressions evaluated with ``variables``). ``write``, if given, is called
    with the gmsh module while the mesh is in it, to save it in gmsh's formats
    (see :func:`save_with_gmsh`)."""
    gmsh = _gmsh()
    regions = {
        name: region
        for name, region in geometry.layers.items()
        if not region.is_empty() and (layers is None or name in set(layers))
    }
    if not regions:
        raise MeshError("nothing to mesh: the component has no shapes on these layers")
    if isinstance(settings, MeshSettings):
        try:
            settings = settings.resolved(variables or {}, geometry.bbox())
        except ValueError as exc:
            raise MeshError(str(exc)) from exc
    named = set(settings.layers) | {getattr(r, "layer", None) for r in settings.refine} - {None}
    unknown = sorted(named - set(geometry.layers))
    if unknown:
        raise MeshError(f"the mesh settings name layers that are not drawn: {', '.join(unknown)}")
    with _lock:
        gmsh.initialize(interruptible=False)
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.model.add("mems-sketch")
            surfaces = _surfaces(gmsh, regions)
            _sizes(gmsh, surfaces, settings)
            _groups(gmsh, surfaces)
            try:
                gmsh.model.mesh.generate(2)
            except Exception as exc:
                raise MeshError(f"gmsh could not mesh it: {exc}") from exc
            mesh = _read(gmsh, surfaces)
            mesh.settings = settings
            if write is not None:
                write(gmsh)
            return mesh
        finally:
            gmsh.finalize()


# -- the geometry ---------------------------------------------------------------------


def _surfaces(gmsh, regions) -> dict[str, list[int]]:
    """The layers as gmsh surfaces, cut into one another (so that overlapping
    layers share their nodes): each layer's surfaces, by tag."""
    occ = gmsh.model.occ
    inputs: list[tuple[str, int]] = []
    for layer, region in regions.items():
        for hull, holes in region.points_um():
            outer = _loop(occ, hull)
            if outer is None:
                continue
            inner = [loop for loop in (_loop(occ, h) for h in holes) if loop is not None]
            inputs.append((layer, occ.addPlaneSurface([outer, *inner])))
    if len(inputs) > 1:
        _, children = occ.fragment([(2, tag) for _, tag in inputs], [])
    else:
        children = [[(2, inputs[0][1])]]
    occ.synchronize()
    surfaces: dict[str, list[int]] = {}
    for (layer, _), pieces in zip(inputs, children, strict=True):
        found = surfaces.setdefault(layer, [])
        found += [tag for dim, tag in pieces if dim == 2 and tag not in found]
    return surfaces


def _loop(occ, points: np.ndarray) -> int | None:
    """A closed curve through ``points`` (µm), or None if fewer than 3 are left
    after merging points closer than :data:`MERGE_UM`."""
    kept: list[tuple[float, float]] = []
    for x, y in np.asarray(points, dtype=float):
        if not kept or math.dist(kept[-1], (x, y)) > MERGE_UM:
            kept.append((float(x), float(y)))
    while len(kept) > 1 and math.dist(kept[0], kept[-1]) <= MERGE_UM:
        kept.pop()
    if len(kept) < 3:
        return None
    tags = [occ.addPoint(x, y, 0) for x, y in kept]
    lines = [occ.addLine(tags[k], tags[(k + 1) % len(tags)]) for k in range(len(tags))]
    return occ.addCurveLoop(lines)


def _boundary(gmsh, surfaces: list[int]) -> list[int]:
    """The curves round ``surfaces`` (their outline, not the edges between them)."""
    found = gmsh.model.getBoundary([(2, s) for s in surfaces], combined=True, oriented=False)
    return sorted({abs(tag) for dim, tag in found if dim == 1})


# -- sizes ----------------------------------------------------------------------------


def _sizes(gmsh, surfaces: dict[str, list[int]], settings: ResolvedMesh) -> None:
    """The size field: the smallest of each layer's size (inside it), and of each
    smaller size graded outward (see the module docstring)."""
    field_ = gmsh.model.mesh.field
    largest = max([settings.size, *settings.layers.values()])
    parts: list[int] = []

    def constant(value: float, inside: list[int]) -> int:
        tag = field_.add("MathEval")
        field_.setString(tag, "F", repr(float(value)))
        restrict = field_.add("Restrict")
        field_.setNumber(restrict, "InField", tag)
        field_.setNumbers(restrict, "SurfacesList", inside)
        return restrict

    def graded(distance_field: int, size: float, start: float) -> int:
        tag = field_.add("Threshold")
        field_.setNumber(tag, "InField", distance_field)
        field_.setNumber(tag, "SizeMin", size)
        field_.setNumber(tag, "SizeMax", largest)
        field_.setNumber(tag, "DistMin", start)
        field_.setNumber(tag, "DistMax", start + max(settings.ramp(size, largest), 1e-9))
        return tag

    def from_curves(curves: list[int], size: float) -> int:
        tag = field_.add("Distance")
        field_.setNumbers(tag, "CurvesList", curves)
        longest = max((_length(gmsh, c) for c in curves), default=0.0)
        field_.setNumber(tag, "Sampling", min(max(int(longest / size) + 2, 20), MAX_SAMPLING))
        return tag

    sized = {layer: settings.layers.get(layer, settings.size) for layer in surfaces}
    plain = sorted(
        {s for layer, tags in surfaces.items() if layer not in settings.layers for s in tags}
    )
    if plain:  # the layers without a size of their own
        parts.append(constant(settings.size, plain))
    for layer, tags in surfaces.items():
        if layer in settings.layers:
            parts.append(constant(sized[layer], tags))
        if sized[layer] < largest:
            curves = _boundary(gmsh, tags)
            if curves:
                parts.append(graded(from_curves(curves, sized[layer]), sized[layer], 0.0))

    class Refinements:  # what each kind of refinement adds (see ResolvedRefinement.apply)
        @staticmethod
        def point(r) -> None:
            distance = field_.add("MathEval")
            field_.setString(distance, "F", f"Sqrt((x-({r.x!r}))^2+(y-({r.y!r}))^2)")
            parts.append(graded(distance, r.size, r.radius))

        @staticmethod
        def box(r) -> None:
            x0, y0, x1, y1 = r.box
            distance = field_.add("MathEval")  # 0 inside the box, the distance outside
            dx = f"Max(Max({x0!r}-x,x-({x1!r})),0)"
            dy = f"Max(Max({y0!r}-y,y-({y1!r})),0)"
            field_.setString(distance, "F", f"Sqrt({dx}^2+{dy}^2)")
            parts.append(graded(distance, r.size, 0.0))

        @staticmethod
        def edges(r) -> None:
            curves = _boundary(gmsh, surfaces.get(r.layer, []))
            if curves:
                parts.append(graded(from_curves(curves, r.size), r.size, r.distance))

    for refinement in settings.refine:
        refinement.apply(Refinements)
    smallest = field_.add("Min")
    field_.setNumbers(smallest, "FieldsList", parts)
    field_.setAsBackgroundMesh(smallest)
    for option, value in (
        ("Mesh.MeshSizeExtendFromBoundary", 0),
        ("Mesh.MeshSizeFromPoints", 0),
        ("Mesh.MeshSizeFromCurvature", 0),
        ("Mesh.MeshSizeMax", largest),
        ("Mesh.Algorithm", 6),  # Frontal-Delaunay: well-shaped triangles
    ):
        gmsh.option.setNumber(option, value)


def _length(gmsh, curve: int) -> float:
    x0, y0, _, x1, y1, _ = gmsh.model.getBoundingBox(1, curve)
    return math.hypot(x1 - x0, y1 - y0)


def _groups(gmsh, surfaces: dict[str, list[int]]) -> None:
    """Named groups: each layer, and its outline (``<layer>_boundary``)."""
    for layer, tags in surfaces.items():
        gmsh.model.addPhysicalGroup(2, tags, name=layer)
        curves = _boundary(gmsh, tags)
        if curves:
            gmsh.model.addPhysicalGroup(1, curves, name=f"{layer}_boundary")


# -- reading the mesh back -----------------------------------------------------------


def _read(gmsh, surfaces: dict[str, list[int]]) -> Mesh:
    tags, coords, _ = gmsh.model.mesh.getNodes()
    index = {int(t): k for k, t in enumerate(tags)}
    nodes = np.asarray(coords, dtype=float).reshape(-1, 3)[:, :2]
    triangles: list[np.ndarray] = []
    element_tags: list[np.ndarray] = []
    by_surface: dict[int, np.ndarray] = {}
    count = 0
    for surface in sorted({s for found in surfaces.values() for s in found}):
        kinds, elements, connectivity = gmsh.model.mesh.getElements(2, surface)
        for kind, tags_, nodes_ in zip(kinds, elements, connectivity, strict=True):
            if kind != 2:  # 3-node triangles only
                continue
            found = np.array([index[int(n)] for n in nodes_], dtype=np.int64).reshape(-1, 3)
            triangles.append(found)
            element_tags.append(np.asarray(tags_))
            by_surface[surface] = np.arange(count, count + len(found))
            count += len(found)
    all_triangles = np.vstack(triangles) if triangles else np.zeros((0, 3), dtype=np.int64)
    all_tags = np.concatenate(element_tags) if element_tags else np.zeros(0)
    quality = (
        np.asarray(gmsh.model.mesh.getElementQualities(all_tags.tolist(), "minSICN"))
        if len(all_tags)
        else np.zeros(0)
    )
    regions = {
        layer: np.unique(np.concatenate([by_surface.get(s, np.zeros(0, np.int64)) for s in found]))
        for layer, found in surfaces.items()
    }
    edges: list[np.ndarray] = []
    boundaries: dict[str, np.ndarray] = {}
    total = 0
    for layer, found in surfaces.items():
        layer_edges = []
        for curve in _boundary(gmsh, found):
            kinds, _, connectivity = gmsh.model.mesh.getElements(1, curve)
            for kind, nodes_ in zip(kinds, connectivity, strict=True):
                if kind == 1:
                    layer_edges.append(
                        np.array([index[int(n)] for n in nodes_], dtype=np.int64).reshape(-1, 2)
                    )
        if layer_edges:
            block = np.vstack(layer_edges)
            edges.append(block)
            boundaries[f"{layer}_boundary"] = np.arange(total, total + len(block))
            total += len(block)
    return Mesh(
        nodes=nodes,
        triangles=all_triangles,
        regions=regions,
        edges=np.vstack(edges) if edges else np.zeros((0, 2), dtype=np.int64),
        boundaries=boundaries,
        quality=quality,
    )


def save_with_gmsh(path: Path, scale: float = 1.0, msh_version: float = 4.1):
    """A ``write`` for :func:`generate` that saves the mesh as ``path`` in the format
    its extension names (``.msh``, ``.inp``, ``.bdf``, ...): the named groups
    only, coordinates times ``scale`` (1e-6 for metres)."""

    def write(gmsh) -> None:
        gmsh.option.setNumber("Mesh.SaveAll", 0)
        gmsh.option.setNumber("Mesh.ScalingFactor", scale)
        gmsh.option.setNumber("Mesh.MshFileVersion", msh_version)
        gmsh.write(str(path))

    return write
