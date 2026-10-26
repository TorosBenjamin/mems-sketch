"""Meshes of a component for simulation (MSH-1, #49), made with gmsh.

:func:`generate` meshes a component's layers in **2D** (their faces, in
triangles) or in **3D** (each layer extruded from its ``z`` by its
``thickness``, as the process gives them, in tetrahedra). The layers are cut
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
ELEMENT = {2: 2, 3: 4}  # gmsh's element types: 3-node triangle, 4-node tetrahedron
FACET = {2: 1, 3: 2}  # and of their sides: 2-node line, 3-node triangle
ELEMENT_NAME = {2: "triangles", 3: "tetrahedra"}
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
    """A mesh in µm: triangles in 2D, tetrahedra in 3D.

    ``regions`` and ``boundaries`` are named groups of elements and of facets
    (their sides: edges in 2D, triangles in 3D), by index. Where layers overlap
    an element is in each of their regions."""

    nodes: np.ndarray  # (n, dim) coordinates
    elements: np.ndarray  # (m, dim + 1) node indices
    regions: dict[str, np.ndarray] = field(default_factory=dict)  # layer: element indices
    facets: np.ndarray = field(default_factory=lambda: np.zeros((0, 2), dtype=np.int64))
    boundaries: dict[str, np.ndarray] = field(default_factory=dict)  # name: facet indices
    quality: np.ndarray = field(default_factory=lambda: np.zeros(0))  # per element, 0..1
    settings: ResolvedMesh | None = None

    @property
    def dim(self) -> int:
        return self.nodes.shape[1]

    @property
    def element_count(self) -> int:
        return len(self.elements)

    @property
    def min_quality(self) -> float:
        """The worst element's quality (gmsh's minimum scaled Jacobian: 1 is a
        regular triangle or tetrahedron, near 0 a sliver)."""
        return float(self.quality.min()) if len(self.quality) else 1.0

    def measures(self) -> np.ndarray:
        """Each element's area (2D) or volume (3D)."""
        corners = [self.nodes[self.elements[:, k]] for k in range(self.dim + 1)]
        edges = np.stack([c - corners[0] for c in corners[1:]], axis=1)
        return np.abs(np.linalg.det(edges)) / math.factorial(self.dim)

    def sizes(self) -> np.ndarray:
        """Each element's size: the edge of a right isosceles triangle of its area
        (2D), or of a regular tetrahedron of its volume (3D)."""
        measure = self.measures()
        if self.dim == 2:
            return np.sqrt(2 * measure)
        return np.cbrt(6 * math.sqrt(2) * measure)

    def bodies(self) -> tuple[list[str], np.ndarray]:
        """Each element in one body, for formats that allow only one: the layers
        it is in, joined (``device``, ``anchor+device``); the names, and each
        element's index into them."""
        member = [[] for _ in range(len(self.elements))]
        for name in sorted(self.regions):
            for index in self.regions[name]:
                member[index].append(name)
        names = sorted({"+".join(m) or "none" for m in member})
        lookup = {name: k for k, name in enumerate(names)}
        return names, np.array([lookup["+".join(m) or "none"] for m in member], dtype=np.int64)

    def summary(self) -> str:
        """``1 234 triangles, 650 nodes; worst quality 0.62``."""

        def count(n: int) -> str:
            return f"{n:,}".replace(",", " ")  # a narrow space between thousands

        return (
            f"{count(self.element_count)} {ELEMENT_NAME[self.dim]}, "
            f"{count(len(self.nodes))} nodes; worst quality {self.min_quality:.2f}"
        )


def generate(
    geometry: Geometry,
    settings: MeshSettings | ResolvedMesh,
    variables: dict[str, float] | None = None,
    layers: Iterable[str] | None = None,
    write: Callable[[object], None] | None = None,
    heights: dict[str, tuple[float, float]] | None = None,
) -> Mesh:
    """Mesh ``geometry`` (all its layers, or ``layers``) with ``settings``
    (expressions evaluated with ``variables``): in 2D, or in 3D when ``heights``
    is given (each layer's bottom and thickness, :meth:`Process.heights`; layers
    without one are left out). ``write``, if given, is called with the gmsh
    module while the mesh is in it, to save it in gmsh's formats (see
    :func:`save_with_gmsh`)."""
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
    with _lock:
        gmsh.initialize(interruptible=False)
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.model.add("mems-sketch")
            entities = _entities(gmsh, regions, heights)
            _sizes(gmsh, entities, settings, dim)
            _groups(gmsh, entities, dim)
            try:
                gmsh.model.mesh.generate(dim)
            except Exception as exc:
                raise MeshError(f"gmsh could not mesh it: {exc}") from exc
            mesh = _read(gmsh, entities, dim)
            mesh.settings = settings
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


def _entities(gmsh, regions, heights) -> dict[str, list[int]]:
    """The layers as gmsh surfaces (2D) or volumes (3D, extruded), cut into one
    another so that layers that overlap or touch share their nodes: each layer's
    pieces, by tag."""
    occ = gmsh.model.occ
    dim = 2 if heights is None else 3
    inputs: list[tuple[str, int]] = []
    for layer, region in regions.items():
        z, thickness = (0.0, 0.0) if heights is None else heights[layer]
        for hull, holes in region.points_um():
            outer = _loop(occ, hull, z)
            if outer is None:
                continue
            inner = [loop for loop in (_loop(occ, h, z) for h in holes) if loop is not None]
            surface = occ.addPlaneSurface([outer, *inner])
            if dim == 2:
                inputs.append((layer, surface))
                continue
            made = occ.extrude([(2, surface)], 0, 0, thickness)
            inputs += [(layer, tag) for d, tag in made if d == 3]
    if len(inputs) > 1:
        _, children = occ.fragment([(dim, tag) for _, tag in inputs], [])
    else:
        children = [[(dim, inputs[0][1])]]
    occ.synchronize()
    found: dict[str, list[int]] = {}
    for (layer, _), pieces in zip(inputs, children, strict=True):
        tags = found.setdefault(layer, [])
        tags += [tag for d, tag in pieces if d == dim and tag not in tags]
    return found


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


def _sizes(gmsh, entities: dict[str, list[int]], settings: ResolvedMesh, dim: int) -> None:
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
    ):
        gmsh.option.setNumber(option, value)


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


def _read(gmsh, entities: dict[str, list[int]], dim: int) -> Mesh:
    tags, coords, _ = gmsh.model.mesh.getNodes()
    index = {int(t): k for k, t in enumerate(tags)}
    nodes = np.asarray(coords, dtype=float).reshape(-1, 3)[:, :dim]
    corners = dim + 1

    def connectivity(entity_dim: int, tag: int, kind: int, width: int):
        kinds, element_tags, nodes_ = gmsh.model.mesh.getElements(entity_dim, tag)
        for found_kind, found_tags, found_nodes in zip(kinds, element_tags, nodes_, strict=True):
            if found_kind == kind:
                rows = np.array([index[int(n)] for n in found_nodes], dtype=np.int64)
                return rows.reshape(-1, width), np.asarray(found_tags)
        return np.zeros((0, width), dtype=np.int64), np.zeros(0)

    blocks: list[np.ndarray] = []
    element_tags: list[np.ndarray] = []
    by_entity: dict[int, np.ndarray] = {}
    count = 0
    for tag in sorted({t for found in entities.values() for t in found}):
        rows, found_tags = connectivity(dim, tag, ELEMENT[dim], corners)
        blocks.append(rows)
        element_tags.append(found_tags)
        by_entity[tag] = np.arange(count, count + len(rows))
        count += len(rows)
    elements = np.vstack(blocks) if blocks else np.zeros((0, corners), dtype=np.int64)
    all_tags = np.concatenate(element_tags) if element_tags else np.zeros(0)
    quality = (
        np.asarray(gmsh.model.mesh.getElementQualities(all_tags.tolist(), "minSICN"))
        if len(all_tags)
        else np.zeros(0)
    )
    regions = {
        layer: np.unique(np.concatenate([by_entity.get(t, np.zeros(0, np.int64)) for t in found]))
        for layer, found in entities.items()
    }
    facets: list[np.ndarray] = []
    boundaries: dict[str, np.ndarray] = {}
    total = 0
    for layer, found in entities.items():
        sides = [connectivity(dim - 1, s, FACET[dim], dim)[0] for s in _boundary(gmsh, found, dim)]
        sides = [s for s in sides if len(s)]
        if sides:
            block = np.vstack(sides)
            facets.append(block)
            boundaries[f"{layer}_boundary"] = np.arange(total, total + len(block))
            total += len(block)
    return Mesh(
        nodes=nodes,
        elements=elements,
        regions=regions,
        facets=np.vstack(facets) if facets else np.zeros((0, dim), dtype=np.int64),
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
