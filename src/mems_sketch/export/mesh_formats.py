"""Meshes in the formats free solvers read (MSH-5, #51).

- **gmsh** ``.msh`` (version 4.1): gmsh itself, and through it most tools.
- **Abaqus / CalculiX** ``.inp``; Ansys Mechanical reads it too.
- **Nastran** ``.bdf``.
- **Elmer**: a mesh folder (``mesh.header``, ``mesh.nodes``, ``mesh.elements``,
  ``mesh.boundary``, ``mesh.names``), what ``Mesh DB`` points ElmerSolver to.
- **XDMF** ``.xdmf`` (FEniCS/dolfinx, scikit-fem via meshio, ParaView): the
  elements with a ``region`` number per element, the data inline.

Each exports the component's mesh as its mesh settings say (see
:mod:`mems_sketch.meshing`), with layers as named regions and their outlines as
named boundaries, and each named shape on each layer too (``mass.device``,
``mass.device_boundary``): in 2D (``dimensions=2D``, triangles) or 3D (``3D``, the
layers extruded through the stack as the process places them, in prisms or
tetrahedra), of the first or second order, as the mesh settings say (#346).
Coordinates are in µm, or in metres with ``units=m``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar
from xml.sax.saxutils import escape

import numpy as np

from mems_sketch.core.component import Geometry
from mems_sketch.core.mesh_settings import MeshSettings
from mems_sketch.core.project import Project
from mems_sketch.export.base import ExportOption
from mems_sketch.meshing import (
    ELEMENT_TYPES,
    Mesh,
    generate,
    named_shapes,
    save_with_gmsh,
    stack_heights,
)

UNITS = {"um": 1.0, "m": 1e-6}
_OPTIONS = (
    ExportOption(
        "dimensions",
        "2D",
        "Dimensions",
        "2D: the layers' faces, in triangles. 3D: the layers extruded through the "
        "stack (each layer's z and thickness in the process), in prisms through "
        "each layer, or tetrahedra",
        choices=("2D", "3D"),
    ),
    ExportOption(
        "units",
        "um",
        "Units",
        "Coordinates in micrometres (um) or metres (m)",
        choices=tuple(UNITS),
    ),
    ExportOption(
        "layers",
        "",
        "Layers",
        "The layers to mesh, separated by commas",
        placeholder="all of them",
    ),
)


def mesh_settings(project: Project, component: str | None) -> MeshSettings:
    """The mesh settings of ``component`` (its own, or the defaults: an imported
    cell has none)."""
    if component is None:
        return MeshSettings()
    found = project.definition(component)
    return found[0].mesh if found is not None else MeshSettings()


def layer_list(text: str) -> list[str] | None:
    names = [name.strip() for name in text.split(",") if name.strip()]
    return names or None


class _MeshExporter:
    """Shared by the mesh formats: make the mesh, then :meth:`write` it."""

    format_name: ClassVar[str]
    file_extension: ClassVar[str]
    title: ClassVar[str]
    options = _OPTIONS
    wants_build = True
    gmsh_writes = False  # gmsh writes the file itself, while the mesh is in it

    def export(
        self,
        project: Project,
        geometry: Geometry,
        path: Path,
        build=None,
        units: str = "um",
        layers: str = "",
        dimensions: str = "2D",
        **_: Any,
    ) -> Mesh:
        component = build.component if build is not None else project.top
        variables = build.variables() if build is not None else {}
        settings = mesh_settings(project, component)
        scale = UNITS[units]
        heights = stack_heights(project.process) if dimensions == "3D" else None
        write = save_with_gmsh(Path(path), scale) if self.gmsh_writes else None
        shapes = named_shapes(build) if build is not None else {}
        mesh = generate(
            geometry,
            settings,
            variables,
            layer_list(layers),
            write=write,
            heights=heights,
            shapes=shapes,
        )
        if not self.gmsh_writes:
            self.write(mesh, Path(path), scale)
        return mesh

    def write(self, mesh: Mesh, path: Path, scale: float) -> None:  # pragma: no cover
        raise NotImplementedError


class MshExporter(_MeshExporter):
    format_name = "msh"
    title = "Mesh: gmsh"
    file_extension = ".msh"
    gmsh_writes = True


class AbaqusExporter(_MeshExporter):
    format_name = "inp"
    title = "Mesh: Abaqus / CalculiX"
    file_extension = ".inp"
    gmsh_writes = True


class NastranExporter(_MeshExporter):
    format_name = "bdf"
    title = "Mesh: Nastran"
    file_extension = ".bdf"
    gmsh_writes = True


class ElmerExporter(_MeshExporter):
    """An Elmer mesh folder (``path`` is the folder: ``chip.elmer``)."""

    format_name = "elmer"
    title = "Mesh: Elmer"
    file_extension = ".elmer"

    def write(self, mesh: Mesh, path: Path, scale: float) -> None:
        write_elmer(mesh, path, scale)


class XdmfExporter(_MeshExporter):
    format_name = "xdmf"
    title = "Mesh: XDMF (FEniCS)"
    file_extension = ".xdmf"

    def write(self, mesh: Mesh, path: Path, scale: float) -> None:
        write_xdmf(mesh, path, scale)


BUILTIN = (MshExporter, AbaqusExporter, NastranExporter, ElmerExporter, XdmfExporter)


# -- Elmer ----------------------------------------------------------------------------

# Elmer's numbers for gmsh's element types, and the order Elmer wants their nodes
# in (from ElmerGrid's own conversion, GmshToElmerIndx).
ELMER = {1: 202, 8: 203, 2: 303, 9: 306, 3: 404, 16: 408, 4: 504, 11: 510, 6: 706, 18: 715}
TET10_ORDER = (0, 1, 2, 3, 4, 5, 6, 7, 9, 8)
PRISM15_ORDER = (0, 1, 2, 3, 4, 5, 6, 9, 7, 12, 14, 13, 8, 10, 11)
ELMER_ORDER = {11: TET10_ORDER, 18: PRISM15_ORDER}


def write_elmer(mesh: Mesh, folder: Path, scale: float = 1.0) -> Path:
    """``mesh`` as an Elmer mesh folder. Bodies are the layers (overlaps are bodies
    of their own, ``anchor+device``); boundaries are each layer's outline. Numbers
    start at 1; ``mesh.names`` maps them to the names."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    bodies, body_of = mesh.bodies()
    dim = mesh.dim
    nodes = np.zeros((len(mesh.nodes), 3))
    nodes[:, :dim] = mesh.nodes * scale
    (folder / "mesh.nodes").write_text(
        "".join(f"{k + 1} -1 {x:.12g} {y:.12g} {z:.12g}\n" for k, (x, y, z) in enumerate(nodes))
    )
    kind = mesh.kind.gmsh
    elements = _ordered(mesh.elements, ELMER_ORDER.get(kind))
    (folder / "mesh.elements").write_text(
        "".join(
            f"{k + 1} {body_of[k] + 1} {ELMER[kind]} {_numbers(element)}\n"
            for k, element in enumerate(elements)
        )
    )
    owners = _facet_owners(mesh)
    lines = []
    counts: dict[int, int] = {kind: len(mesh.elements)}
    names = sorted(mesh.boundaries)
    for number, name in enumerate(names, start=1):
        for index in mesh.boundaries[name]:
            facet_kind = int(mesh.facet_types[index])
            facet_type = ELEMENT_TYPES[facet_kind]
            facet = mesh.facets[index, : facet_type.nodes]
            corners = tuple(sorted(int(n) for n in facet[: facet_type.corners]))
            parents = [*owners.get(corners, []), -1, -1][:2]
            left, right = (p + 1 if p >= 0 else 0 for p in parents)
            lines.append(f"{number} {left} {right} {ELMER[facet_kind]} {_numbers(facet)}")
            counts[facet_kind] = counts.get(facet_kind, 0) + 1
    (folder / "mesh.boundary").write_text(
        "".join(f"{k + 1} {line}\n" for k, line in enumerate(lines))
    )
    (folder / "mesh.header").write_text(
        f"{len(nodes)} {len(mesh.elements)} {len(lines)}\n{len(counts)}\n"
        + "".join(f"{ELMER[k]} {count}\n" for k, count in counts.items())
    )
    (folder / "mesh.names").write_text(
        "! ----- names for bodies -----\n"
        + "".join(f"$ {_elmer_name(n)} = {k}\n" for k, n in enumerate(bodies, start=1))
        + "! ----- names for boundaries -----\n"
        + "".join(f"$ {_elmer_name(n)} = {k}\n" for k, n in enumerate(names, start=1))
    )
    return folder


def _ordered(rows: np.ndarray, order: tuple[int, ...] | None) -> np.ndarray:
    """Each row's nodes in ``order`` (None: as they are)."""
    return rows if order is None else rows[:, list(order)]


def _elmer_name(name: str) -> str:
    return name.replace("+", "_and_").replace(".", "_")


def _numbers(nodes) -> str:
    """Node indices as Elmer numbers them (from 1)."""
    return " ".join(str(int(n) + 1) for n in nodes)


def _facet_owners(mesh: Mesh) -> dict[tuple[int, ...], list[int]]:
    """The elements each side (its sorted corner nodes) belongs to."""
    owners: dict[tuple[int, ...], list[int]] = {}
    for k, element in enumerate(mesh.corners.tolist()):
        for side in mesh.kind.sides:
            owners.setdefault(tuple(sorted(element[i] for i in side)), []).append(k)
    return owners


# -- XDMF -----------------------------------------------------------------------------


# XDMF's names for gmsh's element types, and the order of their nodes (as meshio
# writes them: VTK's for the quadratic ones, gmsh's for the linear prism).
XDMF = {
    2: "Triangle",
    9: "Triangle_6",
    4: "Tetrahedron",
    11: "Tetrahedron_10",
    6: "Wedge",
    18: "Wedge_15",
}
XDMF_ORDER = {11: TET10_ORDER, 18: PRISM15_ORDER}


def write_xdmf(mesh: Mesh, path: Path, scale: float = 1.0) -> Path:
    """``mesh`` as XDMF 3 with the data inline: a ``region`` number per element
    (each its body, see :meth:`Mesh.bodies`), the names in an Information entry."""
    bodies, body_of = mesh.bodies()
    nodes = mesh.nodes * scale
    xy = "\n".join(" ".join(f"{v:.12g}" for v in node) for node in nodes)
    kind = mesh.kind
    elements = _ordered(mesh.elements, XDMF_ORDER.get(kind.gmsh))
    cells = "\n".join(" ".join(str(int(n)) for n in element) for element in elements)
    topology, geometry, corners = XDMF[kind.gmsh], "XY" if mesh.dim == 2 else "XYZ", kind.nodes
    regions = " ".join(str(int(b) + 1) for b in body_of)
    names = "; ".join(f"{k}={name}" for k, name in enumerate(bodies, start=1))
    path = Path(path)
    path.write_text(
        '<?xml version="1.0"?>\n'
        '<Xdmf Version="3.0">\n'
        "<Domain>\n"
        '<Grid Name="mesh" GridType="Uniform">\n'
        f'<Information Name="regions" Value="{escape(names)}"/>\n'
        f'<Topology TopologyType="{topology}" NumberOfElements="{len(mesh.elements)}">\n'
        f'<DataItem Dimensions="{len(mesh.elements)} {corners}" NumberType="Int" '
        'Format="XML">\n'
        f"{cells}\n</DataItem>\n</Topology>\n"
        f'<Geometry GeometryType="{geometry}">\n'
        f'<DataItem Dimensions="{len(nodes)} {mesh.dim}" Format="XML">\n{xy}\n</DataItem>\n'
        "</Geometry>\n"
        '<Attribute Name="region" AttributeType="Scalar" Center="Cell">\n'
        f'<DataItem Dimensions="{len(mesh.elements)}" NumberType="Int" Format="XML">\n'
        f"{regions}\n</DataItem>\n</Attribute>\n"
        "</Grid>\n</Domain>\n</Xdmf>\n"
    )
    return path
