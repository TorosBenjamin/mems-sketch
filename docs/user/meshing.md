# Meshing

mems-sketch makes a mesh of a component for simulation, so a design goes
straight into a free solver: **Elmer** (electrostatics, structures, modes),
**CalculiX**, **FEniCS** or scikit-fem, or **gmsh** itself. Ansys Mechanical
also reads the Abaqus and Nastran files. Meshes are **2D** (the layers' faces,
in triangles) or **3D** (each layer extruded through the stack, in prisms or
tetrahedra), with second-order elements by default.

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
- **Automatic** (on by default) makes the mesh finer where the geometry needs
  it: about **Across** elements (2 by default) across each narrow beam and each
  small gap, such as between comb fingers, and enough along tight curves (16
  round a circle). Without it, a beam narrower than the size gets thin,
  distorted triangles. **Show the size map** draws where, before meshing: a
  circle as wide as the element at each corner that needs it, the finest
  darkest. With free tetrahedra in 3D (below) it also keeps each layer's
  elements near its thickness, so thin layers get no flat tetrahedra.
- **Size on** a layer sets a size for that layer, e.g. finer anchors.
- **Refinements** make the mesh finer in places: around a point (within a
  radius), in a box, or along the edges of a layer (within a distance). Points
  and boxes are drawn dashed on the canvas while the window is open.
- **Order** is **Second** by default: each element has a node at the middle of
  each edge too (6-node triangles, 10-node tetrahedra, 15-node prisms). Keep
  it: first-order elements are far too stiff in bending, so a spring's
  stiffness or a resonator's frequency comes out tens of percent off unless
  the mesh is extremely fine. **First** is there for quick looks and tools that
  read nothing else.
- **Through each layer** (3D) is how many prisms go through each layer's
  thickness: 2 by default. **Free tetrahedra** (0) fills the layers with
  tetrahedra instead.
- **Bake** makes the mesh in the background and draws it over the layers,
  with the number of elements and the worst element's quality (1 is a regular
  element, near 0 a sliver; prisms in a thin film score lower for being flat,
  which is fine). The window stays usable meanwhile; the line under the
  buttons says what gmsh is doing and for how long, and **Cancel** stops it.
  Baking again while one is running replaces it.
- When the design or the settings change, the mesh is **kept but out of date**:
  drawn faintly, its summary starting with *Out of date*. A mesh that took under
  a second (a part in 2D) is baked again by itself shortly after your last
  change, while **Bake again after each change, when it is quick** is on;
  larger ones wait for *Bake*. The size map and the refinements always follow
  each edit at once.
- **Export…** saves it (see below). An export of the design the baked mesh is
  current for uses that mesh, rather than meshing it again.

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
  across: 3            # automatic: three elements across narrow parts
  order: 2             # second-order elements (the default); 1 for linear
  through: 2           # 3D: prisms through each layer (the default); 0 for tetrahedra
  # automatic: false   # to turn the automatic size map off
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

By default each layer is the 2D mesh **extruded into prisms**, *Through each
layer* of them through its thickness. Every layer uses the same heights (each
layer's bottom and top, and the steps between), so layers that meet, one on
another or side by side, share their nodes, and where two overlap in height a
prism is in both. Prisms suit thin films: a film 2 µm thick and 100 µm wide
needs tetrahedra about 2 µm across to keep them from being flat, but only a
couple of prisms through it. The example resonator with the automatic map is
about 124 000 prisms in half a minute; as free tetrahedra it is 538 000 and
takes minutes.

## What the mesh is

- **Conforming everywhere.** The layers are cut into one another first, so
  where an anchor overlaps the device layer (2D) or meets it (3D, at the
  device's bottom face), the two share their nodes.
- **Named.** Each layer is a named region (`device`, `anchor`) and its outline
  (its edges in 2D, its surface in 3D) a named boundary (`device_boundary`), so
  a solver finds them by name. Each **named shape** of the component is a
  region too, on each of its layers: `mass.device`, `anchor_left.anchor`, with
  `mass.device_boundary`. Name the electrodes and anchors you want to find
  (in the Shapes list, F2); the mesh is split along their outlines. Where
  layers overlap, a triangle is in both; formats that allow one body per
  element (Elmer, XDMF) name it after the shapes it is in (`mass.device`), else
  its layers (`anchor+device`).
- **Triangles** (gmsh's Frontal-Delaunay); in 3D **prisms** (those triangles
  extruded) or **tetrahedra** (Delaunay). Second order by default: the extra
  nodes are at the middle of each edge.

## Formats

**☰ → File → Export…** (or *Export…* in the Mesh window), or on the command
line `mems-sketch-cli export PROJECT FILE`:

| Format | Extension | For |
|---|---|---|
| gmsh | `.msh` | gmsh, and most tools through it (version 4.1) |
| Abaqus / CalculiX | `.inp` | CalculiX, Abaqus, Ansys Mechanical |
| Nastran | `.bdf` | Nastran and the tools that read it |
| Elmer | `.elmer` | a mesh folder (`mesh.header`, `mesh.nodes`, …, `mesh.names`): point Elmer's `Mesh DB` at it |
| XDMF | `.xdmf` | FEniCS/dolfinx, meshio, ParaView: a `region` number per element |

Options: **Dimensions** (`2D` or `3D`), **Units** (`um`, or `m` for SI
solvers) and **Layers** (the layers to mesh, e.g. `device`; all of them when
empty). Every format writes second-order and prism elements: `C3D15` and
`C3D10` in `.inp`, Elmer's 715 and 510, XDMF's `Wedge_15` and
`Tetrahedron_10`.

```sh
mems-sketch-cli export examples/resonator resonator.elmer --option units=m --option dimensions=3D
```
