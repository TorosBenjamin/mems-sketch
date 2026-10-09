# Geometry core architecture

> **Status: target architecture, not built yet.** It describes the geometry
> backend mems-sketch is moving to. Until a part is migrated, the code works
> as [Architecture](architecture.md) describes. Each migration step below
> updates both documents.

mems-sketch is moving its geometry backend into a C++ core built on
[Open CASCADE Technology](https://dev.opencascade.org/) (OCC). The Qt editor,
the project model, files, history and the CLI stay in Python.

## Why

The current backend builds everything as KLayout regions on a 1 nm integer
grid. That is right for masks, and wrong for the work around them:

- **One grid for everything.** Every shape is snapped to 1 nm as soon as it
  is built. Booleans, offsets and fillets all run on that grid, so their
  errors build up, and an exported mesh inherits 1 nm slivers and rounded
  angles. KLayout's boolean engine uses 32-bit integers, so a finer grid
  shrinks the largest possible design: ±21 µm at 10⁻⁸ µm.
- **No true curves.** Circles, arcs and fillets become polygons (5 nm chord
  deviation) at build time. A mesher then sees fake corners, exactly where
  stress matters (flexure roots, fillets).
- **2D only.** No solids, no STEP, nothing for a mesher or a 3D view.
- **Copies instead of instances.** Placements and arrays copy every polygon,
  and an array evaluates its node again for every copy.

## Goals

1. **One geometry tolerance**, 10⁻⁸ µm or finer, with exact lines and arcs.
   Exports choose their own: GDS snaps to its grid (1 nm), a mesher uses its
   merge tolerance (e.g. 10⁻⁶ µm), and curves are split into segments only
   for an output that needs it, to that output's chord tolerance.
2. **3D from the process stack.** Layers extruded by their thickness into
   solids, for a 3D view, STEP and meshing (gmsh).
3. **Interactive speed on MEMS-sized designs**, such as plates with 10,000
   release holes and combs with 1,000 fingers, while a parameter is dragged.
4. **The same projects.** The YAML files, expressions, shape kinds,
   modifiers, alignments and results stay as they are. A project that builds
   today builds the same, within 1 nm, on the new core.

Out of scope: simulation itself, and editing a mesh by hand. Meshes are
controlled by their settings (sizes and refinement regions), which stay
parametric.

## Overview

```
 Python                                          │  C++  (mems_sketch._core)
                                                 │
 project folder (YAML) ─► Project (pydantic) ────┼─► Engine
 GUI · CLI · scripts       validated, plain data │     expressions, shape tree,
       ▲                                         │     modifiers, alignments
       │                                         │     │
       │   polygons for drawing, node records,   │     ▼
       └── points, rule results, errors ◄────────┼── geometry: OCC shapes per
                                                 │   layer, instanced, cached
                                                 │     │
 storage, git, history, CLI, exporter plugins    │     ▼
                                                 │   outputs: GDS/OASIS/DXF
                                                 │   (via KLayout), STEP, BREP,
                                                 │   mesh (gmsh), 3D triangles
```

### The boundary

The boundary is at the **component**, not at the operation. Python hands the
core a whole validated project. The core evaluates it, and Python asks
for results. A rebuild after an edit is one call, not one call per shape, so
a 10,000-cell array never makes 10,000 calls across the boundary.

| Stays in Python | Moves to C++ |
|---|---|
| The Qt GUI: canvas, tools, panels, History panel | Expression evaluation (`core/expressions.py`) |
| The project model: pydantic classes are still the one definition of the file format | Shape kinds, modifiers, alignments and points (`core/shapes/`) |
| Storage: project folders, documents, git | The compiler: fingerprints, cache, evaluation order (`core/compiler.py`) |
| `EditSession`: commands, transactions, undo/redo | Geometry: OCC shapes, booleans, offsets, fillets, extrusion |
| History diffs in words (`core/diff.py`) | Rule checks (`process/rules.py`), on exported geometry |
| The CLI and the Python scripting API | Exports that need geometry: GDS/OASIS/DXF, STEP, BREP, mesh |
| Exporter plugins that only need data (JSON, XML, `.mat`) | Imported layouts (GDS/OASIS cells read into the core) |

The core never imports Python or Qt. Like the Python backend today, it can be
tested and used without a GUI.

## The core

### Interface

The Python extension `mems_sketch._core` is built with
[nanobind](https://github.com/wjakob/nanobind). It is deliberately small: the
GUI and scripts never handle OCC objects.

```python
engine = _core.Engine()
engine.load(project_json)  # a whole project, validated by pydantic
engine.update(component_name, def_json)  # an edit: only what depends on it rebuilds

build = engine.build("resonator", params)  # cached; raises with the node path on error
build.layers()  # names of layers with geometry
build.points()  # declared alignment points, µm
build.records()  # per node: frame, shift, bounding box, points
build.outlines(layer, chord_um)  # rings for drawing, as NumPy arrays
build.instances(layer)  # (prototype, transform) pairs for drawing
build.check()  # rule violations
build.export("gds", path, grid_um=0.001, chord_um=0.005)
build.export("step", path)
build.solids(chord_um)  # triangles per layer for a 3D view
```

Values cross the boundary as plain data: JSON or dicts in, NumPy arrays and
small records out. The existing `Geometry` (KLayout regions) stays available
in Python, made from `outlines` or an export, for code that has not moved
yet.

### Kernel and precision

- **Unit: the nanometre.** OCC treats points within `Precision::Confusion()`
  = 10⁻⁷ *model units* as the same. In nanometres that is 10⁻¹⁰ µm, below
  the 10⁻⁸ µm goal, and a die of several centimetres is still well inside
  double precision.
- **Exact curves.** Circles, arcs, fillets and the round joins of offsets are
  OCC circles, not polygons. `segments` on a shape stops being part of
  the geometry. It becomes a display or export hint, or is dropped.
- **2D is planar faces.** OCC's booleans, offsets
  (`BRepOffsetAPI_MakeOffset`) and 2D fillets (`BRepFilletAPI_MakeFillet2d`)
  work on faces. A layer is a set of faces in the plane z = 0.
- **Tolerance growth is checked.** OCC can loosen the tolerance of edges and
  vertices after many operations. Tests assert that the tolerance of results
  stays below the 10⁻⁸ µm goal, and a build that exceeds it reports where.
- **Only the OCC modules we need**, linked statically: Foundation, Modeling
  Data, Modeling Algorithms, and the STEP part of Data Exchange. No
  Visualization, application framework (OCAF) or Draw.

### Evaluation

A component's shape tree is evaluated into a **graph of cached nodes**. Each
node has a fingerprint: a hash of its definition, its inputs' fingerprints,
the parameter values it reads and the process constants it uses. The current
compiler already does this per component; the core does it per node, so
editing one fillet does not rebuild its siblings.

**Rigid transforms are applied last.** Booleans, offsets and fillets commute
with moves, rotations and mirrors: offsetting a rotated shape gives the
rotated offset. So:

- Expensive operations run once, in the node's own frame, and are cached.
- Nested placements (a move inside a rotation inside a mirror inside a move)
  compose into **one** transform. It is applied once, with one rounding
  instead of several. In OCC it is a `TopLoc_Location`, which shares the
  geometry rather than copying it.
- mems-sketch has no scaling. If one is ever added, this rule needs revisiting.

**Instances, not copies.** A placed component, and every copy of an array,
mirror or polar array, is a *prototype plus a transform*.

- An array copy may depend on its `i` and `j`. The core reads which
  variables a node's expressions use (expression dependencies are already
  resolved). If the node uses neither, it is built once and instanced.
  Otherwise each distinct set of values is built once: a node that depends
  only on `i` is built once per column, not once per cell.
- Instances stay instances for drawing (one cached triangulation, drawn per
  transform) and for GDS/OASIS export (cells and array references, which also
  gives smaller files).
- Instances are turned into real shapes only when an operation needs their
  outline: a boolean against them, or merging overlapping ones.

**Layers are independent.** Booleans are per layer, and so are rule checks.
`layer_map` is the only kind that moves geometry between layers. It adds an
edge to the dependency graph. Layers are therefore:

- **Built in parallel**, one task per layer (separate OCC shapes on separate
  threads).
- **Rebuilt alone**: an edit that only touches one layer's nodes rebuilds
  only that layer.

Cross-layer work (extrusion through the process stack, enclosure rules) runs
after all layers are built, on their finished results.

**Fast paths before general booleans.** OCC's general boolean is the slow
operation; see [Measurements](#measurements). Before using it, the core tries:

1. **Bounding-box pruning:** only tools whose boxes overlap an argument take
   part in the boolean.
2. **Disjoint cuts:** holes that lie inside a face and do not overlap each
   other, such as release-hole arrays, are added to the face directly as inner
   boundaries, with no boolean. Checking that they don't overlap uses a grid of
   bounding boxes (exact on the boxes; a real boolean only where boxes touch).
3. **Disjoint unions:** shapes on one layer that do not overlap are kept as a
   list. "Several shapes on one layer are one" holds without computing a
   union.

The general boolean, with all tools in one operation and run in parallel, is the
fallback.

### Outputs

Every output chooses its own tolerance. Nothing below changes the cached
geometry.

| Output | How |
|---|---|
| Canvas | Outlines per prototype at a chord tolerance for the zoom level, drawn per instance. Arrays too small to see at the current zoom are drawn as their outline or a pattern. |
| GDS, OASIS, DXF | Curves split at the export's chord tolerance (default 5 nm), snapped to the export's grid (default 1 nm) and merged per layer by KLayout's C++ library. Hierarchy is kept as cells and array references. |
| Snapping report | Exports to a grid report what snapping removed or changed: features collapsed below the grid, gaps opened or closed, widths changed by more than half a grid step. |
| Rule checks | On the geometry as exported (by default the GDS grid), since that is what the fab checks. KLayout's C++ library, per layer, in parallel. |
| STEP, BREP | Layers extruded by their process thickness into solids, exact curves kept. Writing STEP is slow for large designs (see below), so it is an export, never an interactive step. BREP is fast and is what the mesher reads. |
| Mesh | gmsh reads the solids (or the 2D faces). Size settings are project data: global, per layer, and refinement regions or distances drawn on the canvas, all as expressions. Physical groups come from layers and named nodes, so a solver finds anchors and electrodes by name. |
| 3D view | Solids split into triangles (`BRepMesh`), drawn in a Qt widget. |

## Code layout

```
src/
  mems_sketch/          Python, as today; core/ shrinks as parts move to C++
  core/                 C++ sources of mems_sketch._core
    include/mems/       public headers: engine, build, records
    src/
      expr/             expressions and their dependencies
      model/            the project as read from JSON
      eval/             node graph, fingerprints, cache, alignment order
      shapes/           one file per shape kind, as in core/shapes/kinds/
      modifiers/        array, polar_array, mirror, corners
      geom/             OCC wrappers: faces, booleans with the fast paths, offsets, fillets
      out/              outlines, GDS via KLayout, STEP/BREP, rule checks
    bindings/           nanobind module
    tests/              C++ unit tests
```

Shape kinds keep the rule `tests/test_architecture.py` enforces today:
kind-specific behaviour lives in its own file, and nothing else switches on
a kind.

## Build and packaging

- **CMake** for the core, **scikit-build-core** as the Python build backend
  (replacing hatchling), **nanobind** for the bindings.
- **OCC built from source** with only the modules above (CMake options such
  as `BUILD_MODULE_Visualization=OFF`, `BUILD_MODULE_ApplicationFramework=OFF`,
  `BUILD_MODULE_Draw=OFF`), linked statically. KLayout's database library is linked the same way for GDS and
  rule checks.
- **Prebuilt dependencies in CI.** OCC takes a long time to compile, so CI
  builds it once per version and platform and caches it. A contributor runs
  a script that downloads the same build. Nobody compiles OCC to change a
  shape kind.
- **Wheels** for Linux, macOS and Windows with cibuildwheel. `pip install
  mems-sketch` stays the whole installation.
- **Local checks.** The commands in `CONTRIBUTING.md` stay the commands to
  run: `pip install -e ".[dev]"` builds the core incrementally, and `pytest`
  also runs the C++ tests. Keeping that fast matters: several sessions work
  on this repository at once.

## Testing

- **C++ unit tests** per shape kind, modifier and fast path. Each fast path
  is also checked against the general boolean on random inputs.
- **Equivalence with the current backend.** During the migration, every
  example project and every test project is built by both backends, and the
  GDS output must match within 1 nm (XOR of the two, sized by 1 nm, is
  empty). This is the test that lets each step merge.
- **Tolerance:** results keep OCC tolerances below the goal; arcs stay arcs
  through boolean, offset and fillet.
- **Performance tests** with budgets: a 10,000-hole plate, a 1,000-finger
  comb and the examples, rebuilt after a parameter change. They join
  `tests/test_gui_canvas_performance.py`.

## Migration

Each step is its own pull request into `development`, keeps every existing
test passing and updates this document.

1. **Interface first, in Python.** Put the `Engine`/`build` interface in front
   of the current compiler, and move the GUI, editing and exports onto it, so
   nothing outside the backend uses KLayout types (today `gui/canvas.py`,
   `gui/tools.py`, `editing/results.py` and others do).
2. **Build setup.** CMake, scikit-build-core, nanobind, the OCC build and CI
   caching, with an empty core that only reports its version.
3. **Expressions and the model in C++,** tested against
   `core/expressions.py` on every expression in the tests and examples.
4. **Primitives and placements:** `rect`, `polygon`, `circle`, `arc`, `path`,
   `ref`, `transform`, instancing and composed transforms. The equivalence test
   runs from here on.
5. **Operations:** `boolean`, `offset`, `fillet`, `layer_map`, the modifiers,
   and the fast paths.
6. **Outputs:** GDS/OASIS/DXF, rule checks, then switch the default backend
   to the core. The Python geometry code is removed once the core has been
   the default for one release.
7. **New capabilities:** STEP/BREP, the 3D view, meshing with gmsh.

## Open questions

- **Corner editing.** `editing/corners.py` finds corners among the vertices
  of merged polygons. With exact curves a "corner" is an OCC vertex where two
  edges meet at an angle. The command stays, and its matching needs
  redesigning.
- **Imported layouts** are polygons and stay polygons. Should the core
  recognise arcs in them (e.g. from DXF), or leave them as they are?
- **gmsh's licence (GPL).** Meshing should be an optional extra or a separate
  plugin, so the rest of mems-sketch is not affected.
- **Python version support:** a compiled core means wheels per Python
  version (3.11 and 3.12 today).

## Measurements

The decision for OCC is based on a benchmark (October 2026; OCC 8.0.1 through
`cadquery-ocp` 8.0.1; 4 cores; OCC in nm, KLayout at 1 nm). The plates have
4 µm holes on a 10 µm pitch.

| Case | KLayout | OCC, one general boolean | OCC, built directly (disjoint cut) |
|---|---|---|---|
| 1,000 square holes | 0.006 s | 0.5 s | — |
| 5,000 square holes | 0.05 s | 4.3 s | 0.01 s* |
| 10,000 square holes | 0.09 s | 15.4 s | 0.14 s* |
| 10,000 circular holes (KLayout: 128-sided polygons) | 1.3 s | 3.3 s, exact circles | — |
| Comb, 1,000 fingers, fused and merged into one face | — | 1.3 s | — |

\* Excluding the time Python spends creating the hole shapes one by one (0.66 s
and 1.45 s), which the C++ core does not have.

- Running the boolean in parallel, oriented bounding boxes and splitting into
  16 tiles did **not** make the general boolean noticeably faster. Hence the
  fast paths.
- Extruding the 10,000-hole plate into a solid took 0.3 s.
- Writing the 5,000-hole plate: 0.2 s as BREP, 41 s as STEP (161 s for 10,000
  holes). STEP is an export only.
- All results were valid single faces, and circles stayed exact circles.

The alternative considered was Clipper2 (64-bit integer booleans and offsets,
about as fast as KLayout) with arcs recovered from tagged vertices. It is
faster, but exact curves, tangency cases (fillets are tangent by
construction) and 3D would all be ours to build and maintain. It stays the
fallback if real designs are too slow on OCC even with the fast paths.
