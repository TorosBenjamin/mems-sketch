"""Meshes of a component for simulation (MSH-1, #49), made with gmsh.

:func:`generate` meshes a component's layers in **2D** (their faces, in
triangles) or in **3D** (each layer extruded from its ``z`` by its
``thickness``, as the process gives them): by default the 2D mesh extruded into
prisms, a few through each layer (:func:`_extrude`), or free tetrahedra.
Elements are second order unless the settings say first (#346). The layers are cut
into one another first, so where they overlap or touch they share their nodes,
and the mesh is conforming everywhere (MSH-7, #52): a device layer and the
anchor under it meet node to node. Each layer is a named region and its outline
(2D: its edges; 3D: its surface) a named boundary (``device``,
``device_boundary``), so a solver finds them by name.

**Sizes** come from the component's mesh settings
(:mod:`mems_sketch.core.mesh_settings`): the largest size, a size per layer,
and refinements. Where one size meets a smaller one, the size changes
linearly with distance, by ``growth - 1`` per µm: each element is then at most
``growth`` times the one next to it, never a jump at a seam. In 3D, points and
boxes refine through the whole height.

gmsh is optional (``pip install mems-sketch[mesh]``): :func:`available` says
whether it is installed. It keeps one model at a time, so meshes are made one
after another (a lock), not at once.
"""

from __future__ import annotations

import dataclasses
import itertools
import math
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from mems_sketch.core.component import Geometry
from mems_sketch.core.mesh_settings import MeshSettings, ResolvedMesh
from mems_sketch.mesh_sizes import BIN_RATIO, bins, rings_of, size_map

THICKNESS_RATIO = 1.5  # 3D, automatic sizes: elements at most this times the layer thick
MERGE_UM = 1e-4  # points closer than this along an outline are one point
BOOLEAN_TOLERANCE_UM = 1e-3  # outlines closer than this are merged when cut into another
MAX_SAMPLING = 4000  # points a distance field samples along an edge
LEVEL_UM = 1e-9  # heights closer than this are one level of a layered 3D mesh


@dataclass(frozen=True)
class ElementType:
    """A kind of element, by gmsh's number for it: its corners, its nodes (corners
    first, then those mid-edge), what it is called, and its sides (by corner)."""

    gmsh: int
    name: str
    dim: int
    corners: int
    nodes: int
    sides: tuple[tuple[int, ...], ...] = ()


ELEMENT_TYPES = {
    t.gmsh: t
    for t in (
        ElementType(1, "lines", 1, 2, 2),
        ElementType(8, "3-node lines", 1, 2, 3),
        ElementType(2, "triangles", 2, 3, 3, ((0, 1), (1, 2), (2, 0))),
        ElementType(9, "6-node triangles", 2, 3, 6, ((0, 1), (1, 2), (2, 0))),
        ElementType(3, "quadrilaterals", 2, 4, 4),
        ElementType(16, "8-node quadrilaterals", 2, 4, 8),
        ElementType(4, "tetrahedra", 3, 4, 4, ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3))),
        ElementType(
            11, "10-node tetrahedra", 3, 4, 10, ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3))
        ),
        ElementType(
            6, "prisms", 3, 6, 6, ((0, 1, 2), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (2, 0, 3, 5))
        ),
        ElementType(
            18,
            "15-node prisms",
            3,
            6,
            15,
            ((0, 1, 2), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (2, 0, 3, 5)),
        ),
    )
}
TRIANGLE, QUADRILATERAL, PRISM = 2, 3, 6  # gmsh's numbers for the extruded mesh's parts
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
    """A mesh in µm: triangles in 2D; in 3D prisms (layers extruded) or tetrahedra;
    first or second order (:attr:`element_type`), all elements of one type.

    ``regions`` and ``boundaries`` are named groups of elements and of facets
    (their sides: edges in 2D; triangles, and the prisms' quadrilaterals, in 3D),
    by index. Where layers overlap an element is in each of their regions.
    ``facets`` is padded with -1 where a facet has fewer nodes than the widest
    (a prism mesh's triangles next to its quadrilaterals); ``facet_types`` says
    which each is."""

    nodes: np.ndarray  # (n, dim) coordinates
    elements: np.ndarray  # (m, nodes per element) node indices, corners first
    regions: dict[str, np.ndarray] = field(default_factory=dict)  # layer: element indices
    facets: np.ndarray = field(default_factory=lambda: np.zeros((0, 2), dtype=np.int64))
    boundaries: dict[str, np.ndarray] = field(default_factory=dict)  # name: facet indices
    element_type: int = 0  # gmsh's number (ELEMENT_TYPES); 0: the linear simplex
    facet_types: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    quality: np.ndarray = field(default_factory=lambda: np.zeros(0))  # per element, 0..1
    settings: ResolvedMesh | None = None
    # The component's named shapes on each layer (``mass.device``): element indices (#71).
    parts: dict[str, np.ndarray] = field(default_factory=dict)
    size_points: list = field(default_factory=list)  # the automatic size map used (#73)

    @property
    def dim(self) -> int:
        return self.nodes.shape[1]

    @property
    def kind(self) -> ElementType:
        """What the elements are (a 3-node triangle or 4-node tetrahedron if not said)."""
        return ELEMENT_TYPES[self.element_type or (2 if self.dim == 2 else 4)]

    @property
    def order(self) -> int:
        """1: linear elements; 2: with a node mid-edge."""
        return 2 if self.kind.nodes > self.kind.corners else 1

    @property
    def corners(self) -> np.ndarray:
        """Each element's corner nodes only."""
        return self.elements[:, : self.kind.corners]

    @property
    def element_count(self) -> int:
        return len(self.elements)

    @property
    def min_quality(self) -> float:
        """The worst element's quality (gmsh's minimum scaled Jacobian: 1 is a
        regular triangle or tetrahedron, near 0 a sliver)."""
        return float(self.quality.min()) if len(self.quality) else 1.0

    def measures(self) -> np.ndarray:
        """Each element's area (2D) or volume (3D; a prism as three tetrahedra)."""
        if self.kind.corners == 6:
            pieces = ((0, 1, 2, 3), (1, 2, 3, 4), (2, 3, 4, 5))
        else:
            pieces = (tuple(range(self.dim + 1)),)
        total = np.zeros(len(self.elements))
        for piece in pieces:
            corners = [self.nodes[self.elements[:, k]] for k in piece]
            edges = np.stack([c - corners[0] for c in corners[1:]], axis=1)
            total += np.abs(np.linalg.det(edges)) / math.factorial(self.dim)
        return total

    def sizes(self) -> np.ndarray:
        """Each element's size: the edge of a right isosceles triangle of its area
        (2D), or of a regular tetrahedron of its volume (3D)."""
        measure = self.measures()
        if self.dim == 2:
            return np.sqrt(2 * measure)
        return np.cbrt(6 * math.sqrt(2) * measure)

    def bodies(self) -> tuple[list[str], np.ndarray]:
        """Each element in one body, for formats that allow only one: the named
        shapes it is in (``mass.device``), else the layers it is in, joined
        (``device``, ``anchor+device``); the names, and each element's index into
        them."""
        member = [[] for _ in range(len(self.elements))]
        for name in sorted(self.parts):
            for index in self.parts[name]:
                member[index].append(name)
        for name in sorted(self.regions):
            for index in self.regions[name]:
                if not any("." in m for m in member[index]):
                    member[index].append(name)
        names = sorted({"+".join(m) or "none" for m in member})
        lookup = {name: k for k, name in enumerate(names)}
        return names, np.array([lookup["+".join(m) or "none"] for m in member], dtype=np.int64)

    def summary(self) -> str:
        """``1 234 triangles, 650 nodes; worst quality 0.62``."""

        def count(n: int) -> str:
            return f"{n:,}".replace(",", " ")  # a narrow space between thousands

        return (
            f"{count(self.element_count)} {self.kind.name}, "
            f"{count(len(self.nodes))} nodes; worst quality {self.min_quality:.2f}"
        )


def generate(
    geometry: Geometry,
    settings: MeshSettings | ResolvedMesh,
    variables: dict[str, float] | None = None,
    layers: Iterable[str] | None = None,
    write: Callable[[object], None] | None = None,
    heights: dict[str, tuple[float, float]] | None = None,
    shapes: dict[str, Geometry] | None = None,
) -> Mesh:
    """Mesh ``geometry`` (all its layers, or ``layers``) with ``settings``
    (expressions evaluated with ``variables``): in 2D, or in 3D when ``heights``
    is given (each layer's bottom and thickness, :meth:`Process.heights`; layers
    without one are left out). ``write``, if given, is called with the gmsh
    module while the mesh is in it, to save it in gmsh's formats (see
    :func:`save_with_gmsh`). ``shapes``: the component's named shapes and their
    geometry (:func:`named_shapes`), each a named part of the mesh on each layer
    (``mass.device``)."""
    gmsh = _gmsh()
    dim = 2 if heights is None else 3
    wanted = None if layers is None else set(layers)
    regions = {
        name: region
        for name, region in geometry.layers.items()
        if not region.is_empty()
        and (wanted is None or name in wanted)
        and (heights is None or name in heights)
    }
    if not regions:
        missing = " with a thickness" if heights is not None else ""
        raise MeshError(f"nothing to mesh: the component has no shapes on layers{missing}")
    if isinstance(settings, MeshSettings):
        try:
            settings = settings.resolved(variables or {}, geometry.bbox())
        except ValueError as exc:
            raise MeshError(str(exc)) from exc
    named = set(settings.layers) | {getattr(r, "layer", None) for r in settings.refine} - {None}
    unknown = sorted(named - set(geometry.layers))
    if unknown:
        raise MeshError(f"the mesh settings name layers that are not drawn: {', '.join(unknown)}")
    layered = heights is not None and settings.through > 0
    size_points = []
    if settings.automatic:
        largest = max([settings.size, *settings.layers.values()])
        size_points = size_map(rings_of(geometry, regions), settings.across, largest)
        if heights is not None and not layered:  # no flat tetrahedra: near the thickness
            layers = dict(settings.layers)
            for layer in regions:
                cap = heights[layer][1] * THICKNESS_RATIO
                layers[layer] = min(layers.get(layer, settings.size), cap)
            settings = dataclasses.replace(settings, layers=layers)
    with _lock:
        gmsh.initialize(interruptible=False)
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.model.add("mems-sketch")
            entities, parts = _entities(gmsh, regions, None if layered else heights, shapes or {})
            flat_dim = 2 if layered else dim
            _sizes(gmsh, entities, settings, flat_dim, size_points)
            _groups(gmsh, {**entities, **parts}, flat_dim)
            try:
                gmsh.model.mesh.generate(flat_dim)
            except Exception as exc:
                raise MeshError(f"gmsh could not mesh it: {exc}") from exc
            if layered:  # the flat mesh, extruded through each layer's thickness
                flat = _read(gmsh, set(entities), 2)
                gmsh.model.remove()
                gmsh.model.add("mems-sketch-3d")
                _load(gmsh, _extrude(flat, heights, settings.through))
            if settings.order == 2:
                gmsh.option.setNumber(
                    "Mesh.SecondOrderIncomplete", 1
                )  # 8-node quads, 15-node prisms
                gmsh.option.setNumber("Mesh.SecondOrderLinear", 1)  # mid-edge, on straight edges
                gmsh.model.mesh.setOrder(2)
            mesh = _read(gmsh, set(entities), dim)
            mesh.settings = settings
            mesh.size_points = size_points
            if write is not None:
                write(gmsh)
            return mesh
        finally:
            gmsh.finalize()


def stack_heights(process) -> dict[str, tuple[float, float]]:
    """Where each layer of ``process`` is in height, for a 3D mesh; MeshError if no
    layer has a thickness, or one does not evaluate."""
    try:
        heights = process.heights()
    except ValueError as exc:
        raise MeshError(str(exc)) from exc
    if not heights:
        raise MeshError("a 3D mesh needs the layers' z and thickness: set them in the Process tab")
    return heights


# -- the geometry ---------------------------------------------------------------------


def _entities(gmsh, regions, heights, shapes) -> tuple[dict[str, list[int]], dict]:
    """The layers as gmsh surfaces (2D) or volumes (3D), cut into one another so
    that layers that overlap or touch share their nodes: each layer's pieces, by
    tag; and each named shape's pieces on each layer (``mass.device``).

    The outlines are cut into one another flat first, the shapes with the layers,
    so every piece is wholly inside a shape or outside it. In 3D each layer's
    pieces are then extruded from its z by its thickness, and the layers' volumes
    cut into one another where they meet: no two volumes ever coincide, which the
    3D mesher could not fill."""
    occ = gmsh.model.occ
    inputs: list[tuple[str, int]] = []

    def add(region, key: str) -> None:
        for hull, holes in region.points_um():
            outer = _loop(occ, hull)
            if outer is None:
                continue
            inner = [loop for loop in (_loop(occ, h) for h in holes) if loop is not None]
            inputs.append((key, occ.addPlaneSurface([outer, *inner])))

    for layer, region in regions.items():
        add(region, layer)
    for name, geometry in shapes.items():
        for layer, region in geometry.layers.items():
            if layer in regions and not region.is_empty():
                add(region & regions[layer], f"{name}.{layer}")
    # Outlines a hair apart (a shape's edge on its layer's, snapped differently) are one.
    gmsh.option.setNumber("Geometry.ToleranceBoolean", BOOLEAN_TOLERANCE_UM)
    keys_of: dict[int, set[str]] = {}  # each flat piece: the layers and shapes it is in
    children = _cut(occ, [(2, tag) for _, tag in inputs])
    for (key, _), pieces in zip(inputs, children, strict=True):
        for d, tag in pieces:
            if d == 2:
                keys_of.setdefault(tag, set()).add(key)
    if heights is not None:
        volumes: list[tuple[set[str], int]] = []
        for layer in regions:
            z, thickness = heights[layer]
            for piece, keys in keys_of.items():
                if layer not in keys:
                    continue
                copy = occ.copy([(2, piece)])
                occ.translate(copy, 0, 0, z)
                made = occ.extrude(copy, 0, 0, thickness)
                labels = {layer} | {k for k in keys if k.endswith(f".{layer}")}
                volumes += [(labels, tag) for d, tag in made if d == 3]
        occ.remove([(2, piece) for piece in keys_of], recursive=True)
        keys_of = {}
        for (labels, _), pieces in zip(
            volumes, _cut(occ, [(3, t) for _, t in volumes]), strict=True
        ):
            for d, tag in pieces:
                if d == 3:
                    keys_of.setdefault(tag, set()).update(labels)
    occ.synchronize()
    found: dict[str, list[int]] = {}
    for tag, keys in sorted(keys_of.items()):
        for key in keys:
            found.setdefault(key, []).append(tag)
    layers = {key: tags for key, tags in found.items() if key in regions}
    named = {key: tags for key, tags in found.items() if key not in regions}
    return layers, named


def _cut(occ, entities: list[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """``entities`` cut into one another (OCC's fragment): the pieces each became."""
    if len(entities) > 1:
        return occ.fragment(entities, [])[1]
    return [list(entities)]


def _loop(occ, points: np.ndarray, z: float = 0.0) -> int | None:
    """A closed curve through ``points`` (µm) at height ``z``, or None if fewer
    than 3 are left after merging points closer than :data:`MERGE_UM`."""
    kept: list[tuple[float, float]] = []
    for x, y in np.asarray(points, dtype=float):
        if not kept or math.dist(kept[-1], (x, y)) > MERGE_UM:
            kept.append((float(x), float(y)))
    while len(kept) > 1 and math.dist(kept[0], kept[-1]) <= MERGE_UM:
        kept.pop()
    if len(kept) < 3:
        return None
    tags = [occ.addPoint(x, y, z) for x, y in kept]
    lines = [occ.addLine(tags[k], tags[(k + 1) % len(tags)]) for k in range(len(tags))]
    return occ.addCurveLoop(lines)


def _boundary(gmsh, tags: list[int], dim: int) -> list[int]:
    """What bounds the pieces ``tags`` (of dimension ``dim``) together: their
    outline, not the sides between them."""
    found = gmsh.model.getBoundary([(dim, t) for t in tags], combined=True, oriented=False)
    return sorted({abs(tag) for d, tag in found if d == dim - 1})


# -- sizes ----------------------------------------------------------------------------


def _sizes(
    gmsh, entities: dict[str, list[int]], settings: ResolvedMesh, dim: int, size_points=()
) -> None:
    """The size field: the smallest of each layer's size (inside it), and of each
    smaller size graded outward (see the module docstring)."""
    field_ = gmsh.model.mesh.field
    largest = max([settings.size, *settings.layers.values()])
    parts: list[int] = []
    inside_list = "SurfacesList" if dim == 2 else "VolumesList"
    sides_list = "CurvesList" if dim == 2 else "SurfacesList"

    def constant(value: float, inside: list[int]) -> int:
        tag = field_.add("MathEval")
        field_.setString(tag, "F", repr(float(value)))
        restrict = field_.add("Restrict")
        field_.setNumber(restrict, "InField", tag)
        field_.setNumbers(restrict, inside_list, inside)
        return restrict

    def graded(distance_field: int, size: float, start: float) -> int:
        tag = field_.add("Threshold")
        field_.setNumber(tag, "InField", distance_field)
        field_.setNumber(tag, "SizeMin", size)
        field_.setNumber(tag, "SizeMax", largest)
        field_.setNumber(tag, "DistMin", start)
        field_.setNumber(tag, "DistMax", start + max(settings.ramp(size, largest), 1e-9))
        return tag

    def from_sides(sides: list[int], size: float) -> int:
        tag = field_.add("Distance")
        field_.setNumbers(tag, sides_list, sides)
        longest = max((_extent(gmsh, dim - 1, s) for s in sides), default=0.0)
        field_.setNumber(tag, "Sampling", min(max(int(longest / size) + 2, 20), MAX_SAMPLING))
        return tag

    sized = {layer: settings.layers.get(layer, settings.size) for layer in entities}
    plain = sorted(
        {t for layer, tags in entities.items() if layer not in settings.layers for t in tags}
    )
    if plain:  # the layers without a size of their own
        parts.append(constant(settings.size, plain))
    for layer, tags in entities.items():
        if layer in settings.layers:
            parts.append(constant(sized[layer], tags))
        if sized[layer] < largest:
            sides = _boundary(gmsh, tags, dim)
            if sides:
                parts.append(graded(from_sides(sides, sized[layer]), sized[layer], 0.0))

    class Refinements:  # what each kind of refinement adds (see ResolvedRefinement.apply)
        @staticmethod
        def point(r) -> None:  # (in 3D, along the vertical line through the point)
            distance = field_.add("MathEval")
            field_.setString(distance, "F", f"Sqrt((x-({r.x!r}))^2+(y-({r.y!r}))^2)")
            parts.append(graded(distance, r.size, r.radius))

        @staticmethod
        def box(r) -> None:  # (in 3D, the box through the whole height)
            x0, y0, x1, y1 = r.box
            distance = field_.add("MathEval")  # 0 inside the box, the distance outside
            dx = f"Max(Max({x0!r}-x,x-({x1!r})),0)"
            dy = f"Max(Max({y0!r}-y,y-({y1!r})),0)"
            field_.setString(distance, "F", f"Sqrt({dx}^2+{dy}^2)")
            parts.append(graded(distance, r.size, 0.0))

        @staticmethod
        def edges(r) -> None:
            sides = _boundary(gmsh, entities.get(r.layer, []), dim)
            if sides:
                parts.append(graded(from_sides(sides, r.size), r.size, r.distance))

    for refinement in settings.refine:
        refinement.apply(Refinements)
    parts += _automatic(gmsh, size_points, graded, sides_list, dim)
    smallest = field_.add("Min")
    field_.setNumbers(smallest, "FieldsList", parts)
    field_.setAsBackgroundMesh(smallest)
    for option, value in (
        ("Mesh.MeshSizeExtendFromBoundary", 0),
        ("Mesh.MeshSizeFromPoints", 0),
        ("Mesh.MeshSizeFromCurvature", 0),
        ("Mesh.MeshSizeMax", largest),
        ("Mesh.Algorithm", 6),  # Frontal-Delaunay: well-shaped triangles
        ("Mesh.Algorithm3D", 1),  # Delaunay
        ("Mesh.OptimizeNetgen", 1),  # and then fewer slivers
    ):
        gmsh.option.setNumber(option, value)


def _automatic(gmsh, size_points, graded, sides_list: str, dim: int) -> list[int]:
    """The size fields of the automatic size map: for each bin of sizes, the
    distance to its corners and to the edges between two of them (a narrow beam's
    sides), graded away by the growth rate."""
    if not size_points:
        return []
    field_ = gmsh.model.mesh.field
    by_place = {(round(p.x, 4), round(p.y, 4)): p.size for p in size_points}
    corner_size: dict[int, float] = {}
    for _, tag in gmsh.model.getEntities(0):
        x, y, _z = gmsh.model.getValue(0, tag, [])
        size = by_place.get((round(x, 4), round(y, 4)))
        if size is not None:
            corner_size[tag] = size
    if not corner_size:
        return []
    groups: list[tuple[float, list[int], list[int]]] = []  # size, corners, edges
    for size, members in bins(size_points):
        top = size * BIN_RATIO
        corners = [t for t, s in corner_size.items() if size <= s <= top]
        groups.append((size, corners, []))
    for _, curve in gmsh.model.getEntities(1):
        ends = gmsh.model.getBoundary([(1, curve)], oriented=False)
        sizes = [corner_size.get(abs(t)) for d, t in ends if d == 0]
        if len(sizes) == 2 and None not in sizes:
            edge = max(sizes)  # finer along it only where both ends are
            for size, _corners, edges in groups:
                if size <= edge <= size * BIN_RATIO:
                    edges.append(curve)
                    break
    made = []
    for size, corners, edges in groups:
        if not corners and not edges:
            continue
        distance = field_.add("Distance")
        if corners:
            field_.setNumbers(distance, "PointsList", corners)
        if edges:
            field_.setNumbers(distance, "CurvesList", edges)
            longest = max(_extent(gmsh, 1, e) for e in edges)
            field_.setNumber(
                distance, "Sampling", min(max(int(longest / size) + 2, 20), MAX_SAMPLING)
            )
        made.append(graded(distance, size, 0.0))
    return made


def named_shapes(build) -> dict[str, Geometry]:
    """The named shapes at the top of a component's shape tree and their geometry,
    from its build (``Build.records()``): what :func:`generate` names parts after."""
    try:
        records = build.records()
    except Exception:  # noqa: BLE001 - no records: no named parts
        return {}
    found: dict[str, Geometry] = {}
    for path, record in records.items():
        if len(path) != 1:
            continue
        name = record.points.name if record.points is not None else None
        if name and not record.geometry.is_empty():
            found[name] = record.geometry
    return found


def _extent(gmsh, dim: int, tag: int) -> float:
    """The size of an entity: the diagonal of its bounding box."""
    x0, y0, z0, x1, y1, z1 = gmsh.model.getBoundingBox(dim, tag)
    return math.dist((x0, y0, z0), (x1, y1, z1))


def _groups(gmsh, entities: dict[str, list[int]], dim: int) -> None:
    """Named groups: each layer, and its outline (``<layer>_boundary``)."""
    for layer, tags in entities.items():
        gmsh.model.addPhysicalGroup(dim, tags, name=layer)
        sides = _boundary(gmsh, tags, dim)
        if sides:
            gmsh.model.addPhysicalGroup(dim - 1, sides, name=f"{layer}_boundary")


# -- reading the mesh back -----------------------------------------------------------


def _read(gmsh, layers: set[str], dim: int) -> Mesh:
    """The mesh in gmsh, by its named groups (see :func:`_groups`): the elements of
    the groups named in ``layers`` and of the named shapes' groups, and the facets
    of the ``_boundary`` groups."""
    tags, coords, _ = gmsh.model.mesh.getNodes()
    tags = np.asarray(tags, dtype=np.int64)
    index = np.full(int(tags.max()) + 1 if len(tags) else 1, -1, dtype=np.int64)
    index[tags] = np.arange(len(tags))
    nodes = np.asarray(coords, dtype=float).reshape(-1, 3)[:, :dim]
    groups = {gmsh.model.getPhysicalName(d, t): (d, t) for d, t in gmsh.model.getPhysicalGroups()}

    def gather(group_dim: int, names: list[str]):
        """The elements of the groups ``names`` (each once): their types, node
        indices (padded with -1), gmsh tags, and each group's by index."""
        by_entity: dict[int, np.ndarray] = {}
        types, rows, found_tags = [], [], []
        count = 0
        for name in names:
            for entity in gmsh.model.getEntitiesForPhysicalGroup(*groups[name]):
                if entity in by_entity:
                    continue
                start = count
                kinds, element_tags, element_nodes = gmsh.model.mesh.getElements(group_dim, entity)
                for kind, kind_tags, kind_nodes in zip(
                    kinds, element_tags, element_nodes, strict=True
                ):
                    width = ELEMENT_TYPES[int(kind)].nodes
                    block = index[np.asarray(kind_nodes, dtype=np.int64)].reshape(-1, width)
                    rows.append(block)
                    types.append(np.full(len(block), int(kind), dtype=np.int64))
                    found_tags.append(np.asarray(kind_tags, dtype=np.int64))
                    count += len(block)
                by_entity[entity] = np.arange(start, count)
        width = max((r.shape[1] for r in rows), default=dim)
        padded = np.full((count, width), -1, dtype=np.int64)
        at = 0
        for block in rows:
            padded[at : at + len(block), : block.shape[1]] = block
            at += len(block)
        empty = np.zeros(0, dtype=np.int64)
        joined_types = np.concatenate(types) if types else empty
        joined_tags = np.concatenate(found_tags) if found_tags else empty

        def members(name: str) -> np.ndarray:
            found = [by_entity[e] for e in gmsh.model.getEntitiesForPhysicalGroup(*groups[name])]
            return np.unique(np.concatenate(found)) if found else empty

        return joined_types, padded, joined_tags, {name: members(name) for name in names}

    bodies = [n for n, (d, _t) in groups.items() if d == dim]
    sides = [n for n, (d, _t) in groups.items() if d == dim - 1]
    types, elements, element_tags, by_name = gather(dim, bodies)
    kinds = set(types.tolist())
    if len(kinds) > 1:
        raise MeshError(f"the mesh mixes element types: {sorted(kinds)}")
    quality = (
        np.asarray(gmsh.model.mesh.getElementQualities(element_tags.tolist(), "minSICN"))
        if len(element_tags)
        else np.zeros(0)
    )
    facet_types, facets, _, boundaries = gather(dim - 1, sides)
    return Mesh(
        nodes=nodes,
        elements=elements,
        regions={n: m for n, m in by_name.items() if n in layers},
        facets=facets,
        boundaries=boundaries,
        quality=quality,
        parts={n: m for n, m in by_name.items() if n not in layers},
        element_type=kinds.pop() if kinds else 0,
        facet_types=facet_types,
    )


# -- layered 3D meshes ----------------------------------------------------------------


@dataclass
class _Extruded:
    """A prism mesh made from a flat one (see :func:`_extrude`)."""

    nodes: np.ndarray  # (n, 3)
    prisms: np.ndarray  # (m, 6): the bottom triangle, then the top one
    groups: dict[str, np.ndarray]  # layers and named shapes: prism indices
    facets: np.ndarray  # (k, 4): triangles padded with -1, and quadrilaterals
    facet_types: np.ndarray  # (k,): TRIANGLE or QUADRILATERAL
    boundaries: dict[str, np.ndarray]  # ``<name>_boundary``: facet indices


def _extrude(flat: Mesh, heights: dict[str, tuple[float, float]], through: int) -> _Extruded:
    """``flat`` (linear triangles, its layers and named shapes) extruded into prisms:
    each layer from its z by its thickness, in ``through`` prisms through it.

    All layers share the same levels in height (every layer's bottom and top, and
    the steps between), so where two layers meet, at a face or side by side, their
    prisms share nodes: the mesh is conforming without any cutting. Where layers
    overlap in height over the same triangle, one prism is in both."""
    points = flat.nodes
    triangles = flat.corners.copy()
    a, b, c = (points[triangles[:, k]] for k in range(3))
    u, v = b - a, c - a
    clockwise = (
        u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0] < 0
    )  # every bottom counter-clockwise from above
    triangles[clockwise, 1], triangles[clockwise, 2] = (
        triangles[clockwise, 2].copy(),
        triangles[clockwise, 1].copy(),
    )
    layers = sorted(flat.regions)
    breaks: list[float] = []
    for z in sorted({v for layer in layers for v in (heights[layer][0], sum(heights[layer]))}):
        if not breaks or z - breaks[-1] > LEVEL_UM:
            breaks.append(z)
    levels, slab_of = [breaks[0]], []  # each step's slab
    cover = np.zeros((len(breaks) - 1, len(layers)), dtype=bool)  # the layers in each slab
    for k, (low, high) in enumerate(itertools.pairwise(breaks)):
        steps = 1
        for j, layer in enumerate(layers):
            z, thickness = heights[layer]
            if z <= low + LEVEL_UM and z + thickness >= high - LEVEL_UM:
                cover[k, j] = True
                steps = max(steps, math.ceil(through * (high - low) / thickness - 1e-6))
        levels += [low + (high - low) * (s + 1) / steps for s in range(steps)]
        slab_of += [k] * steps
    level_count = len(levels)
    inside = np.zeros((len(triangles), len(layers)), dtype=bool)
    for j, layer in enumerate(layers):
        inside[flat.regions[layer], j] = True
    has = inside[:, None, :] & cover[np.asarray(slab_of)][None, :, :]  # triangle, step, layer
    tri, step = np.nonzero(has.any(axis=2))
    keys = np.hstack(
        [
            triangles[tri] * level_count + step[:, None],
            triangles[tri] * level_count + step[:, None] + 1,
        ]
    )
    used, prisms = np.unique(keys, return_inverse=True)
    prisms = prisms.reshape(-1, 6)
    nodes = np.column_stack([points[used // level_count], np.asarray(levels)[used % level_count]])

    groups = {layer: np.nonzero(has[tri, step, j])[0] for j, layer in enumerate(layers)}
    for name, members in flat.parts.items():
        layer = name.rsplit(".", 1)[-1]
        if layer not in layers:
            continue
        in_part = np.zeros(len(triangles), dtype=bool)
        in_part[members] = True
        groups[name] = np.nonzero(in_part[tri] & has[tri, step, layers.index(layer)])[0]

    # The faces: a triangle at a level, or a quadrilateral up an edge over a step.
    edge_pairs = np.sort(
        np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]]), axis=1
    )
    edges, edge_of = np.unique(edge_pairs, axis=0, return_inverse=True)
    edge_of = edge_of.reshape(3, -1).T  # each triangle's three edges
    steps_count = level_count - 1
    quads_from = len(triangles) * level_count  # face keys: triangles first, then quadrilaterals
    outline: dict[str, np.ndarray] = {}
    for name, members in groups.items():
        t, s = tri[members], step[members]
        caps = np.concatenate([t * level_count + s, t * level_count + s + 1])
        sides = (edge_of[t] * steps_count + s[:, None]).ravel() + quads_from
        found, counts = np.unique(np.concatenate([caps, sides]), return_counts=True)
        outline[name] = found[counts == 1]  # a face of only one of its prisms
    faces = (
        np.unique(np.concatenate(list(outline.values())))
        if outline
        else np.zeros(0, dtype=np.int64)
    )
    caps, sides = faces[faces < quads_from], faces[faces >= quads_from] - quads_from

    def lookup(key: np.ndarray) -> np.ndarray:  # a node by its key
        return np.searchsorted(used, key)

    cap_rows = lookup(triangles[caps // level_count] * level_count + (caps % level_count)[:, None])
    edge, low = edges[sides // steps_count], sides % steps_count
    side_rows = np.column_stack(
        [
            lookup(edge[:, 0] * level_count + low),
            lookup(edge[:, 1] * level_count + low),
            lookup(edge[:, 1] * level_count + low + 1),
            lookup(edge[:, 0] * level_count + low + 1),
        ]
    )
    facets = np.vstack([np.column_stack([cap_rows, np.full(len(caps), -1)]), side_rows])
    facet_types = np.concatenate([np.full(len(caps), TRIANGLE), np.full(len(sides), QUADRILATERAL)])
    boundaries = {
        f"{name}_boundary": np.searchsorted(faces, keys_)
        for name, keys_ in outline.items()
        if len(keys_)
    }
    return _Extruded(nodes, prisms, groups, facets.astype(np.int64), facet_types, boundaries)


def _load(gmsh, mesh: _Extruded) -> None:
    """``mesh`` into gmsh's (empty) model, with the same named groups as a mesh
    gmsh made itself: an entity for each set of groups an element is in."""
    tag = itertools.count(1)
    node_tags = np.arange(1, len(mesh.nodes) + 1)

    def entities(dim: int, count: int, groups: dict[str, np.ndarray]):
        """One entity per combination of groups: each, and the rows in it."""
        names = sorted(groups)
        member = np.zeros((count, max(len(names), 1)), dtype=bool)
        for k, name in enumerate(names):
            member[groups[name], k] = True
        combos, which = np.unique(np.packbits(member, axis=1), axis=0, return_inverse=True)
        which = which.ravel()
        made = []
        for combo in range(len(combos)):
            rows = np.nonzero(which == combo)[0]
            in_groups = [n for k, n in enumerate(names) if member[rows[0], k]]
            made.append((gmsh.model.addDiscreteEntity(dim), rows, in_groups))
        return made

    volumes = entities(3, len(mesh.prisms), mesh.groups)
    gmsh.model.mesh.addNodes(3, volumes[0][0], node_tags.tolist(), mesh.nodes.ravel().tolist())
    for entity, rows, _ in volumes:
        element_tags = [next(tag) for _ in rows]
        gmsh.model.mesh.addElementsByType(
            entity, PRISM, element_tags, (mesh.prisms[rows] + 1).ravel().tolist()
        )
    surfaces = entities(2, len(mesh.facets), mesh.boundaries)
    for entity, rows, _ in surfaces:
        for kind, width in ((TRIANGLE, 3), (QUADRILATERAL, 4)):
            chosen = rows[mesh.facet_types[rows] == kind]
            if len(chosen):
                gmsh.model.mesh.addElementsByType(
                    entity,
                    kind,
                    [next(tag) for _ in chosen],
                    (mesh.facets[chosen, :width] + 1).ravel().tolist(),
                )
    for dim, made, names in ((3, volumes, mesh.groups), (2, surfaces, mesh.boundaries)):
        for name in sorted(names):
            members = [entity for entity, _, in_groups in made if name in in_groups]
            if members:
                gmsh.model.addPhysicalGroup(dim, members, name=name)


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
