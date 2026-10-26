# Meshing

mems-sketch makes a mesh of a component for simulation, so a design goes
straight into a free solver: **Elmer** (electrostatics, structures, modes),
**CalculiX**, **FEniCS** or scikit-fem, or **gmsh** itself. Ansys Mechanical
also reads the Abaqus and Nastran files. Meshes are **2D** (the layers' faces,
in triangles) or **3D** (each layer extruded through the stack, in
tetrahedra).

Meshing uses [gmsh](https://gmsh.info). It is installed with the app; with
pip, it is an extra: `pip install "mems-sketch[mesh]"`. On Linux, gmsh also
needs the system's `libGLU` (`sudo apt install libglu1-mesa`), which desktops
usually have.

gmsh is GPL-licensed. The rest of mems-sketch is MIT. The installers bundle
gmsh, so as a whole they are distributed under the GPL's terms; installed with
pip, gmsh is optional.

## The Mesh window

**Mesh** on the right stripe (Alt+8) shows how the component you are editing
is meshed:

- **Size** is the largest element, in µm. Left empty, it is a twentieth of
  the component's smaller side.
- **Growth** is how much larger an element may be than the one next to it
  (1.2 by default). Where a fine region meets a coarse one, the size changes
  gradually, never in one step: sudden jumps make distorted elements and
  unreliable results along the seam.
- **Size on** a layer sets a size for that layer, e.g. finer anchors.
- **Refinements** make the mesh finer in places: around a point (within a
  radius), in a box, or along the edges of a layer (within a distance). Points
  and boxes are drawn dashed on the canvas while the window is open.
- **Mesh** makes the mesh and draws it over the layers, with the number of
  triangles and the worst triangle's quality (1 is equilateral, near 0 a
  sliver). It goes away when the design changes; press Mesh again.
- **Export…** saves it (see below).

Every value is an expression like any other, over the component's parameters
and the process constants: `size: plate / 20` follows the plate. The settings
are saved with the component, undone like any edit, and listed in the Commit
panel and in diffs. A library's component shows its settings without letting
them be changed.

In the component's file:

```yaml
mesh:
  size: plate / 20
  growth: 1.2
  layers: {anchor: 2}
  refine:
    - {kind: point, x: 0, y: 0, size: 0.5, radius: 3}
    - {kind: box, x0: -10, y0: 80, x1: 10, y1: 120, size: 1}
    - {kind: edges, layer: device, size: 0.5, distance: 1}
```

## 3D: the layers in height

A 3D mesh needs to know where each layer is in height. In the **Process** tab,
each layer has a **Z** (its bottom) and a **Thickness**, in µm, as expressions
over the process constants:

```yaml
constants: {t_wiring: 0.5, t_oxide: 2, t_device: 2}
layers:
  anchor: {gds: [2, 0], z: process.t_wiring, thickness: process.t_oxide}
  device: {gds: [1, 0], z: process.t_wiring + process.t_oxide, thickness: process.t_device}
```

A layer without a thickness (a mask, such as a cavity or a dimple) is left
out of 3D meshes. The bundled processes (mems.surface, mems.soi,
mems.polysilicon) give theirs. Choose **3D** before *Mesh* in the Mesh window,
or the *Dimensions* option when exporting. A 3D mesh is counted in the
window, not drawn. Point and box refinements reach through the whole height.

## What the mesh is

- **Conforming everywhere.** The layers are cut into one another first, so
  where an anchor overlaps the device layer (2D) or meets it (3D, at the
  device's bottom face), the two share their nodes.
- **Named.** Each layer is a named region (`device`, `anchor`) and its outline
  (its edges in 2D, its surface in 3D) a named boundary (`device_boundary`), so
  a solver finds them by name. Where
  layers overlap, a triangle is in both; formats that allow one body per
  element name the overlap after both (`anchor+device`).
- **Triangles** (gmsh's Frontal-Delaunay) or **tetrahedra** (Delaunay).

## Formats

**☰ → File → Export…** (or *Export…* in the Mesh window), or on the command
line `mems-sketch-cli export PROJECT FILE`:

| Format | Extension | For |
|---|---|---|
| gmsh | `.msh` | gmsh, and most tools through it (version 4.1) |
| Abaqus / CalculiX | `.inp` | CalculiX, Abaqus, Ansys Mechanical |
| Nastran | `.bdf` | Nastran and the tools that read it |
| Elmer | `.elmer` | a mesh folder (`mesh.header`, `mesh.nodes`, …, `mesh.names`): point Elmer's `Mesh DB` at it |
| XDMF | `.xdmf` | FEniCS/dolfinx, meshio, ParaView: a `region` number per triangle |

Options: **Dimensions** (`2D` or `3D`), **Units** (`um`, or `m` for SI
solvers) and **Layers** (the layers to mesh, e.g. `device`; all of them when
empty).

```sh
mems-sketch-cli export examples/resonator resonator.elmer --option units=m --option dimensions=3D
```
