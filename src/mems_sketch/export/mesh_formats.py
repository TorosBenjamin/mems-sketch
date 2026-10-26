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
named boundaries. Coordinates are in µm, or in metres with ``units=m``.
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
from mems_sketch.meshing import Mesh, generate, save_with_gmsh

UNITS = {"um": 1.0, "m": 1e-6}
_OPTIONS = (
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
        **_: Any,
    ) -> Mesh:
        component = build.component if build is not None else project.top
        variables = build.variables() if build is not None else {}
        settings = mesh_settings(project, component)
        scale = UNITS[units]
        write = save_with_gmsh(Path(path), scale) if self.gmsh_writes else None
        mesh = generate(geometry, settings, variables, layer_list(layers), write=write)
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

ELMER_TRIANGLE, ELMER_LINE = 303, 202


def write_elmer(mesh: Mesh, folder: Path, scale: float = 1.0) -> Path:
    """``mesh`` as an Elmer mesh folder. Bodies are the layers (overlaps are bodies
    of their own, ``anchor+device``); boundaries are each layer's outline. Numbers
    start at 1; ``mesh.names`` maps them to the names."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    bodies, body_of = mesh.bodies()
    nodes = mesh.nodes * scale
    (folder / "mesh.nodes").write_text(
        "".join(f"{k + 1} -1 {x:.12g} {y:.12g} 0\n" for k, (x, y) in enumerate(nodes))
    )
    (folder / "mesh.elements").write_text(
        "".join(
            f"{k + 1} {body_of[k] + 1} {ELMER_TRIANGLE} {a + 1} {b + 1} {c + 1}\n"
            for k, (a, b, c) in enumerate(mesh.triangles)
        )
    )
    owners = _edge_owners(mesh.triangles)
    lines = []
    names = sorted(mesh.boundaries)
    for number, name in enumerate(names, start=1):
        for edge in mesh.boundaries[name]:
            a, b = (int(n) for n in mesh.edges[edge])
            parents = [*owners.get((min(a, b), max(a, b)), []), -1, -1][:2]
            left, right = (p + 1 if p >= 0 else 0 for p in parents)
            lines.append(f"{number} {left} {right} {ELMER_LINE} {a + 1} {b + 1}")
    (folder / "mesh.boundary").write_text(
        "".join(f"{k + 1} {line}\n" for k, line in enumerate(lines))
    )
    types = [(ELMER_TRIANGLE, len(mesh.triangles))]
    if lines:
        types.append((ELMER_LINE, len(lines)))
    (folder / "mesh.header").write_text(
        f"{len(nodes)} {len(mesh.triangles)} {len(lines)}\n{len(types)}\n"
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


def _edge_owners(triangles: np.ndarray) -> dict[tuple[int, int], list[int]]:
    owners: dict[tuple[int, int], list[int]] = {}
    for k, (a, b, c) in enumerate(triangles.tolist()):
        for p, q in ((a, b), (b, c), (c, a)):
            owners.setdefault((min(p, q), max(p, q)), []).append(k)
    return owners


# -- XDMF -----------------------------------------------------------------------------


def write_xdmf(mesh: Mesh, path: Path, scale: float = 1.0) -> Path:
    """``mesh`` as XDMF 3 with the data inline: a ``region`` number per triangle
    (each its body, see :meth:`Mesh.bodies`), the names in an Information entry."""
    bodies, body_of = mesh.bodies()
    nodes = mesh.nodes * scale
    xy = "\n".join(f"{x:.12g} {y:.12g}" for x, y in nodes)
    cells = "\n".join(f"{a} {b} {c}" for a, b, c in mesh.triangles)
    regions = " ".join(str(int(b) + 1) for b in body_of)
    names = "; ".join(f"{k}={name}" for k, name in enumerate(bodies, start=1))
    path = Path(path)
    path.write_text(
        '<?xml version="1.0"?>\n'
        '<Xdmf Version="3.0">\n'
        "<Domain>\n"
        '<Grid Name="mesh" GridType="Uniform">\n'
        f'<Information Name="regions" Value="{escape(names)}"/>\n'
        f'<Topology TopologyType="Triangle" NumberOfElements="{len(mesh.triangles)}">\n'
        f'<DataItem Dimensions="{len(mesh.triangles)} 3" NumberType="Int" Format="XML">\n'
        f"{cells}\n</DataItem>\n</Topology>\n"
        '<Geometry GeometryType="XY">\n'
        f'<DataItem Dimensions="{len(nodes)} 2" Format="XML">\n{xy}\n</DataItem>\n'
        "</Geometry>\n"
        '<Attribute Name="region" AttributeType="Scalar" Center="Cell">\n'
        f'<DataItem Dimensions="{len(mesh.triangles)}" NumberType="Int" Format="XML">\n'
        f"{regions}\n</DataItem>\n</Attribute>\n"
        "</Grid>\n</Domain>\n</Xdmf>\n"
    )
    return path
