"""Meshes in the formats free solvers read (MSH-5, #51).

- **gmsh** ``.msh`` (version 4.1): gmsh itself, and through it most tools.
- **Abaqus / CalculiX** ``.inp``; Ansys Mechanical reads it too.
- **Nastran** ``.bdf``.
- **Elmer**: a mesh folder (``mesh.header``, ``mesh.nodes``, ``mesh.elements``,
  ``mesh.boundary``, ``mesh.names``), what ``Mesh DB`` points ElmerSolver to.
- **XDMF** ``.xdmf`` (FEniCS/dolfinx, scikit-fem via meshio, ParaView): the
  triangles with a ``region`` number per triangle, the data inline.

Each exports the component's mesh as its mesh settings say (see
:mod:`mems_sketch.meshing`), with layers as named regions and their outlines as
named boundaries: in 2D (``dimensions=2D``, triangles) or 3D (``3D``, the
layers extruded through the stack as the process places them, tetrahedra).
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
from mems_sketch.meshing import Mesh, generate, save_with_gmsh, stack_heights

UNITS = {"um": 1.0, "m": 1e-6}
_OPTIONS = (
    ExportOption(
        "dimensions",
        "2D",
        "Dimensions",
        "2D: the layers' faces, in triangles. 3D: the layers extruded through the "
        "stack (each layer's z and thickness in the process), in tetrahedra",
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
        mesh = generate(
            geometry, settings, variables, layer_list(layers), write=write, heights=heights
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

ELMER_ELEMENT = {2: 303, 3: 504}  # 3-node triangle, 4-node tetrahedron
ELMER_FACET = {2: 202, 3: 303}  # their sides: 2-node line, 3-node triangle


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
    (folder / "mesh.elements").write_text(
        "".join(
            f"{k + 1} {body_of[k] + 1} {ELMER_ELEMENT[dim]} {_numbers(element)}\n"
            for k, element in enumerate(mesh.elements)
        )
    )
    owners = _facet_owners(mesh.elements)
    lines = []
    names = sorted(mesh.boundaries)
    for number, name in enumerate(names, start=1):
        for index in mesh.boundaries[name]:
            facet = mesh.facets[index]
            parents = [*owners.get(tuple(sorted(int(n) for n in facet)), []), -1, -1][:2]
            left, right = (p + 1 if p >= 0 else 0 for p in parents)
            lines.append(f"{number} {left} {right} {ELMER_FACET[dim]} {_numbers(facet)}")
    (folder / "mesh.boundary").write_text(
        "".join(f"{k + 1} {line}\n" for k, line in enumerate(lines))
    )
    types = [(ELMER_ELEMENT[dim], len(mesh.elements))]
    if lines:
        types.append((ELMER_FACET[dim], len(lines)))
    (folder / "mesh.header").write_text(
        f"{len(nodes)} {len(mesh.elements)} {len(lines)}\n{len(types)}\n"
        + "".join(f"{kind} {count}\n" for kind, count in types)
    )
    (folder / "mesh.names").write_text(
        "! ----- names for bodies -----\n"
        + "".join(f"$ {_elmer_name(n)} = {k}\n" for k, n in enumerate(bodies, start=1))
        + "! ----- names for boundaries -----\n"
        + "".join(f"$ {_elmer_name(n)} = {k}\n" for k, n in enumerate(names, start=1))
    )
    return folder


def _elmer_name(name: str) -> str:
    return name.replace("+", "_and_")


def _numbers(nodes) -> str:
    """Node indices as Elmer numbers them (from 1)."""
    return " ".join(str(int(n) + 1) for n in nodes)


def _facet_owners(elements: np.ndarray) -> dict[tuple[int, ...], list[int]]:
    """The elements each side (sorted node indices) belongs to."""
    owners: dict[tuple[int, ...], list[int]] = {}
    for k, element in enumerate(elements.tolist()):
        for left_out in range(len(element)):
            side = tuple(sorted(n for i, n in enumerate(element) if i != left_out))
            owners.setdefault(side, []).append(k)
    return owners


# -- XDMF -----------------------------------------------------------------------------


def write_xdmf(mesh: Mesh, path: Path, scale: float = 1.0) -> Path:
    """``mesh`` as XDMF 3 with the data inline: a ``region`` number per triangle
    (each its body, see :meth:`Mesh.bodies`), the names in an Information entry."""
    bodies, body_of = mesh.bodies()
    nodes = mesh.nodes * scale
    xy = "\n".join(" ".join(f"{v:.12g}" for v in node) for node in nodes)
    cells = "\n".join(" ".join(str(int(n)) for n in element) for element in mesh.elements)
    topology, geometry, corners = (
        ("Triangle", "XY", 3) if mesh.dim == 2 else ("Tetrahedron", "XYZ", 4)
    )
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
